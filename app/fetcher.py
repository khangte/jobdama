"""공고 페이지 수집. httpx와 Playwright 두 가지.

- 리디렉션은 직접 따라가며 매 단계 허용 대상인지 확인한다.
  등록 사이트는 해당 사이트 호스트만, 자체 채용 사이트는 DNS 결과가 공개 IP인 호스트만 허용한다.
- 봇 확인 화면이나 차단 응답이면 실패로 처리하고, 캐시·미러 등으로 우회하지 않는다.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from app.urls import GENERIC, Site, is_allowed_fetch_url

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"}
MAX_REDIRECTS = 5
MAX_BYTES = 8 * 1024 * 1024
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}

# 봇 확인·차단 화면 표식 (제목 또는 본문 앞부분)
CHALLENGE_MARKERS = (
    "vercel security checkpoint", "just a moment...", "attention required! | cloudflare",
    "checking your browser", "verifying you are human", "access denied", "request rejected",
    "잠시만 기다려 주십시오", "비정상적인 접근", "접근이 차단",
)

_CHARSET_RE = re.compile(rb"""<meta[^>]+charset=["']?\s*([A-Za-z0-9_\-]+)""", re.I)


class FetchError(Exception):
    pass


class BlockedTarget(FetchError):
    """허용되지 않은 주소. 다른 수집기로 다시 시도하지 않는다."""


def decode_html(content: bytes, header_charset: str | None = None) -> str:
    charset = header_charset
    if not charset:
        m = _CHARSET_RE.search(content[:4096])
        charset = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    charset = charset.lower()
    if charset in ("euc-kr", "ks_c_5601-1987", "ksc5601", "x-windows-949"):
        charset = "cp949"
    try:
        return content.decode(charset, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def looks_like_challenge(html: str) -> bool:
    head = html[:6000].lower()
    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.S)
    title = (m.group(1) if m else "").strip()
    if any(k in title for k in CHALLENGE_MARKERS):
        return True
    return len(html) < 60_000 and any(k in head for k in CHALLENGE_MARKERS[:5])


async def is_public_host(host: str) -> bool:
    """호스트의 DNS 결과가 모두 공개 IP인지. 내 PC의 내부망으로 요청이 가는 것을 막는다."""
    host = (host or "").strip("[]")
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    addrs = {info[4][0] for info in infos}
    return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)


async def check_target(url: str, site: Site) -> None:
    if not is_allowed_fetch_url(url, site):
        raise BlockedTarget(f"허용되지 않은 주소: {url}")
    if site is GENERIC and not await is_public_host(urlsplit(url).hostname or ""):
        raise BlockedTarget("내부망 주소로 연결되는 링크라 요청하지 않음")


class HttpFetcher:
    name = "httpx"

    def __init__(self, client: httpx.AsyncClient, concurrency: int = 4):
        self._client = client
        self._sem = asyncio.Semaphore(concurrency)

    async def fetch(self, url: str, site: Site) -> tuple[str, str]:
        """(html, 최종 URL)"""
        current = url
        async with self._sem:
            for _ in range(MAX_REDIRECTS + 1):
                await check_target(current, site)
                resp = await self._get(current)
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError("리디렉션 주소 없음")
                    current = urljoin(current, location)
                    continue
                if resp.status_code != 200:
                    raise FetchError(f"HTTP {resp.status_code}")
                if len(resp.content) > MAX_BYTES:
                    raise FetchError("응답이 너무 큼")
                html = decode_html(resp.content, resp.charset_encoding)
                if looks_like_challenge(html):
                    raise FetchError("봇 확인·차단 화면")
                return html, current
        raise FetchError("리디렉션이 너무 많음")

    async def _get(self, url: str) -> httpx.Response:
        """GET이라 연결이 중간에 끊기면(서버가 연결을 닫는 등) 한 번 더 시도한다."""
        for attempt in (1, 2):
            try:
                return await self._client.get(url, headers=HEADERS, follow_redirects=False)
            except httpx.TimeoutException:
                raise FetchError("응답 시간 초과")
            except httpx.TransportError as e:
                if attempt == 2:
                    raise FetchError(f"연결 실패: {type(e).__name__}")
                await asyncio.sleep(0.5)
            except httpx.HTTPError as e:
                raise FetchError(f"연결 실패: {type(e).__name__}")
        raise FetchError("연결 실패")


class BrowserFetcher:
    """Playwright Chromium. 처음 필요할 때 띄우고 서버 종료 시 닫는다."""

    name = "playwright"

    def __init__(self, enabled: bool, concurrency: int = 2, settle_ms: int = 8000):
        self.enabled = enabled
        self.settle_ms = settle_ms
        self._pw = None
        self._browser = None
        self._start_lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(concurrency)

    async def _ensure_browser(self):
        async with self._start_lock:
            if self._browser is not None:
                return self._browser
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                raise FetchError("Playwright 미설치")
            try:
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(headless=True)
            except Exception as e:  # 브라우저 바이너리 없음 등
                raise FetchError(f"Playwright 실행 실패: {type(e).__name__}")
            return self._browser

    async def fetch(self, url: str, site: Site) -> tuple[str, str]:
        if not self.enabled:
            raise FetchError("Playwright 사용 안 함")
        await check_target(url, site)
        browser = await self._ensure_browser()
        async with self._sem:
            # 서비스 워커 요청은 page.route로 가로챌 수 없으므로 막는다.
            # shortcut: page.route는 리디렉션의 첫 주소에만 불리므로 리디렉션 뒤 주소(하위 리소스 포함)는
            # 요청이 나간 뒤에야 확인된다. 완전히 막으려면 검사하는 프록시를 거치게 해야 한다.
            context = await browser.new_context(user_agent=USER_AGENT, locale="ko-KR", service_workers="block")
            try:
                page = await context.new_page()

                async def guard(route):
                    req = route.request
                    try:
                        if req.is_navigation_request() and req.frame == page.main_frame:
                            await check_target(req.url, site)
                        elif req.resource_type in BLOCKED_RESOURCE_TYPES:
                            await route.abort()  # 공고 텍스트만 필요하므로 이미지·영상·폰트는 받지 않음
                            return
                        elif site is GENERIC and not await is_public_host(urlsplit(req.url).hostname or ""):
                            await route.abort()
                            return
                    except FetchError:
                        await route.abort()
                        return
                    await route.continue_()

                await page.route("**/*", guard)
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                except Exception as e:
                    raise FetchError(f"브라우저 로딩 실패: {type(e).__name__}")
                try:  # 화면을 JS로 그리는 사이트는 데이터 요청이 끝날 때까지 잠시 기다린다
                    await page.wait_for_load_state("networkidle", timeout=self.settle_ms)
                except Exception:
                    pass
                await check_target(page.url, site)
                if resp is not None and resp.status >= 400:
                    raise FetchError(f"HTTP {resp.status}")
                html = await page.content()
                if looks_like_challenge(html):
                    raise FetchError("봇 확인·차단 화면")
                return html, page.url
            finally:
                await context.close()

    async def close(self) -> None:
        """서버 종료 시 정리. Ctrl+C로 브라우저 프로세스가 먼저 끝났으면 이미 닫힌 것이므로 오류를 무시한다."""
        browser, pw = self._browser, self._pw
        self._browser = self._pw = None
        for closer in (browser.close if browser else None, pw.stop if pw else None):
            if closer is None:
                continue
            try:
                await closer()
            except Exception as e:  # 이미 끊긴 연결
                log.debug("Playwright 정리 중 무시한 오류: %s", e)
