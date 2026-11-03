"""HTTP·브라우저 수집과 차단 화면·내부망 검사."""

import httpx
import playwright.async_api
import pytest

from app.collection.fetcher import (
    BlockedTarget, BrowserFetcher, FetchError, HttpFetcher, check_target, decode_html, is_public_host,
    looks_like_challenge,
)
from app.collection.urls import GENERIC, INCRUIT, WANTED
from tests.helpers import FIX, rd


def test_vercel_checkpoint_is_challenge():
    assert looks_like_challenge(rd("vercel_checkpoint.html"))
    assert not looks_like_challenge(rd("wanted_394267.html"))
    assert not looks_like_challenge(rd("saramin_55434315.html"))


async def test_public_host_check():
    assert await is_public_host("8.8.8.8")
    assert not await is_public_host("127.0.0.1")
    assert not await is_public_host("10.1.2.3")
    assert not await is_public_host("localhost")
    assert not await is_public_host("no-such-host.invalid")


async def test_internal_target_raises_blocked():
    with pytest.raises(BlockedTarget):
        await check_target("http://127.0.0.1/careers", GENERIC)


async def test_http_redirect_to_internal_is_blocked_before_request():
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BlockedTarget):
            await HttpFetcher(client).fetch("http://8.8.8.8/jobs/1", GENERIC)
    assert seen == ["http://8.8.8.8/jobs/1"]  # 내부 주소로는 요청하지 않음


async def test_http_redirect_without_location_is_fetch_error():
    # httpx의 is_redirect는 Location이 없어도 3xx면 True라서 "리디렉션 주소 없음" 분기를 탄다
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(302))) as client:
        with pytest.raises(FetchError, match="리디렉션 주소 없음"):
            await HttpFetcher(client).fetch("http://8.8.8.8/jobs/1", GENERIC)


def test_decode_html_meta_charset():
    raw = '<html><head><meta charset="euc-kr"></head><body>경력무관</body></html>'.encode("cp949")
    assert "경력무관" in decode_html(raw, None)


async def test_http_fetcher_retries_once_on_dropped_connection():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected")
        return httpx.Response(200, content=b"<html><title>ok</title></html>",
                              headers={"content-type": "text/html; charset=euc-kr"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        html, _ = await HttpFetcher(c).fetch("https://job.incruit.com/jobdb_info/jobpost.asp?job=1", INCRUIT)
    assert "ok" in html and calls["n"] == 2


async def test_http_fetcher_blocks_redirect_to_other_host():
    def handler(req):
        return httpx.Response(302, headers={"location": "https://evil.example.com/"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(FetchError):
            await HttpFetcher(c).fetch("https://www.wanted.co.kr/wd/1", WANTED)


async def test_http_fetcher_challenge_page_fails():
    page = (FIX / "vercel_checkpoint.html").read_bytes()

    def handler(req):
        return httpx.Response(200, content=page, headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(FetchError, match="봇 확인"):
            await HttpFetcher(c).fetch("https://8.8.8.8/job", GENERIC)


async def test_browser_close_ignores_dead_connection():
    class Dead:
        async def close(self):
            raise RuntimeError("Browser.close: the handler is closed")

        async def stop(self):
            raise RuntimeError("closed")

    b = BrowserFetcher()
    b._browser, b._pw = Dead(), Dead()
    await b.close()  # 예외 없이 끝나야 함
    await b.close()  # 두 번 불러도 안전


async def test_browser_relaunched_after_crash(monkeypatch):
    class FakeBrowser:
        def __init__(self, alive):
            self.alive, self.closed = alive, False

        def is_connected(self):
            return self.alive

        async def close(self):
            self.closed = True

    class FakePW:
        def __init__(self):
            self.chromium = self
            self.stopped = False

        async def launch(self, headless):
            return FakeBrowser(True)

        async def stop(self):
            self.stopped = True

    new_pw = FakePW()

    class Starter:
        async def start(self):
            return new_pw

    monkeypatch.setattr(playwright.async_api, "async_playwright", lambda: Starter())
    b = BrowserFetcher()
    dead, old_pw = FakeBrowser(False), FakePW()
    b._browser, b._pw = dead, old_pw
    browser = await b._ensure_browser()
    assert browser is not dead and browser.is_connected()
    assert dead.closed and old_pw.stopped
    assert await b._ensure_browser() is browser  # 살아 있으면 재사용
