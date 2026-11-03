"""진단 명령(python -m app.snapshot)."""

import app.snapshot as snap


async def test_snapshot_file_names_keep_dots(tmp_path, monkeypatch):
    class Fake:
        def __init__(self, name):
            self.name = name

        async def fetch(self, url, site):
            return f"<html><title>{self.name} {url}</title></html>", url

    monkeypatch.setattr(snap, "OUT_DIR", tmp_path)
    for u in ("https://careers.example.com/jobs/1", "https://careers.example.com/jobs/2"):
        await snap.snapshot(u, Fake("httpx"), Fake("playwright"))
    assert len(list(tmp_path.glob("*.html"))) == 4  # URL 2개 × 수집기 2개가 서로 덮어쓰지 않음
