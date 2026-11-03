"""Host 헤더 확인 (DNS rebinding 방어)과 노션 설정 입력 엔드포인트."""

import asyncio
import os

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
    assert client.post("/api/jobs", json={"url": "https://example.com/jobs/1"}).status_code == 400
    assert client.get("/api/jobs").status_code == 400


def test_local_hosts_allowed():
    for host in ("localhost:8765", "127.0.0.1:8000"):
        client = TestClient(app, base_url=f"http://{host}")
        assert client.get("/static/app.js").status_code == 200


# ------------------------------------------------------------ 노션 설정 입력

JOB_ID = "1" * 32
COMPANY_DB_ID = "2" * 32
COMPANY_DS_ID = "3" * 32
JOB_PROPS = {"공고명": {"type": "title"}, "기업명": {"type": "relation"}, "마감일": {"type": "date"},
             "경력": {"type": "select"}, "URL": {"type": "url"}, "지원 단계": {"type": "status"}}
COMPANY_PROPS = {"기업명": {"type": "title"}, "상태": {"type": "select"}}


MULTI_DB_ID = "5" * 32


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
    if path.endswith(f"data_sources/{COMPANY_DS_ID}"):
        return httpx.Response(200, json={"object": "data_source", "properties": COMPANY_PROPS})
    if path.endswith(f"databases/{COMPANY_DB_ID}"):
        return httpx.Response(200, json={"object": "database", "data_sources": [{"id": COMPANY_DS_ID, "name": "x"}]})
    return httpx.Response(404, json={"object": "error", "status": 404, "code": "object_not_found", "message": "nf"})


class OldStore:
    closed = False

    async def aclose(self):
        self.closed = True


@pytest.fixture
def setup_client(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("NOTION_TOKEN=\nUSE_PLAYWRIGHT=true\n")
    monkeypatch.setattr("app.config.ENV_PATH", env)
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


async def test_notion_settings_saves_and_connects(setup_client):
    client, env, old, created = setup_client
    # 같은 이벤트 루프에서 호출해야 이전 store를 닫는 백그라운드 작업까지 기다릴 수 있다
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main_mod.app), base_url="http://localhost") as ac:
        res = await ac.post("/api/notion/settings", json={
            "notion_token": "ntn_abc",
            "job_db": f"https://www.notion.so/ws/Jobs-{JOB_ID}?v={'9' * 32}",
            "company_db": COMPANY_DB_ID,
        })
        await asyncio.gather(*main_mod._closing)
    assert res.status_code == 200, res.text
    assert res.json() == {"notion_ready": True, "job_data_source_id": JOB_ID,
                          "company_data_source_id": COMPANY_DS_ID, "warnings": []}
    assert env.read_text() == (f"NOTION_TOKEN=ntn_abc\nUSE_PLAYWRIGHT=true\n"
                               f"JOB_DATA_SOURCE_ID={JOB_ID}\nCOMPANY_DATA_SOURCE_ID={COMPANY_DS_ID}\n")
    assert os.stat(env).st_mode & 0o777 == 0o600
    st = main_mod.app.state
    assert st.service.notion_ready and st.service.store is st.store and old.closed and st.problems == []
    assert not any(h.is_closed for h in created)
    assert client.post("/api/notion/settings", json={"notion_token": "a", "job_db": "b", "company_db": "c"}).status_code == 409


def test_notion_settings_rejects_bad_link_without_saving(setup_client):
    client, env, _, created = setup_client
    res = client.post("/api/notion/settings", json={"notion_token": "ntn_abc", "job_db": "4" * 32, "company_db": COMPANY_DB_ID})
    problem = res.json()["problems"][0]
    assert res.status_code == 400 and "채용 지원 관리 DB" in problem and "HTTP 404 object_not_found" in problem
    assert env.read_text() == "NOTION_TOKEN=\nUSE_PLAYWRIGHT=true\n"
    assert not main_mod.app.state.service.notion_ready
    assert created and all(h.is_closed for h in created)


@pytest.mark.parametrize("company_db, expected", [
    ("https://www.notion.so/no-id", "ID를 찾지 못함"),
    (MULTI_DB_ID, "여러 개"),
])
def test_notion_settings_rejects_unusable_company_db(setup_client, company_db, expected):
    client, env, _, created = setup_client
    res = client.post("/api/notion/settings", json={"notion_token": "ntn_abc", "job_db": JOB_ID, "company_db": company_db})
    assert res.status_code == 400 and expected in res.json()["problems"][0]
    assert env.read_text() == "NOTION_TOKEN=\nUSE_PLAYWRIGHT=true\n"
    assert all(h.is_closed for h in created)


def test_notion_settings_rejects_foreign_origin(setup_client):
    client, env, _, created = setup_client
    res = client.post("/api/notion/settings", headers={"Origin": "https://evil.example.com"},
                      json={"notion_token": "ntn_abc", "job_db": JOB_ID, "company_db": COMPANY_DB_ID})
    assert res.status_code == 403 and not created
    assert env.read_text() == "NOTION_TOKEN=\nUSE_PLAYWRIGHT=true\n"


def test_notion_settings_rejects_newline_in_token(setup_client):
    client, _, _, _ = setup_client
    assert client.post("/api/notion/settings", json={
        "notion_token": "a\nJOB_DATA_SOURCE_ID=x", "job_db": JOB_ID, "company_db": COMPANY_DB_ID}).status_code == 422


@pytest.mark.parametrize("job_db, status, text", [
    ("6" * 32, 400, "HTTP 401 unauthorized: 토큰 확인"),
    ("7" * 32, 502, "HTTP 502"),
])
def test_notion_settings_reports_http_status(setup_client, job_db, status, text):
    client, _, _, _ = setup_client
    res = client.post("/api/notion/settings", json={"notion_token": "ntn_abc", "job_db": job_db, "company_db": COMPANY_DB_ID})
    assert res.status_code == status and text in res.json()["problems"][0]


def test_notion_settings_warns_env_override(setup_client, monkeypatch):
    client, _, _, _ = setup_client
    monkeypatch.setenv("NOTION_TOKEN", "from-shell")
    res = client.post("/api/notion/settings", json={"notion_token": "ntn_abc", "job_db": JOB_ID, "company_db": COMPANY_DB_ID})
    assert res.status_code == 200 and res.json()["warnings"] == ["NOTION_TOKEN 환경변수가 있어 재시작하면 .env 값 대신 쓰임"]


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


def test_notion_settings_refuses_when_env_has_values(setup_client):
    client, env, _, created = setup_client
    env.write_text("NOTION_TOKEN=mine\nJOB_DATA_SOURCE_ID=\n")
    res = client.post("/api/notion/settings", json={"notion_token": "ntn_abc", "job_db": JOB_ID, "company_db": COMPANY_DB_ID})
    assert res.status_code == 409 and "NOTION_TOKEN" in res.json()["problems"][0]
    assert env.read_text() == "NOTION_TOKEN=mine\nJOB_DATA_SOURCE_ID=\n" and not created


def test_notion_settings_overwrites_when_asked(setup_client):
    client, env, _, _ = setup_client
    env.write_text("NOTION_TOKEN=wrong\nUSE_PLAYWRIGHT=true\n")
    res = client.post("/api/notion/settings", json={
        "notion_token": "ntn_abc", "job_db": JOB_ID, "company_db": COMPANY_DB_ID, "overwrite": True})
    assert res.status_code == 200, res.text
    assert env.read_text() == (f"NOTION_TOKEN=ntn_abc\nUSE_PLAYWRIGHT=true\n"
                               f"JOB_DATA_SOURCE_ID={JOB_ID}\nCOMPANY_DATA_SOURCE_ID={COMPANY_DS_ID}\n")


def test_notion_settings_conflict_when_env_filled_during_check(setup_client, monkeypatch):
    client, env, _, created = setup_client
    real_check = NotionStore.check_schema

    async def check_then_user_edits(self):
        env.write_text("NOTION_TOKEN=typed-meanwhile\n")
        return await real_check(self)

    monkeypatch.setattr(NotionStore, "check_schema", check_then_user_edits)
    res = client.post("/api/notion/settings", json={"notion_token": "ntn_abc", "job_db": JOB_ID, "company_db": COMPANY_DB_ID})
    assert res.status_code == 409 and "NOTION_TOKEN" in res.json()["problems"][0]
    assert env.read_text() == "NOTION_TOKEN=typed-meanwhile\n"
    assert all(h.is_closed for h in created)
