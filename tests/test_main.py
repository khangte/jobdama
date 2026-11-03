"""Host 헤더 확인 (DNS rebinding 방어)."""

from fastapi.testclient import TestClient

from app.main import app


def test_foreign_host_rejected():
    client = TestClient(app, base_url="http://evil.example.com")
    assert client.post("/api/jobs", json={"url": "https://example.com/jobs/1"}).status_code == 400
    assert client.get("/api/jobs").status_code == 400


def test_local_hosts_allowed():
    for host in ("localhost:8765", "127.0.0.1:8000"):
        client = TestClient(app, base_url=f"http://{host}")
        assert client.get("/static/app.js").status_code == 200
