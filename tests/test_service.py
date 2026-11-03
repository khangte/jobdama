"""가짜 수집기·가짜 노션으로 처리 흐름 검증 (네트워크 없음)."""

import asyncio
from pathlib import Path

import httpx

from app.fetcher import BlockedTarget, FetchError
from app.models import JobStatus
from app.notion_store import page_url
from app.service import Collector, JobService

FIX = Path(__file__).parent / "fixtures"
LIVE = (FIX / "saramin_55434315.html").read_text(encoding="utf-8")
URL = "https://www.saramin.co.kr/zf_user/jobs/relay/view?isMypage=no&rec_idx=55434315&recommend_ids=abc"
CANON = "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=55434315"


class FakeFetcher:
    def __init__(self, html=None, error=None, final_url=None, delay=0.0, name="httpx"):
        self.html, self.error, self.final_url, self.delay, self.name = html, error, final_url, delay, name
        self.calls = 0
        self.urls: list[str] = []

    async def fetch(self, url, site):
        self.urls.append(url)
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error:
            raise FetchError(self.error)
        return self.html, self.final_url or url


class FakeStore:
    def __init__(self, fail_create_once=False, create_lands_anyway=False):
        self.pages: list[dict] = []
        self.companies: dict[str, str] = {}
        self.fail_create_once = fail_create_once
        self.create_lands_anyway = create_lands_anyway
        self.create_calls = 0

    async def find_by_url(self, target):
        await asyncio.sleep(0.01)
        for p in reversed(self.pages):
            if p["url_prop"] == target.url:
                return p
        return None

    async def find_or_create_company(self, name):
        return self.companies.setdefault(name, f"company-{len(self.companies) + 1}")

    async def create_posting(self, posting, company_id):
        self.create_calls += 1
        await asyncio.sleep(0.01)
        page = {"id": f"page-{len(self.pages) + 1}", "url": f"https://notion.so/p{len(self.pages) + 1}",
                "url_prop": posting.url, "title": posting.title, "company": company_id}
        if self.fail_create_once:
            self.fail_create_once = False
            if self.create_lands_anyway:
                self.pages.append(page)
            raise httpx.ReadTimeout("cut")
        self.pages.append(page)
        return page


def make(http, store, browser=None):
    return JobService(Collector(http, browser), store)


async def test_saved():
    store = FakeStore()
    svc = make(FakeFetcher(LIVE), store)
    job = svc.submit(URL)
    await svc.wait_idle()
    assert job.status is JobStatus.SAVED
    assert job.normalized_url == CANON
    assert store.pages[0]["url_prop"] == CANON
    assert store.pages[0]["company"] == "company-1"
    assert job.notion_url == "https://notion.so/p1"


async def test_same_url_pasted_three_times_creates_one():
    store = FakeStore()
    svc = make(FakeFetcher(LIVE, delay=0.05), store)
    jobs = [svc.submit(URL) for _ in range(3)]
    await svc.wait_idle()
    statuses = sorted(j.status.value for j in jobs)
    assert statuses == sorted([JobStatus.SAVED.value, JobStatus.EXISTS.value, JobStatus.EXISTS.value])
    assert len(store.pages) == 1
    assert all(j.notion_url for j in jobs)


async def test_existing_url_skips_fetch():
    store = FakeStore()
    store.pages.append({"id": "old", "url": "https://notion.so/old", "url_prop": CANON})
    http = FakeFetcher(LIVE)
    svc = make(http, store)
    job = svc.submit(URL)
    await svc.wait_idle()
    assert job.status is JobStatus.EXISTS and job.notion_url == "https://notion.so/old"
    assert http.calls == 0


async def test_fetch_failed_both():
    svc = make(FakeFetcher(error="HTTP 403"), FakeStore(), FakeFetcher(error="브라우저 로딩 실패", name="playwright"))
    job = svc.submit(URL)
    await svc.wait_idle()
    assert job.status is JobStatus.FETCH_FAILED
    assert "httpx: HTTP 403" in job.message and "playwright" in job.message


async def test_block_page_is_fetch_failed():
    svc = make(FakeFetcher("<html><body>차단</body></html>"), FakeStore())
    job = svc.submit(URL)
    await svc.wait_idle()
    assert job.status is JobStatus.FETCH_FAILED


async def test_redirect_to_other_posting_is_fetch_failed():
    other = "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=43403012"
    svc = make(FakeFetcher(LIVE, final_url=other), FakeStore())
    job = svc.submit(URL)
    await svc.wait_idle()
    assert job.status is JobStatus.FETCH_FAILED and "다른 공고" in job.message


async def test_partial_when_body_missing_and_browser_fallback_used():
    html = LIVE
    start = html.index('<div class="user_content')
    end = html.index("</div>", start)
    image_only = html[:start] + '<div class="user_content jobsViewDetail_55434315"><img src="x">' + html[end:]
    browser = FakeFetcher(image_only, name="playwright")
    store = FakeStore()
    svc = make(FakeFetcher(image_only), store, browser)
    job = svc.submit(URL)
    await svc.wait_idle()
    assert browser.calls == 0  # 서버가 HTML을 다 주는 사이트라 브라우저로 다시 열지 않음
    assert job.status is JobStatus.PARTIAL
    assert len(store.pages) == 1


async def test_browser_used_when_http_blocked():
    browser = FakeFetcher(LIVE, name="playwright")
    svc = make(FakeFetcher(error="HTTP 403"), FakeStore(), browser)
    job = svc.submit(URL)
    await svc.wait_idle()
    assert browser.calls == 1 and job.status is JobStatus.SAVED


async def test_blocked_target_not_retried_in_browser():
    class Blocked(FakeFetcher):
        async def fetch(self, url, site):
            self.calls += 1
            raise BlockedTarget("내부망 주소로 연결되는 링크라 요청하지 않음")

    browser = FakeFetcher(LIVE, name="playwright")
    svc = make(Blocked(), FakeStore(), browser)
    job = svc.submit(URL)
    await svc.wait_idle()
    assert browser.calls == 0
    assert job.status is JobStatus.FETCH_FAILED and "내부망" in job.message


async def test_unexpected_browser_error_keeps_http_partial():
    class Crashing(FakeFetcher):
        async def fetch(self, url, site):
            self.calls += 1
            raise RuntimeError("page is navigating")

    url = "https://careers.example.com/jobs/1"
    shell = "<html><head><title>백엔드 개발자</title></head><body></body></html>"
    store = FakeStore()
    svc = make(FakeFetcher(shell), store, Crashing(name="playwright"))
    job = svc.submit(url)
    await svc.wait_idle()
    assert job.status is JobStatus.PARTIAL and len(store.pages) == 1


async def test_unexpected_error_on_last_fetcher_is_fetch_failed():
    class Crashing(FakeFetcher):
        async def fetch(self, url, site):
            raise RuntimeError("Target closed")

    svc = make(FakeFetcher(error="HTTP 403"), FakeStore(), Crashing(name="playwright"))
    job = svc.submit(URL)
    await svc.wait_idle()
    assert job.status is JobStatus.FETCH_FAILED and "playwright: RuntimeError" in job.message


async def test_create_timeout_but_page_landed_is_not_duplicated():
    store = FakeStore(fail_create_once=True, create_lands_anyway=True)
    svc = make(FakeFetcher(LIVE), store)
    job = svc.submit(URL)
    await svc.wait_idle()
    assert job.status is JobStatus.SAVED
    assert len(store.pages) == 1 and store.create_calls == 1


async def test_create_timeout_not_landed_retries_once():
    store = FakeStore(fail_create_once=True, create_lands_anyway=False)
    svc = make(FakeFetcher(LIVE), store)
    job = svc.submit(URL)
    await svc.wait_idle()
    assert job.status is JobStatus.SAVED
    assert len(store.pages) == 1 and store.create_calls == 2


async def test_invalid_input():
    svc = make(FakeFetcher(LIVE), FakeStore())
    job = svc.submit("https://www.jobkorea.co.kr/Search/?stext=python")
    assert job.status is JobStatus.INVALID
    job2 = svc.submit("http://127.0.0.1:8000/")
    assert job2.status is JobStatus.INVALID


async def test_notion_not_ready():
    store = FakeStore()
    svc = make(FakeFetcher(LIVE), store)
    svc.notion_ready = False
    job = svc.submit(URL)
    await svc.wait_idle()
    assert job.status is JobStatus.SAVE_FAILED and not store.pages


def test_page_url_fallback():
    assert page_url({"id": "ab-cd"}) == "https://www.notion.so/abcd"


# ---------------------------------------------------------------- 사이트별 수집 순서

JK_MAIN = (FIX / "jobkorea_49874788.html").read_text(encoding="utf-8")
JK_BODY = (FIX / "jobkorea_49874788_body.html").read_text(encoding="utf-8")


class UrlFetcher(FakeFetcher):
    def __init__(self, mapping, name="httpx"):
        super().__init__(name=name)
        self.mapping = mapping

    async def fetch(self, url, site):
        self.calls += 1
        self.urls.append(url)
        for key, html in self.mapping.items():
            if key in url:
                if isinstance(html, Exception):
                    raise html
                return html, url
        raise FetchError("HTTP 404")


async def test_jobkorea_fetches_body_page():
    http = UrlFetcher({"GI_Read_Comt_Ifrm": JK_BODY, "GI_Read/49874788": JK_MAIN})
    store = FakeStore()
    svc = make(http, store)
    job = svc.submit("https://www.jobkorea.co.kr/Recruit/GI_Read/49874788?rPageCode=SL")
    await svc.wait_idle()
    assert job.status is JobStatus.SAVED and job.site == "잡코리아"
    assert any("GI_Read_Comt_Ifrm?Gno=49874788" in u for u in http.urls)


async def test_jobkorea_body_page_failure_is_partial():
    http = UrlFetcher({"GI_Read_Comt_Ifrm": FetchError("HTTP 500"), "GI_Read/49874788": JK_MAIN})
    svc = make(http, FakeStore())
    job = svc.submit("https://www.jobkorea.co.kr/Recruit/GI_Read/49874788")
    await svc.wait_idle()
    assert job.status is JobStatus.PARTIAL


async def test_jobkorea_body_page_unexpected_error_is_partial():
    http = UrlFetcher({"GI_Read_Comt_Ifrm": RuntimeError("page is navigating"), "GI_Read/49874788": JK_MAIN})
    svc = make(http, FakeStore())
    job = svc.submit("https://www.jobkorea.co.kr/Recruit/GI_Read/49874788")
    await svc.wait_idle()
    assert job.status is JobStatus.PARTIAL


SPA_SHELL = "<html><head><title>Careers</title></head><body><div id='__next'></div></body></html>"
SPA_RENDERED = """<html><head><title>Careers</title></head><body><main><h1>데이터 엔지니어 신입</h1>
<dl><dt>경력</dt><dd>신입</dd></dl><h3>주요업무</h3><ul><li>ETL 개발</li></ul></main></body></html>"""


async def test_js_site_uses_browser_after_empty_shell():
    browser = UrlFetcher({"careers.example-corp.com": SPA_RENDERED}, name="playwright")
    svc = make(UrlFetcher({"careers.example-corp.com": SPA_SHELL}), FakeStore(), browser)
    job = svc.submit("https://careers.example-corp.com/jobs/91234")
    await svc.wait_idle()
    assert browser.calls == 1  # 자체 채용 사이트는 JS로 그릴 수 있어 브라우저로 다시 연다
    assert job.status is JobStatus.SAVED and job.title == "데이터 엔지니어 신입"


async def test_jasoseol_and_catch_not_reopened_in_browser():
    jss = (FIX / "jasoseol_107042.html").read_text(encoding="utf-8")
    catch = (FIX / "catch_578555.html").read_text(encoding="utf-8")
    browser = UrlFetcher({}, name="playwright")
    catch_body = (FIX / "catch_578555_body.html").read_text(encoding="utf-8")
    svc = make(UrlFetcher({"jasoseol.com": jss, "RecruitInfoDetails": catch, "recruitDetail": catch_body}),
               FakeStore(), browser)
    j1 = svc.submit("https://jasoseol.com/recruit/107042")
    j2 = svc.submit("https://www.catch.co.kr/NCS/RecruitInfoDetails/578555")
    await svc.wait_idle()
    assert browser.calls == 0  # 추천 공고가 섞이는 렌더링 페이지를 쓰지 않음
    assert j1.status is JobStatus.PARTIAL and "미검증" not in j1.message
    assert j2.status is JobStatus.PARTIAL and "미검증" not in j2.message  # 둘 다 본문이 이미지인 공고


async def test_js_site_without_browser_fails_cleanly():
    svc = make(UrlFetcher({"careers.example-corp.com": SPA_SHELL}), FakeStore(), None)
    job = svc.submit("https://careers.example-corp.com/jobs/91234")
    await svc.wait_idle()
    assert job.status is JobStatus.FETCH_FAILED


async def test_past_deadline_marked_expired():
    store = FakeStore()
    expired = (FIX / "saramin_54220010_expired.html").read_text(encoding="utf-8")
    svc = make(FakeFetcher(expired), store)
    job = svc.submit("https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=54220010")
    await svc.wait_idle()
    assert job.status is JobStatus.SAVED and "마감된 공고" in job.message
