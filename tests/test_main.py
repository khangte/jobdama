"""Host 헤더 확인 (DNS rebinding 방어)과 노션 연결·해제 엔드포인트."""

import asyncio
import json
import re
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
import app.routes.notion as notion_routes
from app.config import Settings, get_settings
from app.main import app
from app.notion.store import NotionStore
from app.routes.deps import get_runner, get_state
from app.service import JobService
from app.collection.collect_config import CollectConfig
from tests.test_collect_runner import make as make_runner


def test_foreign_host_rejected():
    client = TestClient(app, base_url="http://evil.example.com")
    assert client.post("/api/v1/jobs", json={"url": "https://example.com/jobs/1"}).status_code == 400
    assert client.get("/api/v1/jobs").status_code == 400


def test_local_hosts_allowed():
    for host in ("localhost:8765", "127.0.0.1:8000"):
        client = TestClient(app, base_url=f"http://{host}")
        assert client.get("/static/app.js").status_code == 200


def test_static_files_revalidate():
    # 예전 app.js가 캐시에서 쓰이면 폼이 JS 없이 제출돼 JSON 화면으로 넘어간다
    res = TestClient(app, base_url="http://localhost").get("/static/app.js")
    assert res.headers["cache-control"] == "no-cache"


def test_api_routes_are_versioned():
    paths = set(app.openapi()["paths"])  # Swagger에 보이는 경로
    assert {"/api/v1/jobs", "/api/v1/recheck", "/api/v1/notion/connect", "/api/v1/notion/disconnect", "/jobs", "/health",
            "/api/v1/collect", "/api/v1/collect/last", "/api/v1/collect/settings", "/collect", "/collect/results"} <= paths
    assert "/collect/settings" not in paths
    assert not any(p.startswith("/api/") and not p.startswith("/api/v1/") for p in paths)
    assert TestClient(app, base_url="http://localhost").get("/api/jobs").status_code == 404


# ------------------------------------------------------------ 노션 설정 입력

JOB_ID = "1" * 32
COMPANY_DS_ID = "3" * 32
ORPHAN_JOB_ID = "c" * 32  # 기업명 relation이 공유 안 된 DB를 가리키는 채용 DB
NO_CAREER_JOB_ID = "d" * 32  # 경력 열이 없는 채용 DB
JOB_PROPS = {"공고명": {"type": "title"}, "기업명": {"type": "relation", "relation": {"data_source_id": COMPANY_DS_ID}},
             "마감일": {"type": "date"}, "경력": {"type": "select"}, "URL": {"type": "url"}, "지원 단계": {"type": "status"}}
COMPANY_PROPS = {"기업명": {"type": "title"}, "상태": {"type": "select"}}


MULTI_DB_ID = "5" * 32
ENV_TEXT = "NOTION_TOKEN=from-env\nUSE_PLAYWRIGHT=true\n"
VALID = {"notion_token": "ntn_abc", "job_db": JOB_ID}


def notion_handler(req: httpx.Request):
    assert req.headers["authorization"] == "Bearer ntn_abc"
    path = req.url.path
    if path.endswith(f"data_sources/{'6' * 32}"):
        return httpx.Response(401, json={"object": "error", "status": 401, "code": "unauthorized", "message": "x"})
    if path.endswith(f"data_sources/{'7' * 32}"):
        return httpx.Response(502, json={"object": "error", "status": 502, "code": "bad_gateway", "message": "x"})
    if path.endswith(f"databases/{MULTI_DB_ID}"):
        return httpx.Response(200, json={"object": "database", "data_sources": [{"id": "a" * 32}, {"id": "b" * 32}]})
    if path.endswith(f"data_sources/{JOB_ID}"):
        return httpx.Response(200, json={"object": "data_source", "properties": JOB_PROPS})
    if path.endswith(f"data_sources/{ORPHAN_JOB_ID}"):
        props = {**JOB_PROPS, "기업명": {"type": "relation", "relation": {"data_source_id": "8" * 32}}}
        return httpx.Response(200, json={"object": "data_source", "properties": props})
    if path.endswith(f"data_sources/{NO_CAREER_JOB_ID}"):
        props = {k: v for k, v in JOB_PROPS.items() if k != "경력"}
        return httpx.Response(200, json={"object": "data_source", "properties": props})
    if path.endswith(f"data_sources/{COMPANY_DS_ID}"):
        return httpx.Response(200, json={"object": "data_source", "properties": COMPANY_PROPS})
    return httpx.Response(404, json={"object": "error", "status": 404, "code": "object_not_found", "message": "nf"})


class OldStore:
    closed = False

    async def aclose(self):
        self.closed = True


@pytest.fixture
def setup_client(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(ENV_TEXT)
    monkeypatch.chdir(tmp_path)  # 앱이 ./.env를 건드리는지 확인하려고 임시 폴더에서 돌린다
    for key in notion_routes.ENV_KEYS:
        monkeypatch.delenv(key.upper(), raising=False)
    created = []

    def make_store(s):
        # 실제 NotionStore 생성 경로를 타고, 전송만 가짜로 바꾼다
        http = httpx.AsyncClient(transport=httpx.MockTransport(notion_handler))
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: http)
        try:
            store = real_store(s)
        finally:
            monkeypatch.setattr(httpx, "AsyncClient", real_httpx)
        created.append(http)
        return store

    real_store, real_httpx = NotionStore, httpx.AsyncClient
    monkeypatch.setattr(notion_routes, "NotionStore", make_store)
    old = OldStore()
    st = main_mod.app.state
    # raising=False: 테스트가 끝나면 app.state를 원래대로 되돌린다
    monkeypatch.setattr(st, "settings", Settings(_env_file=None), raising=False)
    monkeypatch.setattr(st, "store", old, raising=False)
    monkeypatch.setattr(st, "problems", ["NOTION_TOKEN이 설정되지 않음 (.env 확인)"], raising=False)
    monkeypatch.setattr(st, "service", JobService(None, old, notion_ready=False), raising=False)
    return TestClient(main_mod.app, base_url="http://localhost"), env, old, created


async def test_notion_connect_without_touching_env(setup_client):
    client, env, old, created = setup_client
    # 같은 이벤트 루프에서 호출해야 이전 store를 닫는 백그라운드 작업까지 기다릴 수 있다
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main_mod.app), base_url="http://localhost") as ac:
        res = await ac.post("/api/v1/notion/connect", json={
            "notion_token": "ntn_abc",
            "job_db": f"https://www.notion.so/ws/Jobs-{JOB_ID}?v={'9' * 32}",
        })
        await asyncio.gather(*notion_routes.closing)
    assert res.status_code == 200, res.text
    assert res.json() == {"notion_ready": True, "job_data_source_id": JOB_ID, "company_data_source_id": COMPANY_DS_ID}
    assert env.read_text() == ENV_TEXT
    st = main_mod.app.state
    assert st.settings.notion_token == "ntn_abc"
    assert st.service.notion_ready and st.service.store is st.store and old.closed and st.problems == []
    assert not any(h.is_closed for h in created)
    assert client.post("/api/v1/notion/connect", json={"notion_token": "a", "job_db": "b"}).status_code == 409


async def test_disconnect_then_reconnect_from_web(setup_client):
    _, env, _, created = setup_client
    st = main_mod.app.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main_mod.app), base_url="http://localhost") as ac:
        assert (await ac.post("/api/v1/notion/connect", json=VALID)).status_code == 200
        connected = st.store
        res = await ac.post("/api/v1/notion/disconnect")
        await asyncio.gather(*notion_routes.closing)
        assert res.status_code == 200 and res.json() == {"notion_ready": False}
        assert not st.service.notion_ready and st.problems and st.service.store is st.store is not connected
        assert st.settings.notion_token == st.settings.job_data_source_id == st.store.company_source_id == ""
        assert created[0].is_closed  # 연결했던 store는 작업이 끝난 뒤 닫힌다
        # 해제한 상태에서는 다시 주입할 수 있고, 다시 해제해도 그대로 200
        assert (await ac.post("/api/v1/notion/connect", json=VALID)).status_code == 200
        assert st.service.notion_ready
        assert (await ac.post("/api/v1/notion/disconnect")).status_code == 200
        assert (await ac.post("/api/v1/notion/disconnect")).status_code == 200
        await asyncio.gather(*notion_routes.closing)
    assert env.read_text() == ENV_TEXT


async def test_notion_page_follows_connection_state(setup_client):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main_mod.app), base_url="http://localhost") as ac:
        page = await ac.get("/")
        for name in ("notion_token", "job_db"):
            assert f'name="{name}"' in page.text
        assert 'name="company_db"' not in page.text  # 기업 DB는 채용 DB의 relation에서 찾는다
        assert 'type="password"' in page.text
        assert 'data-notion-action="disconnect"' not in page.text
        assert 'disabled' in page.text

        assert (await ac.post("/api/v1/notion/connect", json=VALID)).status_code == 200
        page = await ac.get("/")
        assert 'data-notion-action="disconnect"' in page.text
        assert 'data-notion-action="connect"' not in page.text
        assert 'disabled' not in page.text
        assert VALID["notion_token"] not in page.text

        assert (await ac.post("/api/v1/notion/disconnect")).status_code == 200
        page = await ac.get("/")
        assert 'data-notion-action="connect"' in page.text
        assert 'data-notion-action="disconnect"' not in page.text
        assert 'disabled' in page.text
        assert VALID["notion_token"] not in page.text
        await asyncio.gather(*notion_routes.closing)


async def test_connected_page_lists_blank_columns(setup_client):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main_mod.app), base_url="http://localhost") as ac:
        res = await ac.post("/api/v1/notion/connect", json={**VALID, "job_db": NO_CAREER_JOB_ID})
        assert res.status_code == 200  # 선택 열이 없어도 연결된다
        page = await ac.get("/")
        assert "비워 두는 열 1개" in page.text and "경력 열을 정하지 못해 비워 둠" in page.text
        assert 'id="recheck"' in page.text
        assert (await ac.post("/api/v1/notion/disconnect")).status_code == 200
        await asyncio.gather(*notion_routes.closing)


def test_disconnect_rejects_foreign_origin(setup_client):
    client, _, old, _ = setup_client
    st = main_mod.app.state
    st.service.notion_ready = True
    res = client.post("/api/v1/notion/disconnect", headers={"Origin": "https://evil.example.com"})
    assert res.status_code == 403 and st.service.notion_ready and st.store is old


def test_notion_connect_rejects_bad_link_without_saving(setup_client):
    client, env, _, created = setup_client
    res = client.post("/api/v1/notion/connect", json={"notion_token": "ntn_abc", "job_db": "4" * 32})
    problem = res.json()["problems"][0]
    assert res.status_code == 400 and "채용 지원 관리 DB" in problem and "HTTP 404 object_not_found" in problem
    assert env.read_text() == ENV_TEXT
    assert not main_mod.app.state.service.notion_ready
    assert created and all(h.is_closed for h in created)


@pytest.mark.parametrize("job_db, expected", [
    ("https://www.notion.so/no-id", "ID를 찾지 못함"),
    (MULTI_DB_ID, "여러 개"),
    (ORPHAN_JOB_ID, "관심 기업 리스트 DB 접근 실패 (HTTP 404"),
])
def test_notion_connect_rejects_unusable_job_db(setup_client, job_db, expected):
    client, env, _, created = setup_client
    res = client.post("/api/v1/notion/connect", json={"notion_token": "ntn_abc", "job_db": job_db})
    assert res.status_code == 400 and expected in res.json()["problems"][0]
    assert env.read_text() == ENV_TEXT
    assert all(h.is_closed for h in created)


def test_notion_connect_rejects_foreign_origin(setup_client):
    client, env, _, created = setup_client
    res = client.post("/api/v1/notion/connect", headers={"Origin": "https://evil.example.com"},
                      json={"notion_token": "ntn_abc", "job_db": JOB_ID})
    assert res.status_code == 403 and not created
    assert env.read_text() == ENV_TEXT


def test_notion_connect_rejects_newline_in_token(setup_client):
    client, _, _, _ = setup_client
    assert client.post("/api/v1/notion/connect", json={
        "notion_token": "a\nJOB_DATA_SOURCE_ID=x", "job_db": JOB_ID}).status_code == 422


@pytest.mark.parametrize("job_db, status, text", [
    ("6" * 32, 400, "HTTP 401 unauthorized: 토큰 확인"),
    ("7" * 32, 502, "HTTP 502"),
])
def test_notion_connect_reports_http_status(setup_client, job_db, status, text):
    client, _, _, _ = setup_client
    res = client.post("/api/v1/notion/connect", json={"notion_token": "ntn_abc", "job_db": job_db})
    assert res.status_code == status and text in res.json()["problems"][0]


async def test_recheck_ignores_result_from_replaced_store(monkeypatch):
    st = main_mod.app.state
    new_store = OldStore()

    class SlowStore(OldStore):
        async def check_schema(self):
            st.store = new_store  # 점검하는 사이 설정 입력이 store를 교체한 상황
            return ["옛 결과"]

    service = JobService(None, new_store, notion_ready=True)
    monkeypatch.setattr(st, "store", SlowStore(), raising=False)
    monkeypatch.setattr(st, "problems", [], raising=False)
    monkeypatch.setattr(st, "service", service, raising=False)
    await notion_routes.recheck(st)
    assert service.notion_ready and st.problems == []


def test_manual_api_not_screened(setup_client, monkeypatch):
    client, _, _, _ = setup_client
    svc = main_mod.app.state.service

    def boom(*a, **k):
        raise AssertionError("수동 경로는 자동 수집 처리(run_one)를 타면 안 됨")

    monkeypatch.setattr(svc, "run_one", boom, raising=False)
    res = client.post("/api/v1/jobs", json={"url": "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=5"})
    assert res.status_code == 202


# ------------------------------------------------------------ 자동 수집 API

@pytest.fixture
def collect_client(monkeypatch, tmp_path):
    """수집 실행기를 가짜로 바꾼 ASGI 클라이언트 만들기 함수. (client, runner, service)를 돌려준다."""
    st = main_mod.app.state

    def build(**kw):
        runner, _, service = make_runner(tmp_path, **kw)
        monkeypatch.setattr(st, "collect", runner, raising=False)
        monkeypatch.setattr(st, "service", service, raising=False)
        monkeypatch.setattr(st, "settings", Settings(_env_file=None), raising=False)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main_mod.app), base_url="http://localhost")
        return client, runner, service

    return build


async def test_collect_without_body_accepted(collect_client):
    client, runner, _ = collect_client(sources=())
    async with client:
        res = await client.post("/api/v1/collect")
        await runner.wait()
    assert res.status_code == 202 and res.json() == {"started": True, "dry_run": False, "trigger": "schedule"}


async def test_collect_dry_run(collect_client):
    client, runner, _ = collect_client(sources=())
    async with client:
        assert (await client.post("/api/v1/collect", json={"dry_run": True})).status_code == 202
        await runner.wait()
        last = (await client.get("/api/v1/collect/last")).json()
    assert last["running"] is False and last["run"]["dry_run"] is True


async def test_collect_conflict(collect_client):
    client, runner, _ = collect_client(delay=0.05)
    async with client:
        assert (await client.post("/api/v1/collect")).status_code == 202
        second = await client.post("/api/v1/collect")
        await runner.aclose()
    assert second.status_code == 409


async def test_collect_foreign_origin(collect_client):
    client, runner, _ = collect_client(sources=())
    async with client:
        res = await client.post("/api/v1/collect", headers={"origin": "https://evil.example"})
    assert res.status_code == 403 and not runner.running


async def test_collect_notion_not_ready(collect_client):
    client, _, service = collect_client(sources=())
    service.notion_ready = False
    async with client:
        res = await client.post("/api/v1/collect")
    assert res.status_code == 503 and "노션 미연결" in res.json()["problems"][0]


async def test_collect_last_empty(collect_client):
    client, _, _ = collect_client(sources=())
    async with client:
        res = await client.get("/api/v1/collect/last")
    assert res.json() == {"running": False, "run": None}


async def test_collect_results_not_in_jobs(collect_client):
    client, runner, _ = collect_client()
    async with client:
        assert (await client.post("/api/v1/collect")).status_code == 202
        await runner.wait()
        last = (await client.get("/api/v1/collect/last")).json()
        jobs = (await client.get("/api/v1/jobs")).json()
    assert last["run"]["items"] and jobs == []


# ------------------------------------------------------------ 수집 화면

def _run(items=(), sites=(), **kw):
    """화면 테스트용 결과 dict."""
    return {"started_at": "2026-11-08T09:30:00+09:00", "finished_at": "2026-11-08T09:34:00+09:00", "trigger": "screen",
            "dry_run": False, "error": None, "sites": list(sites), "items": list(items), **kw}


def _item(group, title, message="", status=None, css=None):
    """화면 테스트용 결과 item."""
    return {"site": "saramin", "title": title, "status": status or group, "css": css or group, "group": group,
            "message": message, "notion_url": None, "url": "https://example.com/" + title}


async def test_collect_page_no_record(collect_client):
    client, _, _ = collect_client(sources=())
    async with client:
        res = await client.get("/collect")
    assert res.status_code == 200 and "아직 실행 기록이 없어요." in res.text
    assert 'aria-current="page"' in res.text and 'id="collect-run"' in res.text


async def test_collect_page_shows_reason_and_site_error(collect_client):
    client, runner, _ = collect_client(sources=())
    site = {"key": "incruit", "name": "인크루트", "candidates": 0, "saved": 0, "exists": 0, "skipped": 0, "failed": 0,
            "error": "HTTP 403"}
    runner._last = _run([_item("skipped", "백엔드", "근무지 제외: 부산")], [site], dry_run=True)
    async with client:
        res = await client.get("/collect")
    assert "근무지 제외: 부산" in res.text and "HTTP 403" in res.text and "미리보기" in res.text


async def test_collect_button_disabled_while_running(collect_client):
    client, runner, _ = collect_client(delay=0.05)
    async with client:
        await client.post("/api/v1/collect")
        running = (await client.get("/collect/results")).text
        await runner.aclose()
        done = (await client.get("/collect/results")).text
    assert "disabled" in running and "every 2s" in running and "수집 중이에요" in running
    assert "disabled" not in done and "every 2s" not in done


async def test_collect_button_disabled_when_notion_missing(collect_client):
    client, _, service = collect_client(sources=())
    service.notion_ready = False
    async with client:
        res = await client.get("/collect")
    assert "disabled" in res.text and 'href="/"' in res.text and "Notion을 연결해야 수집할 수 있어요." in res.text


async def test_collect_filter(collect_client):
    client, runner, _ = collect_client(sources=())
    runner._last = _run([_item("saved", "저장공고"), _item("skipped", "제외공고", "경력 제외")])
    async with client:
        only = (await client.get("/collect/results?status=skipped")).text
        bogus = (await client.get("/collect/results?status=bogus")).text
    assert "제외공고" in only and "저장공고" not in only
    assert "제외공고" in bogus and "저장공고" in bogus


async def test_main_nav_has_collect_link(setup_client):
    client, *_ = setup_client
    assert 'href="/collect"' in client.get("/").text


# ------------------------------------------------------------ 수집 조건 API

async def test_settings_get_defaults(collect_client):
    client, _, _ = collect_client(sources=())
    async with client:
        body = (await client.get("/api/v1/collect/settings")).json()
    assert body["settings"] == body["defaults"] and body["problem"] is None
    assert body["defaults"]["keyword"] == "python" and body["defaults"]["region"] == "seoul_gyeonggi"


async def test_settings_put_roundtrip_and_persist(collect_client, tmp_path):
    client, runner, _ = collect_client(sources=())
    async with client:
        body = {**(await client.get("/api/v1/collect/settings")).json()["settings"], "keyword": " 데이터 엔지니어 ", "region": "busan"}
        res = await client.put("/api/v1/collect/settings", json=body)
        got = (await client.get("/api/v1/collect/settings")).json()
    assert res.status_code == 200 and res.json()["settings"]["keyword"] == "데이터 엔지니어"
    assert runner.config.region == "busan" and (tmp_path / "collect_settings.json").exists()
    assert got["settings"] == res.json()["settings"] and got["settings"] != got["defaults"]


async def test_settings_put_invalid_422(collect_client):
    client, runner, _ = collect_client(sources=())
    async with client:
        res = await client.put("/api/v1/collect/settings", json={"sites": []})
    detail = res.json()["detail"][0]
    assert res.status_code == 422 and detail["type"] == "sites" and detail["msg"] == "사이트를 하나 이상 고르세요."
    assert runner.config.sites == CollectConfig().sites


async def test_settings_put_foreign_origin_403(collect_client, tmp_path):
    client, runner, _ = collect_client(sources=())
    async with client:
        res = await client.put("/api/v1/collect/settings", json={"keyword": "x"}, headers={"origin": "https://evil.example"})
    assert res.status_code == 403 and runner.config.keyword == "python" and not (tmp_path / "collect_settings.json").exists()


async def test_settings_put_write_error_500(collect_client, monkeypatch):
    client, runner, _ = collect_client(sources=())

    def boom(path, cfg):
        raise OSError("disk full")

    monkeypatch.setattr("app.collect_runner.save_config", boom)
    async with client:
        res = await client.put("/api/v1/collect/settings", json={"keyword": "x"})
    assert res.status_code == 500 and res.json() == {"problems": ["설정을 저장하지 못했어요."]} and runner.config.keyword == "python"


# ------------------------------------------------------------ 수집 조건 화면

def _select(html: str, name: str) -> list[tuple[str, str, bool]]:
    """화면에서 select 하나의 (값, 문구, 선택 여부) 목록을 읽는다."""
    body = re.search(rf'<select[^>]*name="{name}"[^>]*>(.*?)</select>', html, re.DOTALL).group(1)
    return [(v, t.strip(), "selected" in attrs) for v, attrs, t in re.findall(r'<option value="([^"]*)"([^>]*)>(.*?)</option>', body, re.DOTALL)]


async def test_collect_form_shows_saved_values(collect_client):
    client, runner, _ = collect_client(sources=())
    runner.update_config(CollectConfig(keyword="데이터 엔지니어", region="busan", job="backend"))
    async with client:
        html = (await client.get("/collect")).text
    assert 'value="데이터 엔지니어"' in html
    region = {v: sel for v, _, sel in _select(html, "region")}
    job = {v: sel for v, _, sel in _select(html, "job")}
    assert region["busan"] and not region["seoul_gyeonggi"] and job["backend"] and not job["any"]
    assert 'aria-current="page"' in html and 'id="settings-form"' in html


async def test_collect_form_every_select_has_any(collect_client):
    client, _, _ = collect_client(sources=())
    async with client:
        html = (await client.get("/collect")).text
    for name in ("job", "region", "career", "education", "employment", "salary", "sort"):
        assert _select(html, name)[0][:2] == ("any", "무관"), name


async def test_collect_form_problem_banner(collect_client, tmp_path):
    (tmp_path / "collect_settings.json").write_text("{broken", encoding="utf-8")
    client, runner, _ = collect_client(sources=())
    async with client:
        broken = (await client.get("/collect")).text
        runner.update_config(CollectConfig())  # 저장하면 정상 파일로 덮인다
        ok = (await client.get("/collect")).text
    assert "설정 파일을 읽지 못해 기본값을 쓰고 있어요." in broken
    assert "설정 파일을 읽지 못해" not in ok


async def test_collect_form_has_defaults_json(collect_client):
    client, runner, _ = collect_client(sources=())
    runner.update_config(CollectConfig(keyword="x"))
    async with client:
        html = (await client.get("/collect")).text
    raw = re.search(r'<script type="application/json" id="settings-defaults">(.*?)</script>', html, re.DOTALL).group(1)
    assert json.loads(raw) == CollectConfig().model_dump()


async def test_collect_form_outside_poll_fragment(collect_client):
    client, _, _ = collect_client(sources=())
    async with client:
        page = (await client.get("/collect")).text
        part = (await client.get("/collect/results")).text
    assert page.index('id="settings-form"') < page.index('id="collect"')
    assert 'id="settings-form"' not in part and "선별 조건" not in page and 'href="/collect/settings"' not in page


async def test_collect_form_words(collect_client):
    client, runner, _ = collect_client(sources=())
    runner.update_config(CollectConfig(title_exclude_words=["xyz"], body_signal_words=[], salary="4000"))
    async with client:
        html = (await client.get("/collect")).text
    assert "xyz" in html and "본문 신호 검사 안 함" in html
    assert {v: sel for v, _, sel in _select(html, "salary")}["4000"]


async def test_collect_page_ignores_wrong_shape_result_file(collect_client, tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "last.json").write_text('{"items": []}')
    client, _, _ = collect_client(sources=())
    async with client:
        res = await client.get("/collect")
    assert res.status_code == 200 and "아직 실행 기록이 없어요." in res.text


EXPECTED_ROUTES = {
    ("GET", "/", 200), ("GET", "/jobs", 200), ("GET", "/health", 200),
    ("GET", "/collect", 200), ("GET", "/collect/results", 200),
    ("POST", "/api/v1/jobs", 202), ("GET", "/api/v1/jobs", 200), ("POST", "/api/v1/recheck", 200),
    ("POST", "/api/v1/notion/connect", 200), ("POST", "/api/v1/notion/disconnect", 200),
    ("POST", "/api/v1/collect", 202), ("GET", "/api/v1/collect/last", 200),
    ("GET", "/api/v1/collect/settings", 200), ("PUT", "/api/v1/collect/settings", 200),
}


def test_route_table_unchanged():
    """라우터를 나눠도 경로·메서드·상태코드가 그대로인지 고정한다."""
    paths = app.openapi()["paths"]
    assert {(m.upper(), p, min(map(int, op["responses"]))) for p, ops in paths.items() for m, op in ops.items()} == EXPECTED_ROUTES


def test_lifespan_starts_and_stops_without_notion(monkeypatch, tmp_path):
    """토큰 없이 서버가 뜨고 화면을 그린 뒤 정상 종료되는지 확인한다."""
    monkeypatch.chdir(tmp_path)  # 프로젝트 .env를 읽지 않게 한다
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("JOB_DATA_SOURCE_ID", raising=False)
    monkeypatch.setenv("USE_PLAYWRIGHT", "false")
    get_settings.cache_clear()
    try:
        with TestClient(app, base_url="http://localhost") as client:
            assert client.get("/health").json() == {"ok": True, "notion_ready": False}
            assert client.get("/").status_code == 200
    finally:
        get_settings.cache_clear()


def test_state_dependency_can_be_overridden():
    """app.state를 건드리지 않고 상태 전체를 바꿔 끼울 수 있다."""
    fake = SimpleNamespace(service=JobService(None, OldStore(), notion_ready=True))
    app.dependency_overrides[get_state] = lambda: fake
    try:
        res = TestClient(app, base_url="http://localhost").get("/health")
    finally:
        app.dependency_overrides.clear()
    assert res.json() == {"ok": True, "notion_ready": True}


def test_runner_dependency_can_be_overridden(tmp_path):
    """수집 실행기만 따로 바꿔 끼울 수 있다."""
    runner, _, _ = make_runner(tmp_path)
    app.dependency_overrides[get_runner] = lambda: runner
    try:
        res = TestClient(app, base_url="http://localhost").get("/api/v1/collect/last")
    finally:
        app.dependency_overrides.clear()
    assert res.json() == {"running": False, "run": None}


def test_connect_validates_body_before_origin_check(setup_client):
    """잘못된 본문은 출처 검사보다 먼저 422로 거절된다."""
    client, _, _, created = setup_client
    res = client.post("/api/v1/notion/connect", headers={"Origin": "https://evil.example.com"},
                      json={"notion_token": "a\nb", "job_db": JOB_ID})
    assert res.status_code == 422 and not created
