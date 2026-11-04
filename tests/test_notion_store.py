"""노션 API 요청 형식 검증. httpx MockTransport로 실제 notion-client가 보내는 요청을 가로챈다."""

import json

import httpx
import pytest
from notion_client import AsyncClient

from app.config import Settings
from app.notion_store import NotionError, NotionStore, build_children, extract_notion_id
from app.parsers.sites import saramin
from app.collection.urls import normalize
from tests.helpers import load, parse_saramin

JOB_DS = "1" * 32  # check_schema가 링크처럼 ID를 해석하므로 실제 형식
JOB_DB = "2" * 32
COMPANY_DS = "company-ds"
RELATION = {"type": "relation", "relation": {"data_source_id": COMPANY_DS, "type": "dual_property"}}


def settings() -> Settings:
    return Settings(notion_token="t", job_data_source_id=JOB_DS, _env_file=None)


def make_store(handler):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncClient(auth="t", client=http)
    store = NotionStore(settings(), client)
    store.company_source_id = COMPANY_DS  # 원래는 check_schema가 relation에서 채운다
    return store


def company_page(pid: str, title: str) -> dict:
    return {"object": "page", "id": pid, "url": f"https://www.notion.so/{pid}",
            "properties": {"기업명": {"type": "title", "title": [{"plain_text": title}]}}}


async def test_find_by_url_filter():
    seen = {}

    def handler(req: httpx.Request):
        seen["path"] = req.url.path
        seen["body"] = json.loads(req.content)
        seen["version"] = req.headers.get("Notion-Version")
        return httpx.Response(200, json={"object": "list", "results": [], "has_more": False})

    store = make_store(handler)
    assert await store.find_by_url(normalize("https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=5")) is None
    assert seen["path"] == f"/v1/data_sources/{JOB_DS}/query"
    assert seen["version"] == "2025-09-03"
    ors = seen["body"]["filter"]["or"]
    assert {"property": "URL", "url": {"equals": "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=5"}} in ors
    assert {"property": "URL", "url": {"ends_with": "rec_idx=5"}} in ors


async def test_company_reuse_by_normalized_name():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path))
        if req.url.path.endswith("/query"):
            return httpx.Response(200, json={"object": "list", "has_more": False, "results": [
                company_page("c-other", "디비투이랩"), company_page("c-1", "디비투이(주)")]})
        raise AssertionError("기업이 있으면 생성하면 안 됨")

    store = make_store(handler)
    assert await store.find_or_create_company("(주)디비투이") == "c-1"
    assert calls == [("POST", f"/v1/data_sources/{COMPANY_DS}/query")]


async def test_company_reuse_ignores_spacing_and_reads_all_pages():
    bodies = []

    def handler(req: httpx.Request):
        if not req.url.path.endswith("/query"):
            raise AssertionError("기업이 있으면 생성하면 안 됨")
        body = json.loads(req.content)
        bodies.append(body)
        if "start_cursor" not in body:
            return httpx.Response(200, json={"object": "list", "has_more": True, "next_cursor": "c2",
                                             "results": [company_page("c-kakao", "카카오")]})
        return httpx.Response(200, json={"object": "list", "has_more": False, "next_cursor": None,
                                         "results": [company_page("c-sk", "SK텔레콤")]})

    store = make_store(handler)
    assert await store.find_or_create_company("SK 텔레콤") == "c-sk"
    assert "filter" not in bodies[0]
    assert bodies[1]["start_cursor"] == "c2"


async def test_company_created_when_missing():
    created = {}

    def handler(req: httpx.Request):
        if req.url.path.endswith("/query"):
            return httpx.Response(200, json={"object": "list", "results": [], "has_more": False})
        created.update(json.loads(req.content))
        return httpx.Response(200, json={"object": "page", "id": "new-c", "url": "https://www.notion.so/newc"})

    store = make_store(handler)
    assert await store.find_or_create_company("(주)로브로스") == "new-c"
    assert created["parent"] == {"type": "data_source_id", "data_source_id": COMPANY_DS}
    assert created["properties"]["기업명"]["title"][0]["text"]["content"] == "(주)로브로스"
    assert created["properties"]["상태"] == {"select": {"name": "⚪️ 리서치 중"}}
    assert "산업군" not in created["properties"]


async def test_create_posting_payload():
    body = {}

    def handler(req: httpx.Request):
        body.update(json.loads(req.content))
        return httpx.Response(200, json={"object": "page", "id": "p-1", "url": "https://www.notion.so/p1"})

    html = load("saramin_55434315.html")
    posting = saramin.parse(html, "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=55434315", "55434315")
    store = make_store(handler)
    page = await store.create_posting(posting, "c-1")
    assert page["id"] == "p-1"
    props = body["properties"]
    assert body["parent"] == {"type": "data_source_id", "data_source_id": JOB_DS}
    assert props["공고명"]["title"][0]["text"]["content"] == "[(주)로브로스] Robotics Data Solution Engineer"
    assert props["기업명"] == {"relation": [{"id": "c-1"}]}
    assert props["마감일"] == {"date": {"start": "2026-12-01"}}
    assert props["경력"] == {"select": {"name": "신입/경력"}}
    assert props["URL"] == {"url": "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=55434315"}
    assert props["지원 단계"] == {"status": {"name": "📌 시작 전"}}
    assert "직무" not in props
    assert 0 < len(body["children"]) <= 100


async def test_schema_check_reports_missing_property():
    def handler(req: httpx.Request):
        if JOB_DS in req.url.path:
            return httpx.Response(200, json={"object": "data_source", "properties": {
                "공고명": {"type": "title"}, "기업명": RELATION, "마감일": {"type": "date"},
                "URL": {"type": "url"}, "지원 단계": {"type": "status"}}})
        assert COMPANY_DS in req.url.path
        return httpx.Response(200, json={"object": "data_source", "properties": {
            "기업명": {"type": "title"}, "상태": {"type": "select"}}})

    store = make_store(handler)
    store.company_source_id = ""
    problems = await store.check_schema()
    assert len(problems) == 1 and "'경력'" in problems[0]
    assert store.company_source_id == COMPANY_DS  # 기업 DB는 기업명 relation이 가리키는 곳


@pytest.mark.parametrize("company_prop, expected", [
    ({"type": "relation", "relation": {}}, "relation이 가리키는 관심 기업 리스트 DB를 찾지 못함"),
    ({"type": "rich_text"}, "'기업명' 속성이 없거나 형식이 다름"),
])
async def test_schema_check_needs_company_relation(company_prop, expected):
    def handler(req: httpx.Request):
        assert JOB_DS in req.url.path  # 기업 DB를 모르면 조회하지 않는다
        return httpx.Response(200, json={"object": "data_source", "properties": {
            "공고명": {"type": "title"}, "기업명": company_prop, "마감일": {"type": "date"},
            "경력": {"type": "select"}, "URL": {"type": "url"}, "지원 단계": {"type": "status"}}})

    store = make_store(handler)
    store.company_source_id = ""
    problems = await store.check_schema()
    assert len(problems) == 1 and expected in problems[0] and store.company_source_id == ""


async def test_schema_check_accepts_database_link():
    # .env에 DB 링크를 붙여넣어도 시작할 때 data source ID로 바꿔 쓴다
    def handler(req: httpx.Request):
        path = req.url.path
        if path.endswith(f"databases/{JOB_DB}"):
            return httpx.Response(200, json={"object": "database", "data_sources": [{"id": JOB_DS}]})
        if path.endswith(f"data_sources/{JOB_DS}"):
            return httpx.Response(200, json={"object": "data_source", "properties": {
                "공고명": {"type": "title"}, "기업명": RELATION, "마감일": {"type": "date"},
                "경력": {"type": "select"}, "URL": {"type": "url"}, "지원 단계": {"type": "status"}}})
        if path.endswith(f"data_sources/{COMPANY_DS}"):
            return httpx.Response(200, json={"object": "data_source", "properties": {
                "기업명": {"type": "title"}, "상태": {"type": "select"}}})
        return httpx.Response(404, json={"object": "error", "status": 404, "code": "object_not_found", "message": "nf"})

    store = make_store(handler)
    store.s.job_data_source_id = f"https://www.notion.so/ws/Jobs-{JOB_DB}?v={'9' * 32}"
    assert await store.check_schema() == []
    assert store.s.job_data_source_id == JOB_DS


@pytest.mark.parametrize("value, expected", [
    ("not-a-link", "채용 지원 관리 DB: 노션 링크에서 ID를 찾지 못함"),
    ("3" * 32, "채용 지원 관리 DB 접근 실패 (HTTP 404 object_not_found"),
])
async def test_schema_check_reports_unusable_job_link(value, expected):
    def handler(req: httpx.Request):
        return httpx.Response(404, json={"object": "error", "status": 404, "code": "object_not_found", "message": "nf"})

    store = make_store(handler)
    store.s.job_data_source_id = value
    problems = await store.check_schema()
    assert len(problems) == 1 and problems[0].startswith(expected)


async def test_schema_check_without_token():
    s = settings()
    s.notion_token = ""
    store = NotionStore(s, AsyncClient(auth="x"))
    assert await store.check_schema() == ["NOTION_TOKEN이 설정되지 않음 (.env 확인)"]


async def test_find_by_url_filter_other_site():
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"object": "list", "results": [], "has_more": False})

    await make_store(handler).find_by_url(normalize("https://www.wanted.co.kr/wd/394267?utm_source=x"))
    ors = seen["body"]["filter"]["or"]
    assert {"property": "URL", "url": {"equals": "https://www.wanted.co.kr/wd/394267"}} in ors
    assert {"property": "URL", "url": {"ends_with": "wanted.co.kr/wd/394267"}} in ors


async def test_find_by_url_generic_equals_only():
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"object": "list", "results": [], "has_more": False})

    await make_store(handler).find_by_url(normalize("https://recruit.i-sens.com/job_posting/DFUxhzD4"))
    assert seen["body"]["filter"] == {"property": "URL", "url": {"equals": "https://recruit.i-sens.com/job_posting/DFUxhzD4"}}


def test_image_only_body_is_partial():
    html = load("saramin_55434315.html")
    start = html.index('<div class="user_content')
    end = html.index("</div>", start)
    html = html[:start] + '<div class="user_content jobsViewDetail_55434315"><img src="x.png">' + html[end:]
    p = saramin.parse(html, "u", "55434315")
    assert p.title and not p.body_found
    blocks = build_children(p)
    assert blocks[0]["type"] == "callout"


def test_build_children_has_template_sections():
    p = parse_saramin("saramin_55434315.html", "55434315")
    blocks = build_children(p)
    headings = [b["heading_2"]["rich_text"][0]["text"]["content"] for b in blocks if b["type"] == "heading_2"]
    assert headings == ["공고 요약", "주요 업무", "자격 요건", "우대 사항", "전형 절차", "원문 링크"]
    assert len(blocks) <= 100
    texts = [
        "".join(r["text"]["content"] for r in b["bulleted_list_item"]["rich_text"])
        for b in blocks if b["type"] == "bulleted_list_item"
    ]
    assert "전형 절차" not in p.sections and "공고에서 확인 불가" in texts


def test_extract_notion_id():
    job_id = "1" * 32
    dashed = "12345678-1234-1234-1234-123456789abc"
    assert extract_notion_id(dashed) == dashed.replace("-", "")
    assert extract_notion_id(f"https://www.notion.so/deadbeef-{job_id}?v={'9' * 32}") == job_id
    with pytest.raises(NotionError):
        extract_notion_id("https://www.notion.so/no-id")
