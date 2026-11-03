"""범용 파서(기업 자체 채용 사이트)."""

from datetime import date

import pytest

from app.parsers.errors import ParseError
from app.parsers.generic import drop_summary_sections
from app.parsers.dispatch import parse_pages
from app.collection.urls import normalize


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
    assert "회사 소개" not in " ".join(x for v in p.sections.values() for x in v)


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


def test_generic_rendered_page_uses_h1_and_body():
    html = """<html><head><title>Careers</title></head><body><main>
    <h1>백엔드 엔지니어 (Python)</h1><dl><dt>경력</dt><dd>신입 ~ 3년</dd><dt>마감일</dt><dd>2026-12-31</dd></dl>
    <h3>주요 업무</h3><ul><li>FastAPI 서버 개발</li></ul><h3>자격 요건</h3><ul><li>Python</li></ul></main></body></html>"""
    p = parse_pages(normalize("https://recruit.example-corp.com/jobs/1"), {"main": html})
    assert p.raw_title == "백엔드 엔지니어 (Python)"
    assert p.career_select == "신입/경력" and p.deadline == date(2026, 12, 31)
    assert p.sections["주요 업무"] == ["FastAPI 서버 개발"]


def test_generic_non_job_page_rejected():
    html = "<html><head><title>오늘의 뉴스</title></head><body><main><h1>오늘의 뉴스</h1><p>날씨 맑음</p></main></body></html>"
    with pytest.raises(ParseError, match="채용공고"):
        parse_pages(normalize("https://news.example.com/article/1"), {"main": html})


def test_generic_image_only_job_page_is_partial():
    html = "<html><head><title>2026 하반기 신입사원 채용</title></head><body><main><img src='a.png'></main></body></html>"
    p = parse_pages(normalize("https://corp.example.com/notice/77"), {"main": html})
    assert p.raw_title == "2026 하반기 신입사원 채용" and not p.body_found


def test_real_requirement_items_with_colons_kept():
    items = {"우대 사항": ["전공: 컴퓨터/시스템공학", "자격증: 정보처리기사"], "자격 요건": ["경력 : 신입", "학력 : 대졸"]}
    out = drop_summary_sections(items)
    assert "우대 사항" in out and "자격 요건" not in out
