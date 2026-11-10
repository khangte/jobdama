"""목록 링크 추출. 실제 사이트 목록 응답(httpx로 받음)과 작은 입력으로 검증한다."""

from urllib.parse import quote_plus

import pytest

from app.collection.collect_config import CollectConfig
from app.collection.listing import SITE_PARAMS, build_sources, extract_links
from app.collection.urls import normalize
from tests.helpers import load

FIXTURES = [("saramin", "listing_saramin.html"), ("jobkorea", "listing_jobkorea.html"),
            ("incruit", "listing_incruit_job.html"), ("wanted", "listing_wanted.json"),
            ("jumpit", "listing_jumpit.json")]


@pytest.mark.parametrize("key, name", FIXTURES)
def test_extract_from_real_fixtures(key, name):
    links = extract_links(key, load(name))
    assert len(links) >= 3 and len(set(links)) == len(links)
    for u in links:
        n = normalize(u)
        assert n.url == u and n.site.key == key


def test_saramin_dedup_relay():
    body = '<a href="/zf_user/jobs/relay/view?rec_idx=1&x=y"></a><a href="/zf_user/jobs/relay/view?rec_idx=1&x=y"></a>rec_idx=2'
    assert extract_links("saramin", body) == [
        "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=1", "https://www.saramin.co.kr/zf_user/jobs/view?rec_idx=2"]


def test_wanted_drops_three_years():
    body = '{"data": [{"id": 1, "annual_from": 0}, {"id": 2, "annual_from": 3}, {"id": 3, "annual_from": null}]}'
    assert extract_links("wanted", body, max_years=2) == ["https://www.wanted.co.kr/wd/1", "https://www.wanted.co.kr/wd/3"]
    assert len(extract_links("wanted", body)) == 3  # max_years=None이면 거르지 않는다


@pytest.mark.parametrize("body", ["not json", '{"nope": 1}', '{"data": [1]}'])
def test_wanted_broken_json_is_value_error(body):
    with pytest.raises(ValueError):
        extract_links("wanted", body)


def test_extract_incruit_job():
    links = extract_links("incruit", load("listing_incruit_job.html"))
    assert len(links) == 6 and len(set(links)) == 6
    assert all("jobpost.asp?job=" in u for u in links)
    assert not any("2611040003500" in u for u in links)  # 광고 영역 링크는 JobPostNoList에 없다


def test_extract_jumpit():
    body = load("listing_jumpit.json")
    assert len(extract_links("jumpit", body)) == 5
    assert extract_links("jumpit", body, max_years=0) == ["https://jumpit.saramin.co.kr/position/55330482"]
    assert len(extract_links("jumpit", body, max_years=2)) == 3  # minCareer 0·2·1


@pytest.mark.parametrize("body", ["not json", '{"result": {}}', '{"result": {"positions": [1]}}'])
def test_jumpit_broken_json_is_value_error(body):
    with pytest.raises(ValueError, match="점핏 목록 형식 오류|Expecting"):
        extract_links("jumpit", body)


def urls(**kw) -> dict[str, tuple[str, ...]]:
    """설정을 덮어써 만든 사이트별 목록 주소."""
    return {s.site.key: s.pages for s in build_sources(CollectConfig(**kw))}


def test_default_urls():
    u = urls()
    assert u["saramin"] == (("https://www.saramin.co.kr/zf_user/search/recruit?searchword=python&recruitPageCount=40"
                            "&loc_mcd=101000,102000&recruitSort=reg_dt"),)
    assert u["jobkorea"] == tuple(f"https://www.jobkorea.co.kr/Search/?stext=python&tabType=recruit&Page_No={n}&ord=RegDtDesc"
                                  for n in (1, 2))
    assert u["wanted"] == (("https://www.wanted.co.kr/api/v4/jobs?country=kr&query=python&limit=40"
                           "&locations=seoul.all&locations=gyeonggi.all&years=0&years=2&job_sort=job.latest_order"),)
    assert u["incruit"] == ("https://job.incruit.com/jobdb_list/searchjob.asp?kw=python",)
    assert u["jumpit"] == (("https://jumpit-api.saramin.co.kr/api/positions?keyword=python&size=40"
                           "&locationTag=101000,102000&sort=reg_dt"),)


def test_all_any_base_urls():
    u = urls(keyword="", job="any", region="any", career="any", sort="any")
    assert u["saramin"] == ("https://www.saramin.co.kr/zf_user/search/recruit?recruitPageCount=40",)
    assert u["jobkorea"][0] == "https://www.jobkorea.co.kr/Search/?tabType=recruit&Page_No=1"
    assert u["wanted"] == ("https://www.wanted.co.kr/api/v4/jobs?country=kr&limit=40",)
    assert u["incruit"] == ("https://job.incruit.com/jobdb_list/searchjob.asp",)
    assert u["jumpit"] == ("https://jumpit-api.saramin.co.kr/api/positions?size=40",)


def test_unmapped_choice_omits_param():
    u = urls(region="busan")
    assert "locations" not in u["wanted"][0]
    assert "loc_mcd=106000" in u["saramin"][0] and "locationTag=106000" in u["jumpit"][0]
    u = urls(salary="4000")
    assert "salary" not in u["incruit"][0] and "sal_min" not in u["incruit"][0]
    assert "sal_min=16" in u["saramin"][0] and "payType=1&payMin=4000" in u["jobkorea"][0]
    u = urls(job="app")
    assert "tag_type_ids" not in u["wanted"][0]
    assert "jobCategory=4&jobCategory=16&jobCategory=18" in u["jumpit"][0]
    assert "cat_mcls=2" in urls(job="ml")["saramin"][0]
    assert "job_sort" not in urls(sort="relevance")["wanted"][0]


def test_career_new_params():
    u = urls(career="new")
    assert "exp_cd=1" in u["saramin"][0] and "careerType=1" in u["jobkorea"][0]
    assert u["wanted"][0].count("years=") == 1 and "years=0" in u["wanted"][0]
    assert "crr=1" in u["incruit"][0] and "career=0" in u["jumpit"][0]


def test_keyword_encoding():
    u = urls(keyword="데이터 엔지니어")
    assert f"searchword={quote_plus('데이터 엔지니어')}" in u["saramin"][0]
    assert f"kw={quote_plus('데이터 엔지니어', encoding='cp949')}" in u["incruit"][0]


def test_sites_subset_order():
    sources = build_sources(CollectConfig(sites=["jumpit", "saramin"]))
    assert tuple(s.site.key for s in sources) == ("saramin", "jumpit")
    assert sources[1].site.hosts == ("jumpit-api.saramin.co.kr",)


def test_site_params_example():
    assert SITE_PARAMS["jumpit"]["job"]["app"] == [("jobCategory", "4"), ("jobCategory", "16"), ("jobCategory", "18")]


def test_catch_url_and_extract():
    """캐치 목록 주소 조립과 RecruitID → 상세 URL 변환·중복 제거를 확인한다."""
    assert urls()["catch"] == (("https://www.catch.co.kr/api/v1.0/recruit/information/getRecruitList"
                                "?Keyword=python&curpage=1&pageSize=40&onRecruitYN=Y"),)
    body = '{"recruitData": [{"RecruitID": 575584}, {"RecruitID": "578557"}, {"RecruitID": 575584}], "intTotalRecordCount": 3}'
    links = extract_links("catch", body)
    assert links == [f"https://www.catch.co.kr/NCS/RecruitInfoDetails/{i}" for i in (575584, 578557)]
    assert normalize(links[0]).url == links[0]


@pytest.mark.parametrize("body", ["null", "not json", '{"recruitData": [1]}', '{"recruitData": [{"RecruitID": "x"}]}'])
def test_catch_broken_is_value_error(body):
    """캐치 목록 응답이 null·깨진 JSON·잘못된 행이면 ValueError를 낸다."""
    with pytest.raises(ValueError):
        extract_links("catch", body)
