"""노션 API 요청 형식 검증. httpx MockTransport로 실제 notion-client가 보내는 요청을 가로챈다."""

import json

import httpx
import pytest
from notion_client import AsyncClient
from notion_client.errors import HTTPResponseError

from app.config import Settings
from app.notion.blocks import build_children
from app.notion.errors import NotionError
from app.notion.pages import is_closed, normalize_title
from app.notion.schema import COMPANY_ROLES, JOB_ROLES, pick_option, resolve_props
from app.notion.store import NotionStore, extract_notion_id
from app.models import Posting
from app.parsers.sites import saramin
from app.collection.urls import normalize
from tests.helpers import load, parse_saramin

JOB_DS = "1" * 32  # check_schema가 링크처럼 ID를 해석하므로 실제 형식
JOB_DB = "2" * 32
COMPANY_DS = "company-ds"
RELATION = {"type": "relation", "relation": {"data_source_id": COMPANY_DS, "type": "dual_property"}}
STAGE = {"type": "status", "status": {
    "options": [{"id": "s1", "name": "📌 시작 전"}, {"id": "s2", "name": "✅ 지원 완료"}],
    "groups": [{"name": "To-do", "option_ids": ["s1"]}, {"name": "Complete", "option_ids": ["s2"]}]}}
JOB_SCHEMA = {"공고명": {"type": "title"}, "기업명": RELATION, "마감일": {"type": "date"},
              "경력": {"type": "select"}, "URL": {"type": "url"}, "지원 단계": STAGE}
COMPANY_SCHEMA = {"기업명": {"type": "title"}, "상태": {"type": "select", "select": {
    "options": [{"id": "c0", "name": "⭐ 관심"}, {"id": "c1", "name": "⚪️ 리서치 중"}]}}}


def settings() -> Settings:
    return Settings(notion_token="t", job_data_source_id=JOB_DS, _env_file=None)


def make_store(handler, job_schema=JOB_SCHEMA, company_schema=COMPANY_SCHEMA):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncClient(auth="t", client=http)
    store = NotionStore(settings(), client)
    # 원래는 check_schema가 스키마를 읽어 채운다
    store.job_props = resolve_props(job_schema, JOB_ROLES)[0]
    store.company_props = resolve_props(company_schema, COMPANY_ROLES)[0]
    store.company_source_id = COMPANY_DS if store.job_props["company"][1]["type"] == "relation" else ""
    return store


def schema_handler(job_schema=JOB_SCHEMA, company_schema=COMPANY_SCHEMA):
    def handler(req: httpx.Request):
        if JOB_DS in req.url.path:
            return httpx.Response(200, json={"object": "data_source", "properties": job_schema})
        assert COMPANY_DS in req.url.path
        return httpx.Response(200, json={"object": "data_source", "properties": company_schema})
    return handler


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


async def test_schema_check_warns_missing_optional_column():
    schema = {k: v for k, v in JOB_SCHEMA.items() if k != "경력"}
    store = make_store(schema_handler(schema))
    store.company_source_id, store.job_props = "", {}
    assert await store.check_schema() == []
    assert store.company_source_id == COMPANY_DS  # 기업 DB는 기업 relation이 가리키는 곳
    assert store.job_props["deadline"][0] == "마감일" and "career" not in store.job_props
    assert len(store.warnings) == 1 and store.warnings[0].startswith("채용 지원 관리 DB: 경력 열을 정하지 못해 비워 둠")


async def test_schema_check_needs_company_relation_target():
    def handler(req: httpx.Request):
        assert JOB_DS in req.url.path  # 기업 DB를 모르면 조회하지 않는다
        return httpx.Response(200, json={"object": "data_source", "properties": {
            **JOB_SCHEMA, "기업명": {"type": "relation", "relation": {}}}})

    store = make_store(handler)
    store.company_source_id = ""
    problems = await store.check_schema()
    assert problems == ["채용 지원 관리 DB: '기업명' relation이 가리키는 관심 기업 리스트 DB를 찾지 못함"]
    assert store.company_source_id == ""


async def test_schema_check_blocks_without_url_column():
    schema = {k: v for k, v in JOB_SCHEMA.items() if k != "URL"}
    problems = await make_store(schema_handler(schema)).check_schema()
    assert len(problems) == 1 and problems[0].startswith("채용 지원 관리 DB: URL 열을 정하지 못함")


async def test_schema_check_company_as_text_skips_company_db():
    schema = {**JOB_SCHEMA, "기업명": {"type": "rich_text"}}

    def handler(req: httpx.Request):
        assert JOB_DS in req.url.path  # relation이 없으면 기업 DB를 쓰지 않는다
        return httpx.Response(200, json={"object": "data_source", "properties": schema})

    store = make_store(handler)
    assert await store.check_schema() == []
    assert store.company_source_id == "" and store.job_props["company"][0] == "기업명"
    assert await store.find_or_create_company("(주)로브로스") is None


def test_resolve_renamed_columns_by_type_and_keyword():
    found, problems, warnings = resolve_props({
        "제목": {"type": "title"}, "회사": RELATION, "지원일": {"type": "date"}, "지원 마감": {"type": "date"},
        "연차": {"type": "multi_select"}, "직무": {"type": "select"}, "공고 주소": {"type": "url"},
        "진행": {"type": "status"}, "메모": {"type": "rich_text"},
    }, JOB_ROLES)
    assert {k: v[0] for k, v in found.items()} == {
        "title": "제목", "company": "회사", "url": "공고 주소", "deadline": "지원 마감", "career": "연차", "stage": "진행"}
    assert problems == warnings == []


def test_resolve_needs_keyword_for_date_and_select():
    # 날짜·선택 열이 하나뿐이어도 이름이 안 맞으면 쓰지 않는다(지원일에 마감일이 들어가지 않게)
    found, problems, warnings = resolve_props({
        "공고명": {"type": "title"}, "기업명": RELATION, "URL": {"type": "url"},
        "지원일": {"type": "date"}, "직무": {"type": "select"},
    }, JOB_ROLES)
    assert set(found) == {"title", "company", "url"} and problems == []
    assert [w.split(":")[0] for w in warnings] == [
        "마감일 열을 정하지 못해 비워 둠", "경력 열을 정하지 못해 비워 둠", "지원 단계 열을 정하지 못해 비워 둠"]


def test_resolve_ambiguous_and_type_priority():
    found, _, warnings = resolve_props({
        "공고명": {"type": "title"}, "기업명": RELATION, "URL": {"type": "url"},
        "서류 마감": {"type": "date"}, "면접 마감": {"type": "date"},
        "경력": {"type": "select"}, "경력 메모": {"type": "rich_text"},  # 같은 키워드면 앞선 형식(select)
    }, JOB_ROLES)
    assert found["career"][0] == "경력" and "deadline" not in found
    assert "후보가 여러 개(서류 마감, 면접 마감)" in warnings[0]


def test_resolve_does_not_reuse_column():
    # 같은 열이 두 역할에 잡히지 않는다: 관계형이 없으면 텍스트 '기업명'이 기업, '기업 상태'는 지원 단계 후보
    found, _, _ = resolve_props({
        "공고명": {"type": "title"}, "기업명": {"type": "rich_text"}, "링크": {"type": "url"},
        "기업 상태": {"type": "select"},
    }, JOB_ROLES)
    assert found["company"][0] == "기업명" and found["stage"][0] == "기업 상태"


def test_pick_option():
    assert pick_option(STAGE, "📌 시작 전") == "📌 시작 전"
    status = {"type": "status", "status": {
        "options": [{"id": "a", "name": "완료"}, {"id": "b", "name": "대기"}],
        "groups": [{"name": "To-do", "option_ids": ["b"]}, {"name": "Complete", "option_ids": ["a"]}]}}
    assert pick_option(status, "📌 시작 전") == "대기"  # 상태는 첫 그룹(할 일)의 첫 옵션
    assert pick_option(COMPANY_SCHEMA["상태"], "없는 옵션") == "⭐ 관심"
    assert pick_option({"type": "select", "select": {"options": []}}, "x") is None


async def test_create_posting_fits_column_types():
    body = {}
    queries = []

    def handler(req: httpx.Request):
        if req.url.path.endswith("/query"):
            queries.append(json.loads(req.content))
            return httpx.Response(200, json={"object": "list", "results": [], "has_more": False})
        body.update(json.loads(req.content))
        return httpx.Response(200, json={"object": "page", "id": "p-1", "url": "https://www.notion.so/p1"})

    store = make_store(handler, {
        "공고명": {"type": "title"}, "회사": {"type": "rich_text"}, "링크": {"type": "rich_text"},
        "마감": {"type": "rich_text"}, "경력": {"type": "multi_select"},
        "단계": {"type": "select", "select": {"options": [{"id": "x", "name": "대기"}]}},
    })
    posting = parse_saramin("saramin_55434315.html", "55434315")
    await store.create_posting(posting, None)
    props = body["properties"]
    assert props["회사"]["rich_text"][0]["text"]["content"] == "(주)로브로스"
    assert props["링크"]["rich_text"][0]["text"]["content"] == posting.url
    assert props["마감"]["rich_text"][0]["text"]["content"] == "2026-12-01"
    assert props["경력"] == {"multi_select": [{"name": "신입/경력"}]}
    assert props["단계"] == {"select": {"name": "대기"}}

    await store.find_by_url(normalize("https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=5"))
    ors = queries[0]["filter"]["or"]
    assert {"property": "링크", "rich_text": {"equals": "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=5"}} in ors


async def test_schema_check_accepts_database_link():
    # .env에 DB 링크를 붙여넣어도 시작할 때 data source ID로 바꿔 쓴다
    def handler(req: httpx.Request):
        path = req.url.path
        if path.endswith(f"databases/{JOB_DB}"):
            return httpx.Response(200, json={"object": "database", "data_sources": [{"id": JOB_DS}]})
        if path.endswith(f"data_sources/{JOB_DS}"):
            return httpx.Response(200, json={"object": "data_source", "properties": JOB_SCHEMA})
        if path.endswith(f"data_sources/{COMPANY_DS}"):
            return httpx.Response(200, json={"object": "data_source", "properties": COMPANY_SCHEMA})
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


def schema_rejected():
    return httpx.Response(400, json={"object": "error", "status": 400, "code": "validation_error",
                                     "message": "URL is not a property that exists."})


async def test_create_posting_rereads_schema_after_rejection():
    # 연결한 뒤 노션에서 URL 열 이름을 '링크'로 바꾼 상황
    renamed = {**{k: v for k, v in JOB_SCHEMA.items() if k != "URL"}, "링크": {"type": "url"}}
    created = []

    def handler(req: httpx.Request):
        if req.method == "GET":
            assert JOB_DS in req.url.path or COMPANY_DS in req.url.path
            schema = renamed if JOB_DS in req.url.path else COMPANY_SCHEMA
            return httpx.Response(200, json={"object": "data_source", "properties": schema})
        props = json.loads(req.content)["properties"]
        created.append(props)
        if "URL" in props:
            return schema_rejected()
        return httpx.Response(200, json={"object": "page", "id": "p-1", "url": "https://www.notion.so/p1"})

    store = make_store(handler)
    page = await store.create_posting(parse_saramin("saramin_55434315.html", "55434315"), "c-1")
    assert page["id"] == "p-1" and len(created) == 2
    assert "링크" in created[1] and "URL" not in created[1]
    assert store.job_props["url"][0] == "링크"


async def test_schema_reread_with_problem_does_not_retry():
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.method)
        if req.method == "GET":  # URL 열을 지워 버린 상황
            schema = {k: v for k, v in JOB_SCHEMA.items() if k != "URL"}
            return httpx.Response(200, json={"object": "data_source", "properties": schema})
        return schema_rejected()

    store = make_store(handler)
    with pytest.raises(NotionError, match="URL 열을 정하지 못함"):
        await store.find_by_url(normalize("https://www.wanted.co.kr/wd/394267"))
    assert calls[0] == "POST" and calls.count("POST") == 1  # 다시 읽은 스키마에 문제가 있으면 재시도하지 않는다


async def test_other_errors_are_not_retried():
    calls = []

    def handler(req: httpx.Request):
        calls.append(req.method)
        return httpx.Response(404, json={"object": "error", "status": 404, "code": "object_not_found", "message": "nf"})

    store = make_store(handler)
    with pytest.raises(HTTPResponseError):
        await store.find_or_create_company("카카오")
    assert calls == ["POST"]


# ---------------------------------------------------------------- 제목 중복·기업 조회·종료 판별

def test_normalize_title_strips_prefix_company_spaces():
    a = normalize_title("[와이온랩] [파견/대행] Python 백엔드 개발자", "와이온랩")
    assert a == normalize_title("[와이온랩] Python  백엔드개발자", "와이온랩") == "python백엔드개발자"


def test_normalize_title_removes_company_in_body():
    assert normalize_title("[(주)와이온랩] (주)와이온랩 Python 백엔드", "(주)와이온랩") == normalize_title("[와이온랩] Python 백엔드", "와이온랩")


def test_normalize_title_different():
    assert normalize_title("[A] Python 백엔드", "A") != normalize_title("[A] Python 데이터 엔지니어", "A")


def test_normalize_title_keeps_distinguishing_brackets():
    n = lambda t: normalize_title(t, "A")
    assert n("(신입) 백엔드 개발자") != n("(경력) 백엔드 개발자")
    assert n("[서울] 백엔드 개발자") != n("[부산] 백엔드 개발자")
    assert n("[신입/경력] 백엔드") != n("[경력] 백엔드")


def test_normalize_title_strips_company_like_brackets():
    assert normalize_title("[서울반도체] 백엔드", "서울반도체") == normalize_title("백엔드", "서울반도체") == "백엔드"
    assert normalize_title("[서울반도체] 백엔드", None) == "백엔드"


def test_normalize_title_empty_when_only_brackets():
    assert normalize_title("[채용공고]", "A") == ""


def test_is_closed():
    assert is_closed({"properties": {"지원 단계": {"type": "status", "status": {"name": "⏹️ 종료"}}}})
    assert not is_closed({"properties": {"지원 단계": {"type": "status", "status": {"name": "📌 시작 전"}}}})
    assert not is_closed({"properties": {"지원 단계": {"type": "status", "status": None}}})


def job_page(pid: str, title: str, url: str) -> dict:
    return {"object": "page", "id": pid, "url": f"https://www.notion.so/{pid}", "properties": {
        "공고명": {"type": "title", "title": [{"plain_text": title}]}, "URL": {"type": "url", "url": url}}}


def posting(title: str, url: str) -> Posting:
    return Posting(source="jobkorea", url=url, title=title, raw_title=title, company="와이온랩")


def title_store(pages: list[dict]):
    """페이지 목록을 돌려주고 요청을 기록하는 store."""
    seen: list[dict] = []

    def handler(req: httpx.Request):
        assert req.url.path == f"/v1/data_sources/{JOB_DS}/query"
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"object": "list", "results": pages, "has_more": False})

    return make_store(handler), seen


async def test_find_by_title_jobkorea_repost():
    old = job_page("old", "[와이온랩] Python 백엔드 개발자", "https://www.jobkorea.co.kr/Recruit/GI_Read/49947457")
    store, seen = title_store([old])
    found = await store.find_by_title(
        posting("[와이온랩] Python 백엔드 개발자", "https://www.jobkorea.co.kr/Recruit/GI_Read/49999999"), "company-1")
    assert found["id"] == "old"
    assert seen[0]["filter"] == {"property": "기업명", "relation": {"contains": "company-1"}}


async def test_find_by_title_empty_key_returns_none():
    store, seen = title_store([job_page("old", "[채용공고]", "u")])
    assert await store.find_by_title(posting("[채용공고]", "u2"), "company-1") is None
    assert seen == []


async def test_find_by_title_cross_site():
    old = job_page("old", "[와이온랩] [파견/대행] Python 백엔드 개발자", "https://www.jobkorea.co.kr/Recruit/GI_Read/1")
    store, _ = title_store([old])
    new = posting("[와이온랩] Python 백엔드 개발자", "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=5")
    assert (await store.find_by_title(new, "company-1"))["id"] == "old"


async def test_find_by_title_none_when_titles_differ():
    store, _ = title_store([job_page("old", "[와이온랩] 데이터 엔지니어", "u")])
    assert await store.find_by_title(posting("[와이온랩] Python 백엔드 개발자", "u2"), "company-1") is None


async def test_find_by_title_without_company_page_skips_query():
    store, seen = title_store([job_page("old", "[와이온랩] Python 백엔드 개발자", "u")])
    assert await store.find_by_title(posting("[와이온랩] Python 백엔드 개발자", "u2"), None) is None
    assert seen == []


async def test_find_company_read_only():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path))
        assert req.url.path.endswith("/query"), "읽기 전용이라 생성 요청이 없어야 함"
        return httpx.Response(200, json={"object": "list", "has_more": False, "results": [company_page("c-1", "와이온랩")]})

    store = make_store(handler)
    assert await store.find_company("(주)와이온랩") == "c-1"
    assert await store.find_company("없는회사") is None
    assert all(path.endswith("/query") for _, path in calls)
