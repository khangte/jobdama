"""진단용: 공고 링크의 HTML을 저장하고 파싱 결과를 출력한다. 노션에는 아무것도 쓰지 않는다.

    uv run python -m app.snapshot <공고 링크> [<공고 링크> ...]

결과 파일: snapshots/<사이트>_<공고번호>_<httpx|playwright>[_body].html
파서가 값을 잘못 뽑는 사이트가 있으면 이 파일을 개발 쪽에 넘겨 파서를 고친다.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import httpx

from app.config import get_settings
from app.collection.fetcher import BrowserFetcher, FetchError, HttpFetcher
from app.parsers.errors import ParseError
from app.parsers.dispatch import parse_pages
from app.collection.urls import UnsupportedURL, normalize

OUT_DIR = Path("snapshots")


def _safe(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", s)[:80]


async def snapshot(raw: str, http: HttpFetcher, browser: BrowserFetcher | None) -> None:
    print(f"\n=== {raw}")
    try:
        target = normalize(raw)
    except UnsupportedURL as e:
        print(f"  입력 오류: {e}")
        return
    print(f"  사이트: {target.site.name} / 정규화: {target.url}")
    for fetcher in (http, browser):
        if fetcher is None:
            continue
        stem = OUT_DIR / f"{target.site.key}_{_safe(target.ident)}_{fetcher.name}"
        try:
            html, final = await fetcher.fetch(target.url, target.site)
        except FetchError as e:
            print(f"  [{fetcher.name}] 수집 실패: {e}")
            continue
        Path(f"{stem}.html").write_text(html, encoding="utf-8")  # with_suffix는 이름 속 점(도메인)을 확장자로 오인한다
        pages = {"main": html}
        body_url = target.extra.get("body_url")
        if body_url:
            try:
                pages["body"], _ = await fetcher.fetch(body_url, target.site)
                Path(f"{stem}_body.html").write_text(pages["body"], encoding="utf-8")
            except FetchError as e:
                print(f"  [{fetcher.name}] 본문 페이지 수집 실패: {e}")
        print(f"  [{fetcher.name}] 저장: {stem}.html ({len(html):,}자, 최종 주소 {final})")
        try:
            p = parse_pages(target, pages)
        except ParseError as e:
            print(f"  [{fetcher.name}] 파싱 실패: {e}")
            continue
        print(f"    공고명: {p.title}")
        print(f"    경력: {p.career_raw} -> {p.career_select} / 마감일: {p.deadline_raw} -> {p.deadline}")
        print(f"    근무지: {p.location} / 고용 형태: {p.employment_type}")
        print(f"    본문 구간: {', '.join(f'{k} {len(v)}개' for k, v in p.sections.items()) or '없음'}")


async def main(urls: list[str]) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    settings = get_settings()
    browser = BrowserFetcher() if settings.use_playwright else None
    async with httpx.AsyncClient(timeout=settings.http_timeout_sec) as client:
        http = HttpFetcher(client)
        try:
            for u in urls:
                await snapshot(u, http, browser)
        finally:
            if browser is not None:
                await browser.close()
    print(f"\n저장 위치: {OUT_DIR.resolve()}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    asyncio.run(main(sys.argv[1:]))
