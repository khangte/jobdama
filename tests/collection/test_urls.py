"""URL 정규화·검증 규칙."""

import pytest

from app.collection.urls import (
    CATCH, GENERIC, GREETING, INCRUIT, JASOSEOL, JOBKOREA, JUMPIT, SARAMIN, WANTED, UnsupportedURL, is_allowed_fetch_url,
    normalize, same_posting,
)


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


@pytest.mark.parametrize("url", [
    "https://career.programmers.co.kr/job_positions/12345",
    "https://programmers.co.kr/job_positions/1",
])
def test_programmers_excluded(url):
    with pytest.raises(UnsupportedURL, match="프로그래머스"):
        normalize(url)
