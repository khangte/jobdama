"""노션 저장소: 같은 URL 조회, 기업 조회·생성, 공고 페이지 생성.

notion-client 3.x (API 2025-09-03, data source 기준)를 사용한다.
POST 요청은 클라이언트가 429일 때만 자동 재시도하므로, 응답이 끊긴 생성 요청은
service 쪽에서 '다시 조회 → 없으면 1회 재시도'로 처리한다.
"""

from __future__ import annotations

import asyncio
import logging
import re

from notion_client import AsyncClient
from notion_client.errors import HTTPResponseError
from notion_client.helpers import async_iterate_paginated_api

from app.config import Settings
from app.models import NormalizedURL, Posting
from app.notion.blocks import build_children, prop_value
from app.notion.errors import NotionError, describe_error, is_schema_error
from app.notion.pages import normalize_title
from app.notion.schema import COMPANY_ROLES, JOB_ROLES, Found, Role, pick_option, resolve_props
from app.parsers.common import normalize_company

log = logging.getLogger(__name__)

STAGE_DEFAULT = "📌 시작 전"  # 옵션에 있으면 우선, 없으면 첫 옵션
COMPANY_STATUS_DEFAULT = "⚪️ 리서치 중"

_ID_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}(?![0-9a-f])")


def extract_notion_id(link: str) -> str:
    """노션 링크나 ID에서 32자리 ID를 꺼낸다. 링크 뒤 ?v= 보기 ID는 무시한다."""
    ids = _ID_RE.findall(link.strip().split("?")[0].split("#")[0].lower())
    if not ids:
        raise NotionError("노션 링크에서 ID를 찾지 못함")
    return ids[-1].replace("-", "")


class NotionStore:
    """노션 DB를 읽고 쓰는 저장소."""
    def __init__(self, settings: Settings, client: AsyncClient | None = None):
        """설정과 노션 클라이언트를 받아 저장소를 만든다."""
        self.s = settings
        self.client = client or AsyncClient(auth=settings.notion_token, timeout_ms=30_000)
        self._company_locks: dict[str, asyncio.Lock] = {}
        # 아래 값은 check_schema가 스키마를 읽어 채운다
        self.company_source_id = ""  # 기업 열이 relation일 때 그 relation이 가리키는 관심 기업 리스트 DB
        self.job_props: Found = {}
        self.company_props: Found = {}
        self.warnings: list[str] = []  # 정하지 못해 비워 두는 열

    # ------------------------------------------------------------ 점검
    async def check_schema(self) -> list[str]:
        """DB 스키마에서 값을 넣을 열을 정한다. 저장을 막는 문제 목록을 돌려준다(없으면 빈 목록).
        기업 열이 relation이면 그것이 가리키는 관심 기업 리스트 DB도 점검한다."""
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
        name = "채용 지원 관리 DB"
        jobs, problems, warnings = await self._check_source(s.job_data_source_id, JOB_ROLES, name)
        company_id, companies = "", {}
        company = jobs.get("company")
        if company and company[1]["type"] == "relation":
            company_id = (company[1].get("relation") or {}).get("data_source_id", "")
            if not company_id:
                problems.append(f"{name}: '{company[0]}' relation이 가리키는 관심 기업 리스트 DB를 찾지 못함")
            else:
                companies, more, more_warnings = await self._check_source(company_id, COMPANY_ROLES, "관심 기업 리스트 DB")
                problems += more
                warnings += more_warnings
        if problems:
            return problems
        # 점검 중에도 저장 작업이 이 값을 쓰므로 다 정한 뒤 한 번에 바꾼다
        self.job_props, self.company_props, self.company_source_id, self.warnings = jobs, companies, company_id, warnings
        return []

    async def _check_source(self, ds_id: str, roles: dict[str, Role], name: str) -> tuple[Found, list[str], list[str]]:
        """data source 하나의 스키마를 읽어 열을 정한다. (찾은 열, 문제, 경고)"""
        try:
            ds = await self.client.data_sources.retrieve(data_source_id=ds_id)
        except Exception as e:
            return {}, [f"{name} 접근 실패 ({describe_error(e)})"], []
        found, problems, warnings = resolve_props(ds.get("properties", {}), roles)
        return found, [f"{name}: {p}" for p in problems], [f"{name}: {w}" for w in warnings]

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

    async def _retry_on_schema_change(self, call):
        """연결한 뒤 노션에서 열을 바꿔 요청이 거부되면 스키마를 다시 읽고 한 번만 다시 보낸다.
        다시 읽은 스키마에 문제가 있으면(필수 열 없음) 보내지 않고 그 문제로 실패한다."""
        try:
            return await call()
        except Exception as e:
            if not is_schema_error(e):
                raise
            log.warning("노션이 요청을 거부해 스키마를 다시 읽음: %s", e)
        if problems := await self.check_schema():
            raise NotionError(" / ".join(problems))
        return await call()

    # ------------------------------------------------------------ 공고
    async def find_by_url(self, target: NormalizedURL) -> dict | None:
        """같은 공고 URL이 이미 있는지. 예전에 다른 형식(추적 파라미터 포함 등)으로 저장한 URL도 함께 찾는다."""
        return await self._retry_on_schema_change(lambda: self._find_by_url(target))

    async def _find_by_url(self, target: NormalizedURL) -> dict | None:
        """같은 공고 URL 페이지를 조회한다(find_by_url의 본체)."""
        prop, info = self.job_props["url"]
        kind = info["type"]  # url과 rich_text 필터는 같은 조건(equals, ends_with, contains)을 받는다
        conditions = [{"property": prop, kind: {"equals": target.url}}]
        conditions += [{"property": prop, kind: {op: value}} for op, value in target.match]
        filt = {"or": conditions} if len(conditions) > 1 else conditions[0]
        res = await self.client.data_sources.query(
            data_source_id=self.s.job_data_source_id,
            filter=filt,
            sorts=[{"timestamp": "created_time", "direction": "descending"}],
            page_size=5,
        )
        results = res.get("results", [])
        return results[0] if results else None

    async def find_by_title(self, p: Posting, company_page_id: str | None) -> dict | None:
        """같은 기업의 공고 중 정규화한 공고명이 같은 첫 페이지. 다른 사이트·다른 URL로 올라온 같은 공고를 찾는다."""
        return await self._retry_on_schema_change(lambda: self._find_by_title(p, company_page_id))

    async def _find_by_title(self, p: Posting, company_page_id: str | None) -> dict | None:
        """같은 기업 공고 중 정규화한 공고명이 같은 페이지를 찾는다(find_by_title의 본체)."""
        # shortcut: 같은 기업 공고를 전부 읽는다. 기업당 수백 건이 되면 정규화 제목 속성을 두고 equals로 검색한다.
        col, info = self.job_props["company"]
        kind = info["type"]
        if kind == "relation":
            if not company_page_id:
                return None
            filt = {"property": col, "relation": {"contains": company_page_id}}
        else:
            if not p.company:
                return None
            filt = {"property": col, kind: {"equals": p.company}}
        want = normalize_title(p.title, p.company)
        if not want:  # "[채용공고]"처럼 비교할 글자가 없으면 아무 공고와도 같다고 보지 않는다
            return None
        title_col = self.job_props["title"][0]
        pages = async_iterate_paginated_api(
            self.client.data_sources.query, data_source_id=self.s.job_data_source_id, filter=filt, page_size=100)
        async for page in pages:
            parts = page.get("properties", {}).get(title_col, {}).get("title", [])
            if normalize_title("".join(t.get("plain_text", "") for t in parts), p.company) == want:
                return page
        return None

    async def create_posting(self, p: Posting, company_page_id: str | None) -> dict:
        """공고 페이지를 만든다."""
        return await self._retry_on_schema_change(lambda: self._create_posting(p, company_page_id))

    async def _create_posting(self, p: Posting, company_page_id: str | None) -> dict:
        """스키마에 맞는 속성으로 공고 페이지를 만든다(create_posting의 본체)."""
        found = self.job_props
        types = {role: info["type"] for role, (_, info) in found.items()}
        deadline = p.deadline.isoformat() if p.deadline else None
        values = {
            "title": p.title,
            "company": company_page_id if types.get("company") == "relation" else p.company,
            "url": p.url,
            "deadline": deadline or (p.deadline_raw if types.get("deadline") == "rich_text" else None),
            "career": p.career_select,
            "stage": pick_option(found["stage"][1], STAGE_DEFAULT) if "stage" in found else None,
        }
        props = {found[role][0]: prop_value(types[role], v) for role, v in values.items() if v and role in found}
        return await self.client.pages.create(
            parent={"type": "data_source_id", "data_source_id": self.s.job_data_source_id},
            properties=props,
            children=build_children(p),
        )

    # ------------------------------------------------------------ 기업
    async def find_or_create_company(self, name: str) -> str | None:
        """관심 기업 리스트 DB의 기업 페이지 ID. 기업 열이 relation이 아니면(기업명을 글자로 넣음) None."""
        if not self.company_source_id:
            return None
        key = normalize_company(name)
        if not key:
            raise NotionError("기업명이 비어 있음")
        lock = self._company_locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._retry_on_schema_change(lambda: self._find_or_create_company(name, key))

    async def find_company(self, name: str) -> str | None:
        """기업 페이지 ID를 읽기만 한다(없어도 만들지 않는다). 기업 열이 relation이 아니거나 이름이 비면 None."""
        key = normalize_company(name)
        if not self.company_source_id or not key:
            return None
        return await self._retry_on_schema_change(lambda: self._find_company_if_linked(key))

    async def _find_company_if_linked(self, key: str) -> str | None:
        """기업 DB에서 기업명이 같은 페이지 ID를 찾는다(find_company의 본체). 기업 열이 relation이 아니면 None."""
        if not self.company_source_id:  # 다시 읽은 스키마에서 기업 열이 relation이 아니게 됐을 수 있다
            return None
        return await self._find_company(key)

    async def _find_or_create_company(self, name: str, key: str) -> str | None:
        """기업 페이지를 찾고 없으면 만든다(find_or_create_company의 본체)."""
        if not self.company_source_id:  # 다시 읽은 스키마에서 기업 열이 relation이 아니게 됐을 수 있다
            return None
        found = await self._find_company(key)
        if found:
            return found
        props = {self.company_props["title"][0]: prop_value("title", name)}
        status = self.company_props.get("status")
        if status and (option := pick_option(status[1], COMPANY_STATUS_DEFAULT)):
            props[status[0]] = prop_value(status[1]["type"], option)
        page = await self.client.pages.create(
            parent={"type": "data_source_id", "data_source_id": self.company_source_id},
            properties=props,
        )
        return page["id"]

    async def _find_company(self, key: str) -> str | None:
        """기업 DB 전체에서 정규화한 기업명이 같은 페이지 ID를 찾는다."""
        # 제목 contains 검색은 띄어쓰기 차이("SK 텔레콤"/"SK텔레콤")를 놓치므로 기업 DB 전체를 받아 정규화해 비교한다.
        # shortcut: 저장할 때마다 기업 DB 전체를 읽는다. 기업이 수천 개로 늘면 정규화 키 속성을 두고 equals로 검색한다.
        pages = async_iterate_paginated_api(
            self.client.data_sources.query,
            data_source_id=self.company_source_id,
            sorts=[{"timestamp": "created_time", "direction": "ascending"}],
            page_size=100,
        )
        async for page in pages:
            title_prop = page.get("properties", {}).get(self.company_props["title"][0], {})
            title = "".join(t.get("plain_text", "") for t in title_prop.get("title", []))
            if normalize_company(title) == key:
                return page["id"]
        return None

    async def aclose(self) -> None:
        """노션 클라이언트를 닫는다."""
        await self.client.aclose()
