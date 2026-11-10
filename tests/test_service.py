"""가짜 수집기·가짜 노션으로 처리 흐름 검증 (네트워크 없음)."""

import asyncio

import httpx

from app.collection.fetcher import BlockedTarget, FetchError
from dataclasses import replace

from app.models import JobStatus
from app.notion.pages import normalize_title, page_url
from app.collection.collector import Collector
from app.collection.urls import normalize
from app.service import JobService, Screening
from tests.helpers import load

LIVE = load("saramin_55434315.html")
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
        self.company_creates = 0

    async def find_by_url(self, target):
        await asyncio.sleep(0.01)
        for p in reversed(self.pages):
            if p["url_prop"] == target.url:
                return p
        return None

    async def find_or_create_company(self, name):
        if name not in self.companies:
            self.company_creates += 1
        return self.companies.setdefault(name, f"company-{len(self.companies) + 1}")

    async def find_company(self, name):
        return self.companies.get(name)

    async def find_by_title(self, p, company_page_id):
        if company_page_id is None:
            return None
        for page in self.pages:
            if page["company"] == company_page_id and normalize_title(page["title"], p.company) == normalize_title(p.title, p.company):
                return page
        return None

    async def create_posting(self, posting, company_id):
        self.create_calls += 1
        await asyncio.sleep(0.01)
        page = {"id": f"page-{len(self.pages) + 1}", "url": f"https://notion.so/p{len(self.pages) + 1}",
                "url_prop": posting.url, "title": posting.title, "company": company_id,
                "career": posting.career_select}
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
    assert job.company and job.to_dict()["company"] == job.company


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

JK_MAIN = load("jobkorea_49874788.html")
JK_BODY = load("jobkorea_49874788_body.html")


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
    jss = load("jasoseol_107042.html")
    catch = load("catch_578555.html")
    browser = UrlFetcher({}, name="playwright")
    catch_body = load("catch_578555_body.html")
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
    expired = load("saramin_54220010_expired.html")
    svc = make(FakeFetcher(expired), store)
    job = svc.submit("https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=54220010")
    await svc.wait_idle()
    assert job.status is JobStatus.SAVED and "마감된 공고" in job.message


async def test_store_swapped_mid_job_keeps_using_original_store():
    old, new = FakeStore(), FakeStore()
    svc = make(FakeFetcher(LIVE, delay=0.05), old)
    job = svc.submit(URL)
    await asyncio.sleep(0.02)  # 수집 중에 노션 연결 교체
    svc.store = new
    await svc.wait_idle()
    assert job.status is JobStatus.SAVED
    assert len(old.pages) == 1 and not new.pages and not new.companies


# ---------------------------------------------------------------- 자동 수집 경로 (run_one)

CLOSED = {"properties": {"지원 단계": {"type": "status", "status": {"name": "⏹️ 종료"}}}}


def auto(store, screen=lambda p: (p, None), dry_run=False, fetcher=None):
    """가짜 수집기를 쓰는 Screening을 만든다."""
    return Screening(screen, Collector(fetcher or FakeFetcher(LIVE), None), dry_run)


async def test_run_one_skipped_not_saved():
    store = FakeStore()
    svc = make(FakeFetcher(LIVE), store)
    job = await svc.run_one(URL, auto(store, screen=lambda p: (p, "근무지 제외: 부산")))
    assert job.status is JobStatus.SKIPPED and job.message == "근무지 제외: 부산"
    assert store.create_calls == 0 and not svc.jobs


async def test_run_one_saves_with_adjusted_posting():
    store = FakeStore()
    svc = make(FakeFetcher(LIVE), store)
    job = await svc.run_one(URL, auto(store, screen=lambda p: (replace(p, career_select="2년 이상"), None)))
    assert job.status is JobStatus.SAVED
    assert store.pages[0]["career"] == "2년 이상"


async def test_run_one_title_duplicate_is_exists():
    store = FakeStore()
    svc = make(FakeFetcher(LIVE), store)
    first = await svc.run_one(URL, auto(store))
    store.pages[0]["url_prop"] = "https://other.example/1"  # 같은 공고가 다른 URL로 올라와 있는 상황
    job = await svc.run_one(URL, auto(store))
    assert first.status is JobStatus.SAVED
    assert job.status is JobStatus.EXISTS and job.message == "같은 기업·공고명 항목이 이미 있음"
    assert store.create_calls == 1 and job.notion_url == "https://notion.so/p1"


async def test_run_one_dry_run_writes_nothing():
    store = FakeStore()
    svc = make(FakeFetcher(LIVE), store)
    job = await svc.run_one(URL, auto(store, dry_run=True))
    assert job.status is JobStatus.PREVIEW and job.message == "미리보기: 저장하지 않음"
    assert store.create_calls == 0 and store.company_creates == 0


async def test_run_one_exists_closed_suffix():
    store = FakeStore()
    store.pages.append({"id": "old", "url": "https://notion.so/old", "url_prop": CANON, **CLOSED})
    svc = make(FakeFetcher(LIVE), store)
    job = await svc.run_one(URL, auto(store))
    assert job.status is JobStatus.EXISTS and job.message.endswith(" (종료)")


async def test_run_one_uses_screening_collector():
    store = FakeStore()
    default = FakeFetcher(error="x")
    svc = make(default, store)
    job = await svc.run_one(URL, auto(store))
    assert job.status is JobStatus.SAVED and default.calls == 0


async def test_run_one_invalid_url():
    svc = make(FakeFetcher(LIVE), FakeStore())
    job = await svc.run_one("http://127.0.0.1:8000/", auto(FakeStore()))
    assert job.status is JobStatus.INVALID and not svc.jobs


async def test_submit_unchanged_by_screening_rules():
    class Harsh(Collector):
        """선별 기준에 걸릴 공고(부산, 3년 이상)를 돌려주는 수집기."""

        async def collect(self, target):
            return replace(await super().collect(target), location="부산 해운대구", career_select="3년 이상")

    store = FakeStore()
    svc = JobService(Harsh(FakeFetcher(LIVE), None), store)
    job = svc.submit(URL)
    await svc.wait_idle()
    assert job.status is JobStatus.SAVED and store.pages[0]["career"] == "3년 이상"  # 수동 경로는 선별하지 않는다
    assert list(svc.jobs.values()) == [job]


def test_status_group():
    S = JobStatus
    assert {s: s.group for s in S} == {
        S.SAVED: "saved", S.PARTIAL: "saved", S.PREVIEW: "saved", S.EXISTS: "exists", S.SKIPPED: "skipped",
        S.FETCH_FAILED: "failed", S.SAVE_FAILED: "failed", S.INVALID: "failed", S.PROCESSING: "processing"}
    assert S.SKIPPED.css == "skipped" and S.PREVIEW.css == "preview"


async def test_collector_skips_catch_body_only_when_configured():
    """자동 수집용 Collector(skip_body_sites)는 캐치 /controls 본문을 요청하지 않고, 기본 Collector는 요청한다."""
    target = normalize("https://www.catch.co.kr/NCS/RecruitInfoDetails/578555")
    page = load("catch_578555.html")
    auto, manual = FakeFetcher(page), FakeFetcher(page)
    await Collector(auto, None, skip_body_sites=("catch",)).collect(target)
    await Collector(manual, None).collect(target)
    assert auto.urls == [target.url]
    assert manual.urls == [target.url, "https://www.catch.co.kr/controls/recruitDetail/578555"]
