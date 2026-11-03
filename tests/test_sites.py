"""2026-11 실제 공고 HTML 샘플로 사이트별 파서 검증 + 범용 파서·URL 정규화·차단 화면 검사."""

from datetime import date
from pathlib import Path

import pytest

from app.fetcher import decode_html, is_public_host, looks_like_challenge
from app.parsers.errors import ParseError
from app.parsers.sites import parse_pages
from app.urls import (
    CATCH, GENERIC, GREETING, INCRUIT, JASOSEOL, JOBKOREA, JUMPIT, WANTED, UnsupportedURL, normalize, same_posting,
)

FIX = Path(__file__).parent / "fixtures"


def rd(name: str, enc: str | None = None) -> str:
    return decode_html((FIX / name).read_bytes(), enc)


# ---------------------------------------------------------------- URL 정규화

@pytest.mark.parametrize("raw,site,url", [
    ("https://www.jobkorea.co.kr/Recruit/GI_Read/49874788?rPageCode=SL&logpath=21&sn=6&sc=611",
     JOBKOREA, "https://www.jobkorea.co.kr/Recruit/GI_Read/49874788"),
    ("https://www.jobkorea.co.kr/Recruit/GI_Read_Comt_Ifrm?Gno=49874788",
     JOBKOREA, "https://www.jobkorea.co.kr/Recruit/GI_Read/49874788"),
    ("https://m.jobkorea.co.kr/Recruit/GI_Read/49874788", JOBKOREA, "https://www.jobkorea.co.kr/Recruit/GI_Read/49874788"),
    ("https://www.wanted.co.kr/wd/394267?utm_source=x#apply", WANTED, "https://www.wanted.co.kr/wd/394267"),
    ("https://job.incruit.com/jobdb_info/jobpost.asp?job=2610210000297&src=gsw*search",
     INCRUIT, "https://job.incruit.com/jobdb_info/jobpost.asp?job=2610210000297"),
    ("https://m.catch.co.kr/NCS/RecruitInfoDetails/578700", CATCH, "https://www.catch.co.kr/NCS/RecruitInfoDetails/578700"),
    ("https://jasoseol.com/recruit/91234?from=main", JASOSEOL, "https://jasoseol.com/recruit/91234"),
    ("https://jumpit.saramin.co.kr/position/55351984?sort=rsp_rate", JUMPIT, "https://jumpit.saramin.co.kr/position/55351984"),
    ("https://cashwalk12.career.greetinghr.com/ko/o/187921?utm_source=x", GREETING,
     "https://cashwalk12.career.greetinghr.com/ko/o/187921"),
    ("https://recruit.i-sens.com/job_posting/DFUxhzD4?utm_source=linkedin&utm_medium=social&page=2#top",
     GENERIC, "https://recruit.i-sens.com/job_posting/DFUxhzD4?page=2"),
])
def test_normalize_sites(raw, site, url):
    n = normalize(raw)
    assert n.site is site and n.url == url


def test_body_url_for_split_sites():
    assert normalize("https://www.jobkorea.co.kr/Recruit/GI_Read/1").extra["body_url"].endswith("GI_Read_Comt_Ifrm?Gno=1")
    assert "jobpostcont.asp?job=2" in normalize("https://job.incruit.com/jobdb_info/jobpost.asp?job=2").extra["body_url"]


def test_same_posting_detects_other_id():
    t = normalize("https://www.wanted.co.kr/wd/1")
    assert same_posting(t, "https://www.wanted.co.kr/wd/1")
    assert not same_posting(t, "https://www.wanted.co.kr/wd/2")
    assert same_posting(t, "https://www.wanted.co.kr/login?next=/wd/1")  # 상세가 아닌 곳은 파서가 판단


# ---------------------------------------------------------------- 사이트별 파서

def test_jobkorea_full():
    t = normalize("https://www.jobkorea.co.kr/Recruit/GI_Read/49874788")
    p = parse_pages(t, {"main": rd("jobkorea_49874788.html"), "body": rd("jobkorea_49874788_body.html")})
    assert p.title == "[㈜넥슨] [슈터본부] 슈터플랫폼실 데이터 엔지니어"
    assert p.company == "㈜넥슨"
    assert p.career_select == "경력무관"
    assert p.deadline is None and p.deadline_raw == "상시채용"  # JSON-LD의 먼 미래 날짜를 쓰지 않음
    assert p.location.startswith("경기 성남시")
    assert set(p.sections) == {"주요 업무", "자격 요건", "우대 사항", "전형 절차"}


def test_jobkorea_image_body_is_partial_and_ignores_summary_area():
    t = normalize("https://www.jobkorea.co.kr/Recruit/GI_Read/50161925")
    p = parse_pages(t, {"main": rd("jobkorea_50161925.html"), "body": rd("jobkorea_50161925_body.html")})
    assert p.title == "[㈜에코브레인] Python 개발자 경력직 채용 (TECH 센터)"
    assert p.career_select == "2년 이상"
    assert p.deadline == date(2026, 11, 12)
    assert not p.body_found  # 공고 페이지의 요약 영역을 본문으로 오인하지 않음


def test_wanted():
    t = normalize("https://www.wanted.co.kr/wd/394267")
    p = parse_pages(t, {"main": rd("wanted_394267.html")})
    assert p.title == "[로민] ML 엔지니어"
    assert p.career_select == "신입/경력" and p.career_raw == "신입·경력 0~10년"
    assert p.deadline == date(2026, 11, 22)
    assert p.employment_type == "정규직"
    assert p.location == "서울 서초구 사임당로 32, 5층"
    assert p.sections["전형 절차"][0].startswith("서류전형")
    assert "Python 및 딥러닝 프레임워크" in " ".join(p.sections["자격 요건"])


def test_jumpit():
    t = normalize("https://jumpit.saramin.co.kr/position/55351984")
    p = parse_pages(t, {"main": rd("jumpit_55351984.html")})
    assert p.title == "[펜타시큐리티] 보안 네트워크 개발자(경력)"
    assert p.career_raw == "경력 10~20년" and p.career_select == "3년 이상"
    assert p.deadline == date(2026, 11, 20)
    assert p.location == "서울 영등포구 여의공원로115, 8층, 9층"
    assert p.sections["자격 요건"] == ["Linux Kernel 및 C 개발 경력", "TCP/IP 기반 지식 보유", "Linux 환경 개발 경험"]


def test_incruit_euc_kr():
    t = normalize("https://job.incruit.com/jobdb_info/jobpost.asp?job=2610210000297")
    p = parse_pages(t, {"main": rd("incruit_2610210000297.html", "euc-kr"),
                        "body": rd("incruit_2610210000297_body.html", "euc-kr")})
    assert p.title == "[한국전자기술연구원] 2026년 정규직 공개채용 공고"
    assert p.career_select == "경력무관"
    assert p.employment_type == "정규직"
    assert p.deadline == date(2026, 11, 5)
    assert "고용형태별" not in " ".join(sum(p.sections.values(), []))  # 메뉴 문구를 본문으로 오인하지 않음


def test_greeting():
    t = normalize("https://cashwalk12.career.greetinghr.com/ko/o/187921")
    p = parse_pages(t, {"main": rd("greeting_187921.html")})
    assert p.title == "[넛지헬스케어] [캐시워크애즈] 프론트엔드개발 산업기능요원 모집"
    assert p.career_select == "경력무관"
    assert p.employment_type == "병역특례"
    assert p.location.startswith("서울특별시 강남구")
    assert p.deadline is None
    assert p.sections["주요 업무"]


def test_greeting_on_custom_domain_detected():
    t = normalize("https://careers.example.com/ko/o/187921")
    assert t.site is GENERIC
    p = parse_pages(t, {"main": rd("greeting_187921.html")})
    assert p.company == "넛지헬스케어" and p.source.endswith("(그리팅)")


# ---------------------------------------------------------------- 범용 파서

GENERIC_HTML = """<!doctype html><html><head>
<title>[신입] CGMS 알고리즘 개발 채용(~11/11) | (주)아이센스</title>
<meta property="og:title" content="[신입] CGMS 알고리즘 개발 채용(~11/11) | (주)아이센스">
<meta property="og:site_name" content="[신입] CGMS 알고리즘 개발 채용(~11/11) | (주)아이센스">
</head><body><header><nav>회사 소개 지원하기 채용 FAQ</nav></header>
<main><h1>[신입] CGMS 알고리즘 개발 채용(~11/11)</h1><p>서울(서초) #연구개발 신입 정규직</p>
<h2>ㅣ 모집 부문 및 상세 내용ㅣ</h2>
<p><strong>[ 주요 업무 ]</strong></p><ul><li>CGMS 생체신호처리 알고리즘 연구 및 개발</li>
<li>머신러닝 기반 모델 설계 및 최적화</li></ul>
<p><strong>[ 자격 요건 ]</strong></p><ul><li>석사 학위 이상 소지자</li><li>MATLAB, Python, C 중 1개 이상 언어 능숙자</li></ul>
<p><strong>[ 우대 사항 ]</strong></p><ul><li>시계열 데이터 신호처리 관련 연구 경험</li></ul>
<h2>ㅣ 근무조건 및 환경ㅣ</h2><ul><li>근무형태 : 정규직(수습기간 약 3개월)</li><li>근무지 : 아이센스 서초 본사</li></ul>
<h2>ㅣ 접수 기간 및 절차ㅣ</h2><ul><li>2026년 11월 11일(수) 23:59 까지</li></ul>
<p>서류전형 → 종합역량검사 → 대면면접(1차/2차) → 최종합격</p>
</main><footer>Created by</footer></body></html>"""


def test_generic_company_site():
    t = normalize("https://recruit.i-sens.com/job_posting/DFUxhzD4")
    p = parse_pages(t, {"main": GENERIC_HTML})
    assert p.company == "(주)아이센스"
    assert p.title == "[(주)아이센스] [신입] CGMS 알고리즘 개발 채용(~11/11)"
    assert p.sections["주요 업무"] == ["CGMS 생체신호처리 알고리즘 연구 및 개발", "머신러닝 기반 모델 설계 및 최적화"]
    assert p.sections["자격 요건"][0] == "석사 학위 이상 소지자"
    assert p.employment_type and p.employment_type.startswith("정규직")
    assert "회사 소개" not in " ".join(sum(p.sections.values(), []))


def test_generic_jsonld_preferred():
    html = """<html><head><title>Careers</title><script type="application/ld+json">
    {"@context":"https://schema.org","@graph":[{"@type":"Organization","name":"X"},
     {"@type":"JobPosting","title":"Data Engineer","hiringOrganization":{"name":"Acme"},
      "validThrough":"2026-12-01T14:59:59Z","employmentType":"FULL_TIME",
      "experienceRequirements":{"@type":"OccupationalExperienceRequirements","monthsOfExperience":24},
      "description":"<p>Responsibilities</p><ul><li>Build pipelines</li></ul><p>Requirements</p><ul><li>Python</li></ul>"}]}
    </script></head><body><main>no text</main></body></html>"""
    p = parse_pages(normalize("https://jobs.acme.example/123"), {"main": html})
    assert p.title == "[Acme] Data Engineer"
    assert p.deadline == date(2026, 12, 1)  # UTC 14:59 → 한국 시간 23:59
    assert p.career_select == "2년 이상"
    assert p.employment_type == "정규직"
    assert p.sections == {"주요 업무": ["Build pipelines"], "자격 요건": ["Python"]}


@pytest.mark.parametrize("url", [
    "https://jasoseol.com/recruit/1", "https://www.catch.co.kr/NCS/RecruitInfoDetails/1",
])
def test_unverified_sites_reject_site_default_title(url):
    shell = "<html><head><title>자소설닷컴</title><meta property='og:title' content='캐치'></head><body></body></html>"
    with pytest.raises(ParseError):
        parse_pages(normalize(url), {"main": shell})


def test_generic_rendered_page_uses_h1_and_body():
    html = """<html><head><title>Careers</title></head><body><main>
    <h1>백엔드 엔지니어 (Python)</h1><dl><dt>경력</dt><dd>신입 ~ 3년</dd><dt>마감일</dt><dd>2026-12-31</dd></dl>
    <h3>주요 업무</h3><ul><li>FastAPI 서버 개발</li></ul><h3>자격 요건</h3><ul><li>Python</li></ul></main></body></html>"""
    p = parse_pages(normalize("https://recruit.example-corp.com/jobs/1"), {"main": html})
    assert p.raw_title == "백엔드 엔지니어 (Python)"
    assert p.career_select == "신입/경력" and p.deadline == date(2026, 12, 31)
    assert p.sections["주요 업무"] == ["FastAPI 서버 개발"]


# ---------------------------------------------------------------- 차단 화면, 내부망

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
    import pytest
    from app.fetcher import BlockedTarget, check_target
    from app.urls import GENERIC
    with pytest.raises(BlockedTarget):
        await check_target("http://127.0.0.1/careers", GENERIC)


async def test_http_redirect_to_internal_is_blocked_before_request():
    import httpx
    import pytest
    from app.fetcher import BlockedTarget, HttpFetcher
    from app.urls import GENERIC

    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/admin"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BlockedTarget):
            await HttpFetcher(client).fetch("http://8.8.8.8/jobs/1", GENERIC)
    assert seen == ["http://8.8.8.8/jobs/1"]  # 내부 주소로는 요청하지 않음


def test_decode_html_meta_charset():
    raw = '<html><head><meta charset="euc-kr"></head><body>경력무관</body></html>'.encode("cp949")
    assert "경력무관" in decode_html(raw, None)


async def test_http_fetcher_retries_once_on_dropped_connection():
    import httpx

    from app.fetcher import HttpFetcher
    from app.urls import INCRUIT

    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected")
        return httpx.Response(200, content="<html><title>ok</title></html>".encode(),
                              headers={"content-type": "text/html; charset=euc-kr"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        html, final = await HttpFetcher(c).fetch("https://job.incruit.com/jobdb_info/jobpost.asp?job=1", INCRUIT)
    assert "ok" in html and calls["n"] == 2


async def test_http_fetcher_blocks_redirect_to_other_host():
    import httpx

    from app.fetcher import FetchError, HttpFetcher
    from app.urls import WANTED

    def handler(req):
        return httpx.Response(302, headers={"location": "https://evil.example.com/"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(FetchError):
            await HttpFetcher(c).fetch("https://www.wanted.co.kr/wd/1", WANTED)


async def test_http_fetcher_challenge_page_fails():
    import httpx

    from app.fetcher import FetchError, HttpFetcher
    from app.urls import GENERIC as G

    page = (FIX / "vercel_checkpoint.html").read_bytes()

    def handler(req):
        return httpx.Response(200, content=page, headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(FetchError, match="봇 확인"):
            await HttpFetcher(c).fetch("https://8.8.8.8/job", G)


def test_generic_non_job_page_rejected():
    html = "<html><head><title>오늘의 뉴스</title></head><body><main><h1>오늘의 뉴스</h1><p>날씨 맑음</p></main></body></html>"
    with pytest.raises(ParseError, match="채용공고"):
        parse_pages(normalize("https://news.example.com/article/1"), {"main": html})


def test_generic_image_only_job_page_is_partial():
    html = "<html><head><title>2026 하반기 신입사원 채용</title></head><body><main><img src='a.png'></main></body></html>"
    p = parse_pages(normalize("https://corp.example.com/notice/77"), {"main": html})
    assert p.raw_title == "2026 하반기 신입사원 채용" and not p.body_found


@pytest.mark.parametrize("url", [
    "https://career.programmers.co.kr/job_positions/12345",
    "https://programmers.co.kr/job_positions/1",
])
def test_programmers_excluded(url):
    with pytest.raises(UnsupportedURL, match="프로그래머스"):
        normalize(url)


def test_catch_like_summary_and_vague_jsonld_career():
    """캐치 저장 결과에서 본 문제: JSON-LD 경력이 '경력'뿐이고, 요약 영역이 자격 요건으로 들어감."""
    html = """<html><head><title>캐치</title><script type="application/ld+json">
    {"@type":"JobPosting","title":"[NHN PAYCO] 데이터 엔지니어 채용","hiringOrganization":{"name":"NHN페이코"},
     "experienceRequirements":"경력","employmentType":"FULL_TIME"}</script></head>
    <body><main><h1>[NHN PAYCO] 데이터 엔지니어 채용</h1>
    <h3>지원자격</h3><ul><li>경력 : 경력(3~5년)</li><li>학력 : 학력무관</li>
    <li>모집직무 : 데이터 엔지니어, 빅데이터 엔지니어, 데이터분석</li></ul>
    <h3>접수기간</h3><p>~11/30(일) 23:59</p></main></body></html>"""
    p = parse_pages(normalize("https://www.catch.co.kr/NCS/RecruitInfoDetails/578555"), {"main": html})
    assert p.career_select == "3년 이상" and p.career_raw == "경력(3~5년)"
    assert "자격 요건" not in p.sections
    assert p.deadline is not None and (p.deadline.month, p.deadline.day) == (11, 30)


@pytest.mark.parametrize("raw,today,expected", [
    ("~11/30(일) 23:59", date(2026, 11, 3), date(2026, 11, 30)),
    ("1.15", date(2026, 11, 3), date(2027, 1, 15)),
    ("2026.12.01", date(2026, 11, 3), date(2026, 12, 1)),
    ("상시채용", date(2026, 11, 3), None),
])
def test_deadline_without_year(raw, today, expected):
    from app.parsers.common import parse_deadline
    assert parse_deadline(raw, today=today) == expected


def test_real_requirement_items_with_colons_kept():
    from app.parsers.generic import drop_summary_sections
    items = {"우대 사항": ["전공: 컴퓨터/시스템공학", "자격증: 정보처리기사"], "자격 요건": ["경력 : 신입", "학력 : 대졸"]}
    out = drop_summary_sections(items)
    assert "우대 사항" in out and "자격 요건" not in out


async def test_browser_close_ignores_dead_connection():
    from app.fetcher import BrowserFetcher

    class Dead:
        async def close(self):
            raise RuntimeError("Browser.close: the handler is closed")

        async def stop(self):
            raise RuntimeError("closed")

    b = BrowserFetcher(enabled=True)
    b._browser, b._pw = Dead(), Dead()
    await b.close()  # 예외 없이 끝나야 함
    await b.close()  # 두 번 불러도 안전


async def test_browser_relaunched_after_crash(monkeypatch):
    import playwright.async_api
    from app.fetcher import BrowserFetcher

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
    b = BrowserFetcher(enabled=True)
    dead, old_pw = FakeBrowser(False), FakePW()
    b._browser, b._pw = dead, old_pw
    browser = await b._ensure_browser()
    assert browser is not dead and browser.is_connected()
    assert dead.closed and old_pw.stopped
    assert await b._ensure_browser() is browser  # 살아 있으면 재사용


# ---------------------------------------------------------------- 자소설닷컴, 캐치 (2026-11-03 집 PC에서 받은 HTML)

def test_jasoseol():
    t = normalize("https://jasoseol.com/recruit/107042")
    p = parse_pages(t, {"main": rd("jasoseol_107042.html")})
    assert p.title == "[NHN Cloud] 인프라 데브옵스 엔지니어"
    assert p.career_raw == "경력 5년 이상" and p.career_select == "3년 이상"
    assert p.deadline == date(2027, 1, 1)
    assert p.employment_type == "정규직"
    assert not p.body_found  # 본문이 이미지 한 장인 공고


def test_jasoseol_rendered_page_does_not_mix_recommended_posting():
    t = normalize("https://jasoseol.com/recruit/107042")
    p = parse_pages(t, {"main": rd("jasoseol_107042_rendered.html")})
    assert p.title == "[NHN Cloud] 인프라 데브옵스 엔지니어"
    assert "코리안리" not in (p.location or "") + (p.career_raw or "") + p.title
    assert p.location is None and p.career_select is None


def test_catch():
    t = normalize("https://www.catch.co.kr/NCS/RecruitInfoDetails/578555")
    assert t.extra["body_url"] == "https://www.catch.co.kr/controls/recruitDetail/578555"
    p = parse_pages(t, {"main": rd("catch_578555.html")})
    assert p.title == "[NHN페이코] [NHN PAYCO] 데이터 엔지니어 채용"
    assert p.career_raw == "경력(3~5년)" and p.career_select == "3년 이상"
    assert p.deadline is None and p.deadline_raw == "채용시 마감"
    assert p.location == "서울 구로구 디지털로26길 72" and p.employment_type == "정규직"
    assert not p.sections  # 요약 영역을 본문으로 넣지 않음(본문은 별도 페이지)


def test_catch_rendered_fallback_keeps_company():
    t = normalize("https://www.catch.co.kr/NCS/RecruitInfoDetails/578555")
    p = parse_pages(t, {"main": rd("catch_578555_rendered.html")})
    assert p.title == "[NHN페이코] [NHN PAYCO] 데이터 엔지니어 채용"


def test_catch_body_page_sections():
    body = """<html><body><div class="recruit_detail"><h3>담당업무</h3><ul><li>데이터 파이프라인 구축</li></ul>
    <h3>자격요건</h3><ul><li>Python, SQL 활용 경험</li></ul><h3>우대사항</h3><ul><li>Spark 경험</li></ul></div></body></html>"""
    t = normalize("https://www.catch.co.kr/NCS/RecruitInfoDetails/578555")
    p = parse_pages(t, {"main": rd("catch_578555.html"), "body": body})
    assert p.sections["주요 업무"] == ["데이터 파이프라인 구축"] and p.body_found


def test_catch_real_image_body_is_partial():
    t = normalize("https://www.catch.co.kr/NCS/RecruitInfoDetails/578555")
    p = parse_pages(t, {"main": rd("catch_578555.html"), "body": rd("catch_578555_body.html")})
    assert p.career_select == "3년 이상" and not p.body_found


# ---------------------------------------------------------------- 근무지 주소 합치기

@pytest.mark.parametrize("region,locality,street,expected", [
    ("서울", "구로구", "디지털로26길 72", "서울 구로구 디지털로26길 72"),
    ("경기", "성남시", "분당구 대왕판교로645번길 16", "경기 성남시 분당구 대왕판교로645번길 16"),
    ("경기도", "성남시", "경기도 성남시 분당구 새나리로 25 (야탑동)", "경기도 성남시 분당구 새나리로 25 (야탑동)"),
    ("서울", "서초구", "서울 서초구 사임당로 32, 5층", "서울 서초구 사임당로 32, 5층"),
    ("서울특별시", "강남구", "서울 강남구 테헤란로 1", "서울 강남구 테헤란로 1"),
    ("경기", "성남시", None, "경기 성남시"),
    (None, None, "경기 성남시 분당구 판교로256번길 7", "경기 성남시 분당구 판교로256번길 7"),
])
def test_join_address(region, locality, street, expected):
    from app.parsers.common import join_address
    assert join_address(region, locality, street) == expected


def test_sites_location_with_region():
    jss = parse_pages(normalize("https://jasoseol.com/recruit/107042"), {"main": rd("jasoseol_107042.html")})
    assert jss.location == "경기 성남시 분당구 대왕판교로645번길 16"
    catch = parse_pages(normalize("https://www.catch.co.kr/NCS/RecruitInfoDetails/578555"), {"main": rd("catch_578555.html")})
    assert catch.location == "서울 구로구 디지털로26길 72"
