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
from typing import NamedTuple

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
STAGE_DEFAULT = "📌 시작 전"  # 옵션에 있으면 우선, 없으면 첫 옵션
COMPANY_STATUS_DEFAULT = "⚪️ 리서치 중"


class Role(NamedTuple):
    """노션 DB에서 값을 넣을 열을 찾는 규칙. 스키마에는 이름·형식·옵션만 있어 용도는 이름으로 짐작한다."""
    label: str
    types: tuple[str, ...]  # 받을 수 있는 노션 형식, 앞일수록 우선
    keywords: tuple[str, ...] = ()  # 이름(소문자, 공백 제거)에 들어 있으면 후보
    required: bool = False  # 못 찾으면 저장을 막는다. 아니면 비우고 경고만
    by_type: bool = True  # 첫 형식인 열이 하나뿐이면 이름을 보지 않고 쓴다


JOB_ROLES = {
    "title": Role("공고명", ("title",), required=True),
    "company": Role("기업", ("relation", "rich_text", "select"), ("기업", "회사", "company"), required=True),
    "url": Role("URL", ("url", "rich_text"), ("url", "링크", "link"), required=True),
    # 날짜·선택 열은 여러 용도로 쓰여(지원일, 직무 등) 하나뿐이어도 이름이 맞아야 쓴다
    "deadline": Role("마감일", ("date", "rich_text"), ("마감", "deadline", "due"), by_type=False),
    "career": Role("경력", ("select", "multi_select", "rich_text"), ("경력", "career", "연차", "experience"), by_type=False),
    "stage": Role("지원 단계", ("status", "select"), ("단계", "상태", "stage", "status")),
}
COMPANY_ROLES = {
    "title": Role("기업명", ("title",), required=True),
    "status": Role("상태", ("status", "select"), ("상태", "단계", "status", "stage"), by_type=False),
}
TYPE_NAMES = {"title": "제목", "rich_text": "텍스트", "url": "URL", "date": "날짜", "select": "선택",
              "multi_select": "다중 선택", "status": "상태", "relation": "관계형"}

Found = dict[str, tuple[str, dict]]  # 역할 -> (열 이름, 스키마의 속성 정보)


def _pick(props: dict, role: Role) -> tuple[tuple[str, dict] | None, str]:
    """역할에 맞는 열 하나와, 못 찾았으면 그 이유."""
    cands = [(n, p) for n, p in props.items() if p.get("type") in role.types]
    if role.by_type:
        first = [c for c in cands if c[1]["type"] == role.types[0]]
        if len(first) == 1:
            return first[0], ""
    hits = [c for c in cands if any(k in c[0].lower().replace(" ", "") for k in role.keywords)]
    for t in role.types:  # 이름이 맞는 열이 여러 개면 앞선 형식만 남긴다
        if same := [c for c in hits if c[1]["type"] == t]:
            hits = same
            break
    if len(hits) == 1:
        return hits[0], ""
    if hits:
        return None, f"후보가 여러 개({', '.join(n for n, _ in hits)})"
    types = "/".join(TYPE_NAMES[t] for t in role.types)
    if role.keywords:
        return None, f"이름에 {'·'.join(role.keywords)} 중 하나가 들어간 {types} 열이 없음"
    return None, f"{types} 열이 없음"


def resolve_props(props: dict, roles: dict[str, Role]) -> tuple[Found, list[str], list[str]]:
    """스키마의 열을 역할에 짝짓는다. (찾은 열, 저장을 막는 문제, 비워 두는 경고)"""
    found: Found = {}
    problems: list[str] = []
    warnings: list[str] = []
    for key, role in roles.items():
        hit, why = _pick({n: p for n, p in props.items() if n not in {f[0] for f in found.values()}}, role)
        if hit:
            found[key] = hit
        elif role.required:
            problems.append(f"{role.label} 열을 정하지 못함: {why}")
        else:
            warnings.append(f"{role.label} 열을 정하지 못해 비워 둠: {why}")
    return found, problems, warnings


def _pick_option(prop: dict, preferred: str) -> str | None:
    """선택·상태 열에 넣을 옵션. 원하는 이름이 없으면 상태는 첫 그룹(할 일)의 첫 옵션, 선택은 첫 옵션."""
    conf = prop.get(prop["type"]) or {}
    names = {o.get("id"): o["name"] for o in conf.get("options", [])}
    if preferred in names.values():
        return preferred
    for group in conf.get("groups", []):
        for oid in group.get("option_ids", []):
            if oid in names:
                return names[oid]
    return next(iter(names.values()), None)


def _prop_value(ptype: str, value: str) -> dict:
    if ptype in ("select", "multi_select"):
        value = value.replace(",", " ")  # 선택 옵션 이름에는 쉼표를 쓸 수 없다
    return {
        "title": lambda: {"title": [_rt(value)]},
        "rich_text": lambda: {"rich_text": [_rt(value)]},
        "url": lambda: {"url": value},
        "date": lambda: {"date": {"start": value}},
        "select": lambda: {"select": {"name": value}},
        "multi_select": lambda: {"multi_select": [{"name": value}]},
        "status": lambda: {"status": {"name": value}},
        "relation": lambda: {"relation": [{"id": value}]},
    }[ptype]()


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


def is_schema_error(e: Exception) -> bool:
    """열 이름·형식이 맞지 않아 노션이 요청을 거부한 오류. 거부된 요청은 반영되지 않는다."""
    return isinstance(e, HTTPResponseError) and e.status == 400 and getattr(e.code, "value", e.code) == "validation_error"


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

    async def create_posting(self, p: Posting, company_page_id: str | None) -> dict:
        return await self._retry_on_schema_change(lambda: self._create_posting(p, company_page_id))

    async def _create_posting(self, p: Posting, company_page_id: str | None) -> dict:
        found = self.job_props
        types = {role: info["type"] for role, (_, info) in found.items()}
        deadline = p.deadline.isoformat() if p.deadline else None
        values = {
            "title": p.title,
            "company": company_page_id if types.get("company") == "relation" else p.company,
            "url": p.url,
            "deadline": deadline or (p.deadline_raw if types.get("deadline") == "rich_text" else None),
            "career": p.career_select,
            "stage": _pick_option(found["stage"][1], STAGE_DEFAULT) if "stage" in found else None,
        }
        props = {found[role][0]: _prop_value(types[role], v) for role, v in values.items() if v and role in found}
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

    async def _find_or_create_company(self, name: str, key: str) -> str | None:
        if not self.company_source_id:  # 다시 읽은 스키마에서 기업 열이 relation이 아니게 됐을 수 있다
            return None
        found = await self._find_company(key)
        if found:
            return found
        props = {self.company_props["title"][0]: _prop_value("title", name)}
        status = self.company_props.get("status")
        if status and (option := _pick_option(status[1], COMPANY_STATUS_DEFAULT)):
            props[status[0]] = _prop_value(status[1]["type"], option)
        page = await self.client.pages.create(
            parent={"type": "data_source_id", "data_source_id": self.company_source_id},
            properties=props,
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
            title_prop = page.get("properties", {}).get(self.company_props["title"][0], {})
            title = "".join(t.get("plain_text", "") for t in title_prop.get("title", []))
            if normalize_company(title) == key:
                return page["id"]
        return None

    async def aclose(self) -> None:
        await self.client.aclose()
