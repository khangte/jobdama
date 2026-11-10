"""수집 순서 결정. httpx·Playwright 수집기로 공고(와 본문) 페이지를 받아 파서에 넘긴다."""

from __future__ import annotations

import logging
from typing import Protocol

from app.collection.fetcher import BlockedTarget, FetchError
from app.collection.urls import NormalizedURL, Site, same_posting
from app.models import Posting
from app.parsers.dispatch import parse_pages
from app.parsers.errors import ParseError, PostingMismatch

log = logging.getLogger(__name__)


class Fetcher(Protocol):
    name: str

    async def fetch(self, url: str, site: Site) -> tuple[str, str]: ...


class Collector:
    """사이트별 순서로 수집한다.

    - 기본: httpx → (실패하거나, JS로 그리는 사이트인데 본문이 비면) Playwright
    - JS로 그릴 수 있는 자체 채용 사이트: 위와 같되 httpx 결과가
      본문 없이 제목만 있으면 Playwright 결과를 우선한다.
    - 본문을 별도 페이지로 주는 사이트(잡코리아, 인크루트, 캐치)는 본문 페이지도 같은 수집기로 받는다.
    """

    def __init__(self, http: Fetcher, browser: Fetcher | None, skip_body_sites: tuple[str, ...] = ()):
        self.http = http
        self.browser = browser
        self.skip_body_sites = skip_body_sites  # 본문 URL을 요청하지 않을 사이트 키(robots 금지 경로)

    async def _once(self, fetcher: Fetcher, target: NormalizedURL) -> Posting:
        html, final_url = await fetcher.fetch(target.url, target.site)
        if not same_posting(target, final_url):
            raise PostingMismatch("요청한 공고와 다른 공고로 이동됨")
        pages = {"main": html}
        body_url = None if target.site.key in self.skip_body_sites else target.extra.get("body_url")
        if body_url:
            try:
                pages["body"], _ = await fetcher.fetch(body_url, target.site)
            except Exception as e:  # Playwright Error 등도 본문 없이 진행한다
                log.info("본문 페이지 수집 실패 %s: %s", body_url, e)
        return parse_pages(target, pages)

    async def collect(self, target: NormalizedURL) -> Posting:
        reasons: list[str] = []
        first: Posting | None = None
        fetchers = [f for f in (self.http, self.browser) if f is not None]
        for i, fetcher in enumerate(fetchers):
            is_last = i == len(fetchers) - 1
            try:
                posting = await self._once(fetcher, target)
            except BlockedTarget as e:  # 막힌 주소는 브라우저로 다시 열지 않는다(브라우저는 리디렉션을 미리 막지 못함)
                reasons.append(f"{fetcher.name}: {e}")
                break
            except (FetchError, ParseError) as e:
                reasons.append(f"{fetcher.name}: {e}")
                log.info("수집 실패 %s %s: %s", fetcher.name, target.url, e)
                continue
            except Exception as e:  # Playwright Error 등 예상 못 한 오류도 다음 수집기·부분 저장으로 넘긴다
                reasons.append(f"{fetcher.name}: {type(e).__name__}")
                log.exception("수집 중 예상 못 한 오류 %s %s", fetcher.name, target.url)
                continue
            if posting.body_found:
                return posting
            first = first or posting
            reasons.append(f"{fetcher.name}: 본문 구간 없음")
            # 서버가 HTML을 다 주는 사이트는 브라우저로 다시 열어도 본문이 생기지 않는다(이미지형 공고 등)
            if not target.site.js_rendered and not is_last:
                break
        if first is not None:
            return first  # 제목은 확인, 본문 미확보 → 부분 저장
        raise FetchError(" / ".join(reasons) or "수집기 없음")
