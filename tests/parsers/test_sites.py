"""2026-11 실제 공고 HTML 샘플로 사이트별 파서 검증."""

from datetime import date

import pytest

from app.parsers.errors import ParseError
from app.parsers.dispatch import parse_pages
from app.collection.urls import GENERIC, normalize
from tests.helpers import rd


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
    assert "고용형태별" not in " ".join(x for v in p.sections.values() for x in v)  # 메뉴 문구를 본문으로 오인하지 않음


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


@pytest.mark.parametrize("url", [
    "https://jasoseol.com/recruit/1", "https://www.catch.co.kr/NCS/RecruitInfoDetails/1",
])
def test_unverified_sites_reject_site_default_title(url):
    shell = "<html><head><title>자소설닷컴</title><meta property='og:title' content='캐치'></head><body></body></html>"
    with pytest.raises(ParseError):
        parse_pages(normalize(url), {"main": shell})


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


def test_sites_location_with_region():
    jss = parse_pages(normalize("https://jasoseol.com/recruit/107042"), {"main": rd("jasoseol_107042.html")})
    assert jss.location == "경기 성남시 분당구 대왕판교로645번길 16"
    catch = parse_pages(normalize("https://www.catch.co.kr/NCS/RecruitInfoDetails/578555"), {"main": rd("catch_578555.html")})
    assert catch.location == "서울 구로구 디지털로26길 72"
