"""값 변환(경력 분류, 마감일, 공고명 형식)."""

from datetime import date

import pytest

from app.parsers.common import classify_career, format_title, parse_deadline


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
    ("경력 3년 이하", None),
    ("경력 1년 미만", None),
    ("경력 3년↓", None),
    ("경력 1~3년 이하", "1년 이상"),
    ("경력 2~5년 미만", "2년 이상"),
    ("경력 0~3년 이하", "신입/경력"),
    ("경력 5년 이하 (1년 이상)", "1년 이상"),
    ("경력 3년~", "3년 이상"),
    ("경력 2년", "2년 이상"),
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
    ("[(주)디비투이] 백엔드", "(주)디비투이", "[(주)디비투이] 백엔드"),
    ("(주)디비투이 백엔드", "디비투이", "[디비투이] 백엔드"),
    ("【(주)디비투이】 백엔드", "디비투이", "[디비투이] 백엔드"),
])
def test_format_title(raw, company, expected):
    assert format_title(raw, company) == expected


@pytest.mark.parametrize("raw,today,expected", [
    ("~11/30(일) 23:59", date(2026, 11, 3), date(2026, 11, 30)),
    ("1.15", date(2026, 11, 3), date(2027, 1, 15)),
    ("2026.12.01", date(2026, 11, 3), date(2026, 12, 1)),
    ("상시채용", date(2026, 11, 3), None),
])
def test_deadline_without_year(raw, today, expected):
    assert parse_deadline(raw, today=today) == expected
