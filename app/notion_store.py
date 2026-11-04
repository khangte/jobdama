"""노션 저장소: 같은 URL 조회, 기업 조회·생성, 공고 페이지 생성.

notion-client 3.x (API 2025-09-03, data source 기준)를 사용한다.
POST 요청은 클라이언트가 429일 때만 자동 재시도하므로, 응답이 끊긴 생성 요청은
service 쪽에서 '다시 조회 → 없으면 1회 재시도'로 처리한다.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

import httpx
from notion_client import AsyncClient
from notion_client.errors import HTTPResponseError, RequestTimeoutError
from notion_client.helpers import async_iterate_paginated_api

from app.config import Settings
from app.models import KST, SECTION_KEYS, Posting
from app.parsers.common import normalize_company
from app.collection.urls import NormalizedURL

log = logging.getLogger(__name__)

NOT_FOUND_TEXT = "공고에서 확인 불가"
MAX_TEXT = 1900  # 노션 rich_text 한 덩어리 한도(2000자) 여유


class NotionError(Exception):
    pass


def is_uncertain_write_error(e: Exception) -> bool:
    """요청이 서버에 반영됐는지 알 수 없는 오류(시간 초과, 연결 끊김, 5xx)."""
    if isinstance(e, (RequestTimeoutError, httpx.TransportError)):
        return True
    if isinstance(e, HTTPResponseError):
        return (getattr(e, "status", 0) or 0) >= 500
    return False


_ID_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}(?![0-9a-f])")


def extract_notion_id(link: str) -> str:
    """노션 링크나 ID에서 32자리 ID를 꺼낸다. 링크 뒤 ?v= 보기 ID는 무시한다."""
    ids = _ID_RE.findall(link.strip().split("?")[0].split("#")[0].lower())
    if not ids:
        raise NotionError("노션 링크에서 ID를 찾지 못함")
    return ids[-1].replace("-", "")


def describe_error(e: Exception) -> str:
    """원인을 짐작할 수 있게 HTTP 상태와 코드를 적는다. 401과 404가 같은 예외 클래스라 이름만으로는 구분이 안 된다."""
    if isinstance(e, HTTPResponseError):
        hint = {401: "토큰 확인", 403: "권한 확인", 404: "integration 연결과 링크·ID 확인"}.get(e.status)
        code = getattr(e.code, "value", e.code)  # 알려진 코드는 APIErrorCode enum으로 온다
        return f"HTTP {e.status} {code}" + (f": {hint}" if hint else "")
    return type(e).__name__


def page_url(page: dict) -> str:
    return page.get("url") or f"https://www.notion.so/{page['id'].replace('-', '')}"


def _rt(text: str, link: str | None = None, bold: bool = False) -> dict:
    item: dict = {"type": "text", "text": {"content": text[:MAX_TEXT]}}
    if link:
        item["text"]["link"] = {"url": link}
    if bold:
        item["annotations"] = {"bold": True}
    return item


def _heading(text: str) -> dict:
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [_rt(text)]}}


def _bullet(text: str, link: str | None = None) -> dict:
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [_rt(text, link)]}}


def _labeled(label: str, value: str | None) -> dict:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [_rt(f"{label}: ", bold=True), _rt(value or NOT_FOUND_TEXT)]},
    }


def build_children(p: Posting, collected_at: datetime | None = None) -> list[dict]:
    collected_at = collected_at or datetime.now(KST)
    blocks: list[dict] = []
    if not p.body_found:
        blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [_rt("상세 페이지 미확인 - 원문 링크에서 직접 확인 필요")],
                "icon": {"type": "emoji", "emoji": "⚠️"},
            },
        })

    if p.deadline:
        deadline = p.deadline.isoformat()
    elif p.deadline_raw:
        deadline = p.deadline_raw
    else:
        deadline = None
    if deadline and p.expired:
        deadline += " (마감된 공고)"

    blocks.append(_heading("공고 요약"))
    blocks += [
        _labeled("기업", p.company),
        _labeled("출처", p.source),
        _labeled("근무지", p.location),
        _labeled("경력 조건", p.career_raw),
        _labeled("고용 형태", p.employment_type),
        _labeled("마감일", deadline),
        _labeled("수집일", collected_at.date().isoformat()),
    ]
    for key in SECTION_KEYS:
        blocks.append(_heading(key))
        items = p.sections.get(key) or []
        blocks += [_bullet(i) for i in items] if items else [_bullet(NOT_FOUND_TEXT)]
    blocks.append(_heading("원문 링크"))
    blocks.append(_bullet(p.url, link=p.url))
    return blocks


class NotionStore:
    def __init__(self, settings: Settings, client: AsyncClient | None = None):
        self.s = settings
        self.client = client or AsyncClient(auth=settings.notion_token, timeout_ms=30_000)
        self._company_locks: dict[str, asyncio.Lock] = {}
        self.company_source_id = ""  # check_schema가 채용 DB의 relation에서 채운다

    # ------------------------------------------------------------ 점검
    async def check_schema(self) -> list[str]:
        """DB 속성 이름·형식 점검. 문제 목록을 돌려준다(없으면 빈 목록).
        관심 기업 리스트 DB는 채용 DB의 기업명 relation이 가리키는 곳으로 정한다(company_source_id)."""
        s = self.s
        if not s.notion_token:
            return ["NOTION_TOKEN이 설정되지 않음 (.env 확인)"]
        if not s.job_data_source_id:
            return ["JOB_DATA_SOURCE_ID가 설정되지 않음 (.env 확인)"]
        # .env에 DB 링크나 database ID를 적어도 쓸 수 있게 data source ID로 바꿔 둔다
        try:
            s.job_data_source_id = await self.resolve_data_source_id(s.job_data_source_id)
        except NotionError as e:
            return [f"채용 지원 관리 DB: {e}"]
        except Exception as e:
            return [f"채용 지원 관리 DB 접근 실패 ({describe_error(e)})"]
        expected_jobs = {
            s.prop_title: "title", s.prop_company: "relation", s.prop_deadline: "date",
            s.prop_career: "select", s.prop_url: "url", s.prop_stage: "status",
        }
        expected_company = {s.company_prop_title: "title", s.company_prop_status: "select"}
        props, problems = await self._check_source(s.job_data_source_id, expected_jobs, "채용 지원 관리 DB")
        if props is None:
            return problems
        company_id = (props.get(s.prop_company, {}).get("relation") or {}).get("data_source_id", "")
        if not company_id:
            if not problems:  # 형식이 relation이 아니면 위에서 이미 알렸다
                problems.append(f"채용 지원 관리 DB: '{s.prop_company}' relation이 가리키는 관심 기업 리스트 DB를 찾지 못함")
            return problems
        # 점검 중에도 저장 작업이 이 값을 쓰므로 비우지 않고 찾았을 때만 바꾼다
        self.company_source_id = company_id
        return problems + (await self._check_source(company_id, expected_company, "관심 기업 리스트 DB"))[1]

    async def _check_source(self, ds_id: str, expected: dict[str, str], name: str) -> tuple[dict | None, list[str]]:
        try:
            ds = await self.client.data_sources.retrieve(data_source_id=ds_id)
        except Exception as e:
            return None, [f"{name} 접근 실패 ({describe_error(e)})"]
        props = ds.get("properties", {})
        problems = [
            f"{name}: '{prop}' 속성이 없거나 형식이 다름 (기대 {typ}, 실제 {props.get(prop, {}).get('type')})"
            for prop, typ in expected.items() if props.get(prop, {}).get("type") != typ
        ]
        return props, problems

    async def resolve_data_source_id(self, link: str) -> str:
        """링크가 data source면 그 ID, database면 그 안의 유일한 data source ID를 돌려준다."""
        nid = extract_notion_id(link)
        try:
            await self.client.data_sources.retrieve(data_source_id=nid)
            return nid
        except HTTPResponseError as e:
            if e.status not in (400, 404):  # 토큰·권한·서버 오류는 database로 다시 물어도 같다
                raise
        db = await self.client.databases.retrieve(database_id=nid)
        sources = db.get("data_sources") or []
        if not sources:
            raise NotionError("database에 data source가 없음")
        if len(sources) > 1:
            raise NotionError("data source가 여러 개라 고를 수 없음, data source ID를 직접 입력")
        return sources[0]["id"]

    # ------------------------------------------------------------ 공고
    async def find_by_url(self, target: NormalizedURL) -> dict | None:
        """같은 공고 URL이 이미 있는지. 예전에 다른 형식(추적 파라미터 포함 등)으로 저장한 URL도 함께 찾는다."""
        prop = self.s.prop_url
        conditions = [{"property": prop, "url": {"equals": target.url}}]
        conditions += [{"property": prop, "url": {op: value}} for op, value in target.match]
        filt = {"or": conditions} if len(conditions) > 1 else conditions[0]
        res = await self.client.data_sources.query(
            data_source_id=self.s.job_data_source_id,
            filter=filt,
            sorts=[{"timestamp": "created_time", "direction": "descending"}],
            page_size=5,
        )
        results = res.get("results", [])
        return results[0] if results else None

    async def create_posting(self, p: Posting, company_page_id: str | None) -> dict:
        s = self.s
        props: dict = {
            s.prop_title: {"title": [_rt(p.title)]},
            s.prop_url: {"url": p.url},
            s.prop_stage: {"status": {"name": s.stage_default}},
        }
        if company_page_id:
            props[s.prop_company] = {"relation": [{"id": company_page_id}]}
        if p.deadline:
            props[s.prop_deadline] = {"date": {"start": p.deadline.isoformat()}}
        if p.career_select:
            props[s.prop_career] = {"select": {"name": p.career_select}}
        return await self.client.pages.create(
            parent={"type": "data_source_id", "data_source_id": s.job_data_source_id},
            properties=props,
            children=build_children(p),
        )

    # ------------------------------------------------------------ 기업
    async def find_or_create_company(self, name: str) -> str:
        key = normalize_company(name)
        if not key:
            raise NotionError("기업명이 비어 있음")
        lock = self._company_locks.setdefault(key, asyncio.Lock())
        async with lock:
            found = await self._find_company(key)
            if found:
                return found
            page = await self.client.pages.create(
                parent={"type": "data_source_id", "data_source_id": self.company_source_id},
                properties={
                    self.s.company_prop_title: {"title": [_rt(name)]},
                    self.s.company_prop_status: {"select": {"name": self.s.company_status_default}},
                },
            )
            return page["id"]

    async def _find_company(self, key: str) -> str | None:
        # 제목 contains 검색은 띄어쓰기 차이("SK 텔레콤"/"SK텔레콤")를 놓치므로 기업 DB 전체를 받아 정규화해 비교한다.
        # shortcut: 저장할 때마다 기업 DB 전체를 읽는다. 기업이 수천 개로 늘면 정규화 키 속성을 두고 equals로 검색한다.
        pages = async_iterate_paginated_api(
            self.client.data_sources.query,
            data_source_id=self.company_source_id,
            sorts=[{"timestamp": "created_time", "direction": "ascending"}],
            page_size=100,
        )
        async for page in pages:
            title_prop = page.get("properties", {}).get(self.s.company_prop_title, {})
            title = "".join(t.get("plain_text", "") for t in title_prop.get("title", []))
            if normalize_company(title) == key:
                return page["id"]
        return None

    async def aclose(self) -> None:
        await self.client.aclose()
