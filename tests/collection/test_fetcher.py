"""HTTP·브라우저 수집과 차단 화면·내부망 검사."""

import asyncio
import socket

import httpx
import playwright.async_api
import pytest

import app.collection.fetcher as fetcher_mod
from app.collection.fetcher import (
    BlockedTarget, BrowserFetcher, FetchError, HttpFetcher, Pacer, check_target, decode_html, is_public_host,
    looks_like_challenge, resolve_public,
)
from app.collection.urls import GENERIC, INCRUIT, SARAMIN, WANTED
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


async def test_http_fetcher_rejects_oversized_content_length_without_reading_body(monkeypatch):
    monkeypatch.setattr(fetcher_mod, "MAX_BYTES", 10)
    read = {"n": 0}

    async def body():
        read["n"] += 1
        yield b"x" * 100

    def handler(req):
        return httpx.Response(200, headers={"content-length": "100"}, content=body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(FetchError, match="너무 큼"):
            await HttpFetcher(c).fetch("http://8.8.8.8/jobs/1", GENERIC)
    assert read["n"] == 0


async def test_http_fetcher_stops_streaming_past_max_bytes(monkeypatch):
    monkeypatch.setattr(fetcher_mod, "MAX_BYTES", 10)
    sent = {"n": 0}

    async def body():  # Content-Length 없이 끝없이 보내는 서버
        for _ in range(1000):
            sent["n"] += 1
            yield b"x" * 8

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body()))) as c:
        with pytest.raises(FetchError, match="너무 큼"):
            await HttpFetcher(c).fetch("http://8.8.8.8/jobs/1", GENERIC)
    assert sent["n"] < 5


async def test_http_fetcher_pins_validated_ip_against_dns_rebinding(monkeypatch):
    resolved = []

    async def fake_resolve(host):
        resolved.append(host)
        return "93.184.216.34"

    monkeypatch.setattr(fetcher_mod, "resolve_public", fake_resolve)
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        return httpx.Response(200, content=b"<html><title>ok</title></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        await HttpFetcher(c).fetch("https://careers.example.com:8443/jobs/1", GENERIC)
    assert resolved == ["careers.example.com"]  # 한 번만 조회
    req = seen[0]
    assert req.url.host == "93.184.216.34"  # 검증한 IP로 직접 연결
    assert req.headers["host"] == "careers.example.com:8443"
    assert req.extensions["sni_hostname"] == "careers.example.com"
    assert req.headers["connection"] == "close"  # IP 키 연결이 다른 호스트 요청에 재사용되지 않게


async def test_resolve_public_keeps_getaddrinfo_order(monkeypatch):
    def info(addr):
        return (socket.AF_INET6 if ":" in addr else socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0))

    async def fake_getaddrinfo(host, port, **kw):  # OS가 정한 순서: IPv4 먼저(IPv6 경로 없는 PC)
        return [info("93.184.216.34"), info("2600:9000::1"), info("93.184.216.34")]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
    assert await resolve_public("dual.example.com") == "93.184.216.34"


# ---------------------------------------------------------------- 같은 호스트 요청 간격

def fake_pacer():
    """가짜 시계와 sleep 기록을 가진 Pacer. 시계는 now[0]을 올려 진행시킨다."""
    now, slept = [100.0], []

    async def sleep(sec):
        slept.append(sec)
        now[0] += sec

    return Pacer(1.0, 1.5, clock=lambda: now[0], sleep=sleep, rand=lambda a, b: 1.2), now, slept


async def test_pacer_waits_same_host():
    pacer, now, slept = fake_pacer()
    await pacer.wait("a")
    now[0] += 0.3
    await pacer.wait("a")
    assert slept == [pytest.approx(0.9)]


async def test_pacer_other_host_no_wait():
    pacer, _, slept = fake_pacer()
    await pacer.wait("a")
    await pacer.wait("b")
    assert slept == []


async def test_pacer_no_wait_after_gap():
    pacer, now, slept = fake_pacer()
    await pacer.wait("a")
    now[0] += 2.0
    await pacer.wait("a")
    assert slept == []


async def test_http_fetcher_uses_pacer():
    class Recorder:
        def __init__(self):
            self.hosts = []

        async def wait(self, host):
            self.hosts.append(host)

    pacer = Recorder()
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text="<html><body>ok</body></html>"))
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = HttpFetcher(client, pacer=pacer)
        await fetcher.fetch("https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=1", SARAMIN)
        await fetcher.fetch("https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=2", SARAMIN)
    assert pacer.hosts == ["www.saramin.co.kr", "www.saramin.co.kr"]


async def test_http_fetcher_paces_retry_attempt():
    """연결이 끊겨 다시 시도하는 GET도 같은 호스트 간격을 지킨다."""
    hosts, calls = [], {"n": 0}

    class Recorder:
        async def wait(self, host):
            hosts.append(host)

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected")
        return httpx.Response(200, text="<html><body>ok</body></html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await HttpFetcher(client, pacer=Recorder()).fetch("https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=1", SARAMIN)
    assert calls["n"] == 2 and hosts == ["www.saramin.co.kr"] * 2
