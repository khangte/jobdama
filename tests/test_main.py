"""Host 헤더 확인 (DNS rebinding 방어)과 노션 연결·해제 엔드포인트."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.config import Settings
from app.main import app
from app.notion_store import NotionStore
from app.service import JobService


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
    assert {"/api/v1/jobs", "/api/v1/recheck", "/api/v1/notion/connect", "/api/v1/notion/disconnect", "/jobs", "/health"} <= paths
    assert not any(p.startswith("/api/") and not p.startswith("/api/v1/") for p in paths)
    assert TestClient(app, base_url="http://localhost").get("/api/jobs").status_code == 404


# ------------------------------------------------------------ 노션 설정 입력

JOB_ID = "1" * 32
COMPANY_DS_ID = "3" * 32
ORPHAN_JOB_ID = "c" * 32  # 기업명 relation이 공유 안 된 DB를 가리키는 채용 DB
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
    for key in main_mod.ENV_KEYS:
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
    monkeypatch.setattr(main_mod, "NotionStore", make_store)
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
        await asyncio.gather(*main_mod._closing)
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
        await asyncio.gather(*main_mod._closing)
        assert res.status_code == 200 and res.json() == {"notion_ready": False}
        assert not st.service.notion_ready and st.problems and st.service.store is st.store is not connected
        assert st.settings.notion_token == st.settings.job_data_source_id == st.store.company_source_id == ""
        assert created[0].is_closed  # 연결했던 store는 작업이 끝난 뒤 닫힌다
        # 해제한 상태에서는 다시 주입할 수 있고, 다시 해제해도 그대로 200
        assert (await ac.post("/api/v1/notion/connect", json=VALID)).status_code == 200
        assert st.service.notion_ready
        assert (await ac.post("/api/v1/notion/disconnect")).status_code == 200
        assert (await ac.post("/api/v1/notion/disconnect")).status_code == 200
        await asyncio.gather(*main_mod._closing)
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
        await asyncio.gather(*main_mod._closing)


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
    await main_mod.recheck(type("R", (), {"app": main_mod.app})())
    assert service.notion_ready and st.problems == []
