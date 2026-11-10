"""사이트별 공고 목록에서 상세 링크를 뽑는다(자동 수집용). 목록 요청 주소와 링크 추출 규칙은 이 모듈에서만 고친다."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from urllib.parse import urlencode

from app.collection.collect_config import CollectConfig
from app.collection.urls import CATCH, INCRUIT, JOBKOREA, JUMPIT, SARAMIN, WANTED, Site

# 목록 요청 전용 Site. urls.py의 SITES·normalize는 건드리지 않는다(API 호스트는 상세 링크로 받지 않음)
JUMPIT_API = replace(JUMPIT, hosts=("jumpit-api.saramin.co.kr",))

Pairs = list[tuple[str, str | None]]


@dataclass(frozen=True)
class ListingSource:
    """자동 수집 대상 사이트와 그 목록 페이지 주소."""

    site: Site
    pages: tuple[str, ...]


def _one(name: str, values: dict[str, str]) -> dict[str, Pairs]:
    """선택 키 → 변수 하나짜리 쌍 목록 표를 만든다."""
    return {k: [(name, v)] for k, v in values.items()}


_SARAMIN_REGION = {"seoul_gyeonggi": "101000,102000", "seoul": "101000", "gyeonggi": "102000", "incheon": "108000",
                   "busan": "106000", "daegu": "104000", "gwangju": "112000", "daejeon": "105000", "ulsan": "107000",
                   "sejong": "118000", "gangwon": "109000", "chungbuk": "114000", "chungnam": "115000",
                   "jeonbuk": "113000", "jeonnam": "112000", "gyeongbuk": "111000", "gyeongnam": "110000",
                   "jeju": "116000"}

# 사이트 → 필드 → 선택 키 → 쿼리 쌍. 표에 없는 칸(해당 사이트에 대응 코드 없음)은 변수를 보내지 않고 받은 뒤 거른다
SITE_PARAMS: dict[str, dict[str, dict[str, Pairs]]] = {
    "saramin": {
        "job": _one("cat_kewd", {"backend": "84", "frontend": "92", "app": "86", "data_engineer": "83"})
        | _one("cat_mcls", {"ml": "2", "devops": "2"}),
        "region": _one("loc_mcd", _SARAMIN_REGION),
        "career": _one("exp_cd", {"new": "1"}),
        "education": _one("edu_min", {"high": "6", "college": "7", "bachelor": "8", "master": "9", "doctor": "5"}),
        "employment": _one("job_type", {"fulltime": "1", "contract": "2", "intern": "4", "freelance": "9"}),
        "salary": _one("sal_min", {"3000": "11", "4000": "16", "5000": "17"}),
        "sort": _one("recruitSort", {"latest": "reg_dt", "relevance": "relation"}),
    },
    "jobkorea": {
        "job": _one("duty", {"backend": "1000229", "frontend": "1000230", "app": "1000232", "data_engineer": "1000236",
                             "ml": "1000242", "devops": "1000233"}),
        "career": _one("careerType", {"new": "1"}),
        "education": _one("edu", {"high": "3", "college": "4", "bachelor": "5", "master": "6", "doctor": "7"}),
        "employment": _one("jobtype", {"fulltime": "1", "contract": "2", "intern": "3", "freelance": "6"}),
        "salary": {k: [("payType", "1"), ("payMin", k)] for k in ("3000", "4000", "5000")},
        "sort": _one("ord", {"latest": "RegDtDesc", "relevance": "RelevanceDesc"}),
    },
    "wanted": {
        "job": _one("tag_type_ids", {"backend": "872", "frontend": "669", "data_engineer": "655", "ml": "1634",
                                     "devops": "674"}),
        "region": {"seoul_gyeonggi": [("locations", "seoul.all"), ("locations", "gyeonggi.all")],
                   "seoul": [("locations", "seoul.all")], "gyeonggi": [("locations", "gyeonggi.all")]},
        "career": {"new": [("years", "0")], "upto1": [("years", "0"), ("years", "1")],
                   "upto2": [("years", "0"), ("years", "2")]},
        "sort": _one("job_sort", {"latest": "job.latest_order"}),
    },
    "incruit": {
        "job": _one("occ1", dict.fromkeys(("backend", "frontend", "app", "data_engineer", "ml", "devops"), "150")),
        "career": _one("crr", {"new": "1"}),
        "education": _one("schol", {"high": "40", "college": "50", "bachelor": "60", "master": "70", "doctor": "80"}),
        "employment": _one("jobty", {"fulltime": "1", "contract": "2", "intern": "4", "freelance": "8"}),
    },
    "jumpit": {
        "job": {"backend": [("jobCategory", "1")], "frontend": [("jobCategory", "2")],
                "app": [("jobCategory", "4"), ("jobCategory", "16"), ("jobCategory", "18")],
                "data_engineer": [("jobCategory", "19")], "ml": [("jobCategory", "8")],
                "devops": [("jobCategory", "9")]},
        "region": _one("locationTag", _SARAMIN_REGION),
        "career": _one("career", {"new": "0"}),
        "sort": _one("sort", {"latest": "reg_dt", "relevance": "relation"}),
    },
    "catch": {},  # 필터 코드가 검증되지 않아 검색어만 보낸다
}

_FIELD_ORDER = ("job", "region", "career", "education", "employment", "salary", "sort")
_SITES = {"saramin": SARAMIN, "jobkorea": JOBKOREA, "wanted": WANTED, "incruit": INCRUIT, "jumpit": JUMPIT_API, "catch": CATCH}


# 사이트별 기본 쿼리 쌍. 값이 None인 쌍이 검색어 자리다(검색어가 비면 뺀다)
_BASE: dict[str, Pairs] = {
    "saramin": [("searchword", None), ("recruitPageCount", "40")],
    "jobkorea": [("stext", None), ("tabType", "recruit")],
    "wanted": [("country", "kr"), ("query", None), ("limit", "40")],
    "incruit": [("kw", None)],
    "jumpit": [("keyword", None), ("size", "40")],
    # 서버가 curpage·pageSize가 없으면 null을 준다. 키워드 키는 대문자 Keyword
    "catch": [("Keyword", None), ("curpage", "1"), ("pageSize", "40"), ("onRecruitYN", "Y")],
}
_JOBKOREA_PAGES = ("1", "2")  # 잡코리아만 목록 2페이지를 받는다


_ENDPOINT = {
    "saramin": "https://www.saramin.co.kr/zf_user/search/recruit",
    "jobkorea": "https://www.jobkorea.co.kr/Search/",
    "wanted": "https://www.wanted.co.kr/api/v4/jobs",
    "incruit": "https://job.incruit.com/jobdb_list/searchjob.asp",
    "jumpit": "https://jumpit-api.saramin.co.kr/api/positions",
    "catch": "https://www.catch.co.kr/api/v1.0/recruit/information/getRecruitList",
}


def build_sources(cfg: CollectConfig) -> tuple[ListingSource, ...]:
    """설정으로 사이트별 목록 주소를 만든다. 순서는 cfg.sites. 대응 코드가 없는 선택은 변수를 뺀다."""
    sources = []
    for key in cfg.sites:
        extra: Pairs = []
        for field in _FIELD_ORDER:
            extra += SITE_PARAMS[key].get(field, {}).get(getattr(cfg, field), [])
        enc = "cp949" if key == "incruit" else "utf-8"  # 인크루트는 검색어를 cp949로 받는다
        base = [(k, cfg.keyword if v is None else v) for k, v in _BASE[key] if v is not None or cfg.keyword]
        bases = [base + [("Page_No", n)] for n in _JOBKOREA_PAGES] if key == "jobkorea" else [base]
        pages = tuple(_ENDPOINT[key] + "?" * bool(b + extra) + urlencode(b + extra, safe=",", encoding=enc) for b in bases)
        sources.append(ListingSource(_SITES[key], pages))
    return tuple(sources)


_SARAMIN_RE = re.compile(r"rec_idx=(\d+)")
_JOBKOREA_RE = re.compile(r"/Recruit/GI_Read/(\d+)")
_INCRUIT_INPUT_RE = re.compile(r"<input[^>]*JobPostNoList[^>]*>")
_VALUE_RE = re.compile(r'value="([^"]*)"')


def _unique(ids: list[str]) -> list[str]:
    """순서를 유지하며 중복을 뺀다."""
    return list(dict.fromkeys(ids))


def _incruit_ids(body: str) -> list[str]:
    """숨은 입력 JobPostNoList(쉼표 구분)에서 일반 공고 id만 뽑는다. 광고 영역 링크는 이 목록에 없다."""
    tag = _INCRUIT_INPUT_RE.search(body)
    value = _VALUE_RE.search(tag.group(0)) if tag else None
    return _unique(re.findall(r"\d+", value.group(1))) if value else []


def extract_links(site_key: str, body: str, max_years: int | None = None) -> list[str]:
    """목록 응답에서 공고 상세 URL을 순서 유지·중복 제거해 뽑는다.

    max_years가 있으면 원티드(annual_from)·점핏(minCareer)에서 그 연차를 넘는 항목을 뺀다(값이 없으면 남긴다).
    원티드·점핏 JSON이 깨졌으면 ValueError.
    """
    limit = float("inf") if max_years is None else max_years
    if site_key == "saramin":
        return [f"https://www.saramin.co.kr/zf_user/jobs/view?rec_idx={i}" for i in _unique(_SARAMIN_RE.findall(body))]
    if site_key == "jobkorea":
        return [f"https://www.jobkorea.co.kr/Recruit/GI_Read/{i}" for i in _unique(_JOBKOREA_RE.findall(body))]
    if site_key == "incruit":
        return [f"https://job.incruit.com/jobdb_info/jobpost.asp?job={i}" for i in _incruit_ids(body)]
    if site_key == "wanted":
        try:
            jobs = json.loads(body)["data"]
            return [f"https://www.wanted.co.kr/wd/{j['id']}" for j in jobs if (j.get("annual_from") or 0) <= limit]
        except (KeyError, TypeError, AttributeError) as e:
            raise ValueError(f"원티드 목록 형식 오류: {type(e).__name__}") from e
    if site_key == "jumpit":
        try:
            positions = json.loads(body)["result"]["positions"]
            return [f"https://jumpit.saramin.co.kr/position/{p['id']}" for p in positions
                    if (p.get("minCareer") or 0) <= limit]
        except (KeyError, TypeError, AttributeError) as e:
            raise ValueError(f"점핏 목록 형식 오류: {type(e).__name__}") from e
    if site_key == "catch":
        try:
            rows = json.loads(body)["recruitData"]
            return _unique([f"https://www.catch.co.kr/NCS/RecruitInfoDetails/{int(r['RecruitID'])}" for r in rows])
        except (KeyError, TypeError, ValueError, AttributeError) as e:  # 조건에 맞는 공고가 없거나 파라미터가 틀리면 null이 온다
            raise ValueError(f"캐치 목록 형식 오류: {type(e).__name__}") from e
    raise ValueError(f"목록을 지원하지 않는 사이트: {site_key}")
