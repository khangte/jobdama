"""입력 URL 검증과 정규화.

등록된 채용 사이트는 사이트별 공고 상세 경로만 받아 정해진 형식으로 정규화한다.
등록되지 않은 호스트는 '기업 자체 채용 사이트'로 보고 추적 파라미터와 해시만 지운다.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 공고 식별값이 아닌 추적·유입 파라미터
TRACKING_PARAMS = {
    "gclid", "fbclid", "igshid", "mc_cid", "mc_eid", "ref", "referrer", "source", "src",
    "t_ref", "t_ref_content", "t_ref_scnid", "relaynonce", "search_uuid", "view_type",
    "searchword", "searchtype", "gz", "paid_fl", "immediately_apply_layer_open", "recommend_ids",
    "ismypage", "logpath", "rpagecode", "sn", "sc", "inner_source", "inner_medium",
    "inner_campaign", "inner_term", "dpid", "referer", "from", "utm",
}


class UnsupportedURL(ValueError):
    pass


@dataclass(frozen=True)
class Site:
    key: str
    name: str
    hosts: tuple[str, ...]          # 허용 호스트(정확히 일치). '*.' 로 시작하면 하위 도메인 허용
    js_rendered: bool = False       # httpx 응답에 공고 내용이 없을 가능성이 커서 Playwright 우선
    verified: bool = True           # 실제 공고 HTML로 파서를 확인했는지

    def host_allowed(self, host: str) -> bool:
        host = (host or "").lower()
        for h in self.hosts:
            if h.startswith("*."):
                if host.endswith(h[1:]) and host != h[2:]:
                    return True
            elif host == h:
                return True
        return False


SARAMIN = Site("saramin", "사람인", ("www.saramin.co.kr", "saramin.co.kr", "m.saramin.co.kr"))
JOBKOREA = Site("jobkorea", "잡코리아", ("www.jobkorea.co.kr", "jobkorea.co.kr", "m.jobkorea.co.kr"))
WANTED = Site("wanted", "원티드", ("www.wanted.co.kr", "wanted.co.kr"))
INCRUIT = Site("incruit", "인크루트", ("job.incruit.com", "m.incruit.com", "www.incruit.com"))
# 캐치·자소설닷컴은 httpx 응답에 공고 데이터가 다 있다. 브라우저로 열면 추천 공고가 섞여 오히려 부정확하다.
CATCH = Site("catch", "캐치", ("www.catch.co.kr", "catch.co.kr", "m.catch.co.kr"))
JASOSEOL = Site("jasoseol", "자소설닷컴", ("jasoseol.com", "www.jasoseol.com"))
JUMPIT = Site("jumpit", "점핏", ("jumpit.saramin.co.kr",))
GREETING = Site("greeting", "그리팅", ("*.greetinghr.com",))
GENERIC = Site("generic", "기업 채용 사이트", (), js_rendered=True)

SITES: tuple[Site, ...] = (SARAMIN, JUMPIT, JOBKOREA, WANTED, INCRUIT, CATCH, JASOSEOL, GREETING)

# 지원 대상에서 뺀 사이트. 자체 채용 사이트로 받지 않도록 명시적으로 거절한다.
EXCLUDED_HOSTS: dict[str, str] = {
    "career.programmers.co.kr": "프로그래머스",
    "programmers.co.kr": "프로그래머스",
    "school.programmers.co.kr": "프로그래머스",
}


@dataclass(frozen=True)
class NormalizedURL:
    site: Site
    url: str                       # 노션에 저장할 정규화 URL
    ident: str                     # 사이트 안의 공고 번호(자체 사이트는 정규화 URL)
    # 노션 URL 속성 중복 조회 조건 (연산자, 값). equals(url)은 항상 포함한다.
    match: tuple[tuple[str, str], ...] = field(default=())
    extra: dict = field(default_factory=dict, compare=False, hash=False)


def site_for_host(host: str) -> Site | None:
    for site in SITES:
        if site.host_allowed(host):
            return site
    return None


def _num(m: re.Match | None) -> str | None:
    return m.group(1) if m else None


def normalize(raw: str) -> NormalizedURL:
    raw = (raw or "").strip()
    if not raw:
        raise UnsupportedURL("빈 입력")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise UnsupportedURL("http(s) 링크가 아님")
    host = (parts.hostname or "").lower()
    if not host:
        raise UnsupportedURL("호스트가 없음")
    if host in EXCLUDED_HOSTS:
        raise UnsupportedURL(f"{EXCLUDED_HOSTS[host]}는 지원 대상에서 제외됨")
    path = parts.path.rstrip("/") or "/"
    query = {k.lower(): v for k, v in parse_qsl(parts.query, keep_blank_values=True)}
    site = site_for_host(host)

    if site is SARAMIN:
        if path in ("/zf_user/jobs/view", "/zf_user/jobs/relay/view", "/job-search/view"):
            rid = query.get("rec_idx", "")
            if rid.isdigit():
                return NormalizedURL(
                    SARAMIN, f"https://www.saramin.co.kr/zf_user/jobs/view?rec_idx={rid}", rid,
                    (("ends_with", f"rec_idx={rid}"), ("contains", f"rec_idx={rid}&")),
                )
            raise UnsupportedURL("rec_idx(공고 번호)가 없음")
        raise UnsupportedURL("사람인 공고 상세 페이지 링크가 아님")

    if site is JUMPIT:
        n = _num(re.fullmatch(r"/position/(\d+)", path))
        if n:
            return NormalizedURL(JUMPIT, f"https://jumpit.saramin.co.kr/position/{n}", n,
                                 (("ends_with", f"jumpit.saramin.co.kr/position/{n}"),
                                  ("contains", f"jumpit.saramin.co.kr/position/{n}?")))
        raise UnsupportedURL("점핏 포지션 상세 링크가 아님")

    if site is JOBKOREA:
        n = _num(re.fullmatch(r"/recruit/gi_read/(\d+)", path, re.IGNORECASE))
        if n is None and re.fullmatch(r"/recruit/gi_read_comt_ifrm", path, re.IGNORECASE):
            n = query.get("gno") if (query.get("gno") or "").isdigit() else None
        if n:
            return NormalizedURL(JOBKOREA, f"https://www.jobkorea.co.kr/Recruit/GI_Read/{n}", n,
                                 (("ends_with", f"GI_Read/{n}"), ("contains", f"GI_Read/{n}?"),
                                  ("ends_with", f"Gno={n}"), ("contains", f"Gno={n}&")),
                                 extra={"body_url": f"https://www.jobkorea.co.kr/Recruit/GI_Read_Comt_Ifrm?Gno={n}"})
        raise UnsupportedURL("잡코리아 공고 상세 링크가 아님")

    if site is WANTED:
        n = _num(re.fullmatch(r"/wd/(\d+)", path))
        if n:
            return NormalizedURL(WANTED, f"https://www.wanted.co.kr/wd/{n}", n,
                                 (("ends_with", f"wanted.co.kr/wd/{n}"), ("contains", f"wanted.co.kr/wd/{n}?")))
        raise UnsupportedURL("원티드 공고 상세 링크가 아님")

    if site is INCRUIT:
        job = query.get("job", "")
        if path.lower() == "/jobdb_info/jobpost.asp" and job.isdigit():
            return NormalizedURL(INCRUIT, f"https://job.incruit.com/jobdb_info/jobpost.asp?job={job}", job,
                                 (("ends_with", f"job={job}"), ("contains", f"job={job}&")),
                                 extra={"body_url": f"https://job.incruit.com/s_common/jobpost/jobpostcont.asp?job={job}"})
        raise UnsupportedURL("인크루트 공고 상세 링크가 아님")

    if site is CATCH:
        n = _num(re.fullmatch(r"/ncs/recruitinfodetails/(\d+)", path, re.IGNORECASE))
        if n:
            return NormalizedURL(CATCH, f"https://www.catch.co.kr/NCS/RecruitInfoDetails/{n}", n,
                                 (("ends_with", f"RecruitInfoDetails/{n}"), ("contains", f"RecruitInfoDetails/{n}?")),
                                 extra={"body_url": f"https://www.catch.co.kr/controls/recruitDetail/{n}"})
        raise UnsupportedURL("캐치 채용공고 상세 링크가 아님")

    if site is JASOSEOL:
        n = _num(re.fullmatch(r"/recruit/(\d+)", path))
        if n:
            return NormalizedURL(JASOSEOL, f"https://jasoseol.com/recruit/{n}", n,
                                 (("ends_with", f"jasoseol.com/recruit/{n}"), ("contains", f"jasoseol.com/recruit/{n}?")))
        raise UnsupportedURL("자소설닷컴 공고 상세 링크가 아님")

    if site is GREETING:
        m = re.fullmatch(r"(/[a-z]{2})?/o/(\d+)", path)
        if m:
            lang, n = m.group(1) or "", m.group(2)
            url = f"https://{host}{lang}/o/{n}"
            return NormalizedURL(GREETING, url, f"{host}/{n}",
                                 (("ends_with", f"{host}{lang}/o/{n}"), ("ends_with", f"{host}/o/{n}"),
                                  ("contains", f"{host}{lang}/o/{n}?")))
        raise UnsupportedURL("그리팅 공고 상세 링크가 아님")

    return _normalize_generic(parts, host)


def _normalize_generic(parts, host: str) -> NormalizedURL:
    if is_private_host_literal(host):
        raise UnsupportedURL("내부 주소(localhost, 사설 IP)는 받지 않음")
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")]
    path = parts.path or "/"
    netloc = host if parts.port in (None, 80, 443) else f"{host}:{parts.port}"
    url = urlunsplit((parts.scheme, netloc, path, urlencode(kept, doseq=True), ""))
    return NormalizedURL(GENERIC, url, url, ())


def is_private_host_literal(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if host in ("localhost",) or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_global


def is_allowed_fetch_url(url: str, site: Site) -> bool:
    """수집 중 요청·이동 대상이 허용되는지(호스트 기준).

    등록 사이트는 해당 사이트 호스트만, 자체 채용 사이트는 사설 주소가 아닌 공개 호스트만 허용한다.
    (자체 사이트의 DNS 결과 검사는 fetcher에서 추가로 한다.)
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    if site is GENERIC:
        return bool(host) and not is_private_host_literal(host)
    return parts.scheme == "https" and site.host_allowed(host)


def same_posting(target: NormalizedURL, final_url: str) -> bool:
    """이동 후 주소가 같은 사이트의 다른 공고면 False. 판단할 수 없으면 True."""
    if target.site is GENERIC:
        return True
    try:
        final = normalize(final_url)
    except UnsupportedURL:
        return True  # 상세 경로가 아닌 곳(로그인, 안내 페이지 등)은 파서가 판단
    if final.site is not target.site:
        return True
    return final.ident == target.ident
