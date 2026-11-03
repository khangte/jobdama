from datetime import date

import pytest

from app.parsers.common import classify_career, extract_sections, format_title, parse_deadline
from app.urls import GENERIC, SARAMIN, UnsupportedURL, is_allowed_fetch_url, normalize

CANON = "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=54220010"


@pytest.mark.parametrize("raw", [
    "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=54220010",
    "https://www.saramin.co.kr/zf_user/jobs/relay/view?isMypage=no&rec_idx=54220010&recommend_ids=eJx%3D&view_type=list#seq=0",
    "http://saramin.co.kr/zf_user/jobs/view?rec_idx=54220010&t_ref=search",
    "  https://www.saramin.co.kr/zf_user/jobs/view/?rec_idx=54220010  ",
])
def test_normalize_ok(raw):
    n = normalize(raw)
    assert n.url == CANON and n.ident == "54220010"


@pytest.mark.parametrize("raw", [
    "",
    "saramin.co.kr/zf_user/jobs/view?rec_idx=1",
    "https://www.saramin.co.kr/zf_user/company-info/view?csn=abc",
    "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=abc",
    "https://www.jobkorea.co.kr/Search/?stext=python",
    "https://www.wanted.co.kr/company/10785",
    "https://cashwalk12.career.greetinghr.com/ko/home",
    "ftp://www.saramin.co.kr/zf_user/jobs/view?rec_idx=1",
    "http://localhost:8000/x",
    "http://127.0.0.1/admin",
    "http://192.168.0.1/",
    "http://[::1]/",
])
def test_normalize_rejects(raw):
    with pytest.raises(UnsupportedURL):
        normalize(raw)


def test_lookalike_host_is_generic_not_saramin():
    n = normalize("https://www.saramin.co.kr.evil.com/zf_user/jobs/view?rec_idx=1")
    assert n.site is GENERIC


def test_redirect_allowlist():
    assert is_allowed_fetch_url("https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=1", SARAMIN)
    assert not is_allowed_fetch_url("http://www.saramin.co.kr/zf_user/jobs/view?rec_idx=1", SARAMIN)
    assert not is_allowed_fetch_url("https://other.com/", SARAMIN)
    assert is_allowed_fetch_url("https://other.com/", GENERIC)
    assert not is_allowed_fetch_url("http://10.0.0.5/", GENERIC)


@pytest.mark.parametrize("raw,expected", [
    ("신입·경력", "신입/경력"),
    ("신입~경력 5년", "신입/경력"),
    ("신입 또는 1년 이상", "신입/경력"),
    ("신입·경력 3~8년", "신입/경력"),
    ("신입", "신입"),
    ("신입(인턴)", "신입"),
    ("졸업예정자", "신입"),
    ("경력무관", "경력무관"),
    ("경력무관(신입포함)", "경력무관"),
    ("경력 1~5년", "1년 이상"),
    ("경력 1년↑", "1년 이상"),
    ("경력 2~7년", "2년 이상"),
    ("경력 3년 이상", "3년 이상"),
    ("경력 5년↑", "3년 이상"),
    ("", None),
    ("경력", None),
])
def test_classify_career(raw, expected):
    assert classify_career(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("2026.11.28 23:59", date(2026, 11, 28)),
    ("2026-08-17", date(2026, 8, 17)),
    ("채용시 마감", None),
    ("상시채용", None),
    (None, None),
])
def test_parse_deadline(raw, expected):
    assert parse_deadline(raw) == expected


@pytest.mark.parametrize("raw,company,expected", [
    ("[디비투이] 신입,경력 (웹개발자,DBA/DBE) 채용", "(주)디비투이", "[(주)디비투이] 신입,경력 (웹개발자,DBA/DBE) 채용"),
    ("[캐시워크] 데이터엔지니어 모집", "넛지헬스케어(주)", "[넛지헬스케어(주)] [캐시워크] 데이터엔지니어 모집"),
    ("넥스블루 DataBase 엔지니어 신입 모집", "(주)넥스블루", "[(주)넥스블루] DataBase 엔지니어 신입 모집"),
    ("[신입] CGMS 알고리즘 개발 채용", "(주)아이센스", "[(주)아이센스] [신입] CGMS 알고리즘 개발 채용"),
    ("데이터 엔지니어", None, "데이터 엔지니어"),
    ("토스뱅크 Server Developer", "토스", "[토스] 토스뱅크 Server Developer"),
    ("카카오페이 백엔드 개발자", "카카오", "[카카오] 카카오페이 백엔드 개발자"),
])
def test_format_title(raw, company, expected):
    assert format_title(raw, company) == expected


def test_extract_sections_patterns():
    lines = [
        "회사 소개 문단",
        "[업무내용]",
        "- 파이프라인 구축",
        "📋 자격요건",
        "ㆍPython 기반 백엔드 개발",
        "역량",  # 줄바꿈된 이어지는 줄
        "📋 우대사항",
        "[실무 경험 및 기술 스택]",  # 구간 안 소제목은 종료가 아님
        "• Airflow 경험",
        "•",
        "Kafka 경험",
        "🏠 근무조건",
        "• 근무지 : 서울",
        "전형절차 : 서류 → 면접",
        "🛎️ 유의사항",
        "• 허위 기재 시 취소",
    ]
    s = extract_sections(lines)
    assert s["주요 업무"] == ["파이프라인 구축"]
    assert s["자격 요건"] == ["Python 기반 백엔드 개발 역량"]
    assert s["우대 사항"] == ["[실무 경험 및 기술 스택]", "Airflow 경험", "Kafka 경험"]
    assert s["전형 절차"] == ["서류 → 면접"]
