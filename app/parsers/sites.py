"""등록 사이트별 파서. 범용 파서 결과에 사이트에서 확실히 얻을 수 있는 값을 덮어쓴다.

확인한 구조 (2026-11 기준, tests/fixtures 참고)
- 잡코리아: JSON-LD(JobPosting) + 메타 설명 '경력 : ..., 마감일 : ...'. 본문은 GI_Read_Comt_Ifrm?Gno= 별도 페이지
- 인크루트: JSON-LD(JobPosting, description에 '경력: ...'). 본문은 jobpostcont.asp?job= 별도 페이지. EUC-KR
- 원티드: __NEXT_DATA__ props.pageProps.initialData (main_tasks, requirements, preferred_points, hire_rounds ...)
- 점핏: 서버 렌더링 HTML. h1, a.name, dt/dd(주요업무, 자격요건, 우대사항, 경력, 마감일, 근무지역)
- 그리팅: __NEXT_DATA__ dehydratedState 의 getOpeningById (openingsInfo.detail HTML, jobPositions)
- 자소설닷컴: __NEXT_DATA__ props.pageProps.initialEmploymentCompany (title, name, end_time, content HTML) + JSON-LD
- 캐치: JSON-LD(JobPosting, description에 '경력 : ...'), 본문은 /controls/recruitDetail/{번호} 별도 페이지
  (div#iframe_wrapper 안에 텍스트 또는 이미지)
"""

from __future__ import annotations

import re
from dataclasses import replace

from selectolax.lexbor import LexborHTMLParser

from app.models import Posting
from app.parsers import saramin
from app.parsers.common import (
    classify_career, clean, employment_label, extract_sections, format_title, html_to_lines, itemize, jsonld_jobposting,
    merge_sections, meta_content, next_data, parse_deadline, parse_iso_deadline,
)
from app.parsers.generic import meta_value, parse_generic, today_kst
from app.parsers.generic import pick_classifiable
from app.urls import (
    CATCH, GENERIC, GREETING, INCRUIT, JASOSEOL, JOBKOREA, JUMPIT, SARAMIN, WANTED, NormalizedURL,
)


def parse_pages(target: NormalizedURL, pages: dict[str, str]) -> Posting:
    """pages: {'main': 공고 HTML, 'body': 본문 HTML(있으면)}"""
    main = pages.get("main") or ""
    body = pages.get("body")
    site = target.site
    if site is SARAMIN:
        return saramin.parse(main, target.url, target.ident)
    if site is JOBKOREA:
        return parse_jobkorea(main, body, target)
    if site is INCRUIT:
        return parse_incruit(main, body, target)
    if site is WANTED:
        return parse_wanted(main, target)
    if site is JUMPIT:
        return parse_jumpit(main, target)
    if site is JASOSEOL:
        return parse_jasoseol(main, target)
    if site is CATCH:
        return parse_catch(main, body, target)
    if site is GREETING or (site is GENERIC and "getOpeningById" in main):
        return parse_greeting(main, target)
    return parse_generic(main, target, body)


# ---------------------------------------------------------------- 잡코리아

def parse_jobkorea(html: str, body_html: str | None, target: NormalizedURL) -> Posting:
    p = parse_generic(html, target, body_html, page_sections=False)
    tree = LexborHTMLParser(html)
    desc = meta_content(tree, "description", "og:description")
    career = meta_value(desc, "경력")
    deadline_raw = meta_value(desc, "마감일")
    updates: dict = {}
    if career:
        updates.update(career_raw=career, career_select=classify_career(career))
    if deadline_raw:  # '상시채용'이면 JSON-LD의 먼 미래 날짜 대신 비운다
        updates.update(deadline_raw=deadline_raw, deadline=parse_deadline(deadline_raw))
    return replace(p, **updates)


# ---------------------------------------------------------------- 인크루트

def parse_incruit(html: str, body_html: str | None, target: NormalizedURL) -> Posting:
    p = parse_generic(html, target, body_html, page_sections=False)
    ld = jsonld_jobposting(LexborHTMLParser(html)) or {}
    desc_lines = html_to_lines(ld.get("description"))
    info = {k: v for k, v in (_split_label(x) for x in desc_lines) if k}
    updates: dict = {}
    career = info.get("경력")
    if career:
        updates.update(career_raw=career, career_select=classify_career(career))
    if info.get("고용형태"):
        updates["employment_type"] = info["고용형태"]
    if info.get("접수기간"):
        updates.update(deadline_raw=info["접수기간"], deadline=parse_deadline(info["접수기간"]) or p.deadline)
    return replace(p, **updates)


def _split_label(line: str) -> tuple[str | None, str]:
    if ":" in line:
        k, v = line.split(":", 1)
        return clean(k), clean(v)
    return None, ""


# ---------------------------------------------------------------- 원티드

def parse_wanted(html: str, target: NormalizedURL) -> Posting:
    data = next_data(LexborHTMLParser(html)) or {}
    job = ((data.get("props") or {}).get("pageProps") or {}).get("initialData") or {}
    if not job.get("position"):
        return parse_generic(html, target)
    company = clean((job.get("company") or {}).get("company_name")) or None
    raw_title = clean(job["position"])
    career_raw, career_select = _wanted_career(job.get("career") or {})
    address = job.get("address") or {}
    location = clean(address.get("full_location")) or " ".join(
        x for x in (clean(address.get("location")), clean(address.get("district"))) if x) or None
    due = job.get("due_time")
    sections = merge_sections({
        "주요 업무": itemize(job.get("main_tasks") or ""),
        "자격 요건": itemize(job.get("requirements") or ""),
        "우대 사항": itemize(job.get("preferred_points") or ""),
        "전형 절차": itemize(job.get("hire_rounds") or ""),
    })
    return Posting(
        source=target.site.name,
        url=target.url,
        title=format_title(raw_title, company),
        raw_title=raw_title,
        company=company,
        career_raw=career_raw,
        career_select=career_select,
        employment_type=employment_label(job.get("employment_type")),
        location=location,
        deadline=parse_iso_deadline(due) if due else None,
        deadline_raw=str(due) if due else "상시채용",
        expired=(job.get("status") not in (None, "active")),
        sections=sections,
    )


def _wanted_career(career: dict) -> tuple[str | None, str | None]:
    newbie, expert = bool(career.get("is_newbie")), bool(career.get("is_expert"))
    lo, hi = career.get("annual_from"), career.get("annual_to")
    span = f" {lo}~{hi}년" if isinstance(lo, int) and isinstance(hi, int) and expert else ""
    if newbie and expert:
        return f"신입·경력{span}", "신입/경력"
    if newbie:
        return "신입", "신입"
    if expert:
        raw = f"경력{span}".strip()
        if isinstance(lo, int) and lo > 0:
            return raw, classify_career(f"경력 {lo}년 이상")
        return raw, "경력무관"
    return None, None


# ---------------------------------------------------------------- 점핏

def parse_jumpit(html: str, target: NormalizedURL) -> Posting:
    p = parse_generic(html, target)
    tree = LexborHTMLParser(html)
    company = None
    node = tree.css_first("a.name span") or tree.css_first("a.name")
    if node is not None:
        company = clean(node.text(separator=" "))
    if not company:
        desc = meta_content(tree, "og:description")
        if " - " in desc:
            company = clean(desc.split(" - ", 1)[0])
    h1 = tree.css_first("h1")
    raw_title = clean(h1.text(separator=" ")) if h1 is not None else p.raw_title
    return replace(p, company=company or p.company, raw_title=raw_title,
                   title=format_title(raw_title, company or p.company))


# ---------------------------------------------------------------- 자소설닷컴

def parse_jasoseol(html: str, target: NormalizedURL) -> Posting:
    p = parse_generic(html, target, page_sections=False)
    data = next_data(LexborHTMLParser(html)) or {}
    c = ((data.get("props") or {}).get("pageProps") or {}).get("initialEmploymentCompany") or {}
    if not c.get("title"):
        return p
    company = clean(c.get("name")) or p.company
    raw_title = clean(c["title"])
    end = c.get("end_time")
    updates: dict = {}
    # 브라우저로 렌더링한 페이지는 본 공고 JSON-LD가 사라지고 추천 공고 JSON-LD만 남는다.
    # JSON-LD 제목이 본 공고와 다르면 거기서 온 값(경력·근무지·고용 형태)을 버린다.
    ld = jsonld_jobposting(LexborHTMLParser(html)) or {}
    if raw_title not in clean(ld.get("title")):
        updates.update(career_raw=None, career_select=None, location=None, employment_type=None)
    return replace(
        p, company=company, raw_title=raw_title, title=format_title(raw_title, company),
        deadline=parse_iso_deadline(end) if end else None, deadline_raw=str(end) if end else "상시채용",
        sections=extract_sections(html_to_lines(c.get("content"))), **updates,
    )


# ---------------------------------------------------------------- 캐치

def parse_catch(html: str, body_html: str | None, target: NormalizedURL) -> Posting:
    p = parse_generic(html, target, body_html, page_sections=False)
    tree = LexborHTMLParser(html)
    ld = jsonld_jobposting(tree) or {}
    info = {k: v for k, v in (_split_label(x) for x in (ld.get("description") or "").split("\n")) if k}
    career = pick_classifiable(info.get("경력"), p.career_raw)
    updates: dict = {"career_raw": career, "career_select": classify_career(career)}
    if info.get("고용형태"):
        updates["employment_type"] = info["고용형태"]
    # 접수 기간: 'MM.DD(요일) ~ 채용시 마감' 또는 '~ MM.DD(요일) 24시'. og:title 꼬리 '(~채용시 마감)'도 같은 값
    og = meta_content(tree, "og:title")
    m = re.search(r"\(~\s*([^)]+)\)\s*\|\s*캐치\s*$", og)
    if m:
        raw = clean(m.group(1))
        updates.update(deadline_raw=raw, deadline=parse_deadline(raw, today=today_kst()))
    if not p.company:  # og:description: '<기업> 채용ㅣ모집부문, ...'
        desc = meta_content(tree, "og:description")
        cm = re.match(r"\s*(.+?)\s*채용\s*[ㅣ|]", desc)
        if cm:
            company = clean(cm.group(1))
            updates.update(company=company, title=format_title(p.raw_title, company))
    return replace(p, **updates)


# ---------------------------------------------------------------- 그리팅

GREETING_CAREER = {"NOT_MATTER": "경력무관", "NEW_COMER": "신입", "NEWCOMER": "신입", "NEW": "신입"}


def parse_greeting(html: str, target: NormalizedURL) -> Posting:
    data = next_data(LexborHTMLParser(html)) or {}
    props = (data.get("props") or {}).get("pageProps") or {}
    opening_data: dict = {}
    for q in ((props.get("dehydratedState") or {}).get("queries") or []):
        key = q.get("queryKey") or []
        if "getOpeningById" in key:
            opening_data = (((q.get("state") or {}).get("data") or {}).get("data")) or {}
            break
    info = opening_data.get("openingsInfo") or {}
    if not info.get("title"):
        return parse_generic(html, target)

    company = (clean((opening_data.get("groupInfo") or {}).get("name"))
               or clean(((props.get("careerBaseInfo") or {}).get("workspace") or {}).get("name")) or None)
    raw_title = clean(info["title"])
    positions = ((opening_data.get("jobPositionSetting") or {}).get("jobPositions")) or []
    pos = positions[0] if positions else {}
    career_raw, career_select = _greeting_career(pos.get("jobPositionCareer") or {})
    place = pos.get("workspacePlace") or {}
    location = " ".join(x for x in (clean(place.get("place")), clean(place.get("detailPlace"))) if x) or None
    employment = employment_label((pos.get("jobPositionEmployment") or {}).get("employmentType"))
    due = info.get("dueDate")

    sections = extract_sections(html_to_lines(info.get("detail")))
    source = target.site.name if target.site is GREETING else f"{target.site.name}(그리팅)"
    return Posting(
        source=source,
        url=target.url,
        title=format_title(raw_title, company),
        raw_title=raw_title,
        company=company,
        career_raw=career_raw,
        career_select=career_select,
        employment_type=employment,
        location=location,
        deadline=parse_iso_deadline(due) if due else None,
        deadline_raw=str(due) if due else "상시채용",
        expired=(info.get("status") not in (None, "OPEN")),
        sections=sections,
    )


def _greeting_career(c: dict) -> tuple[str | None, str | None]:
    typ = str(c.get("careerType") or "").upper()
    lo, hi = c.get("careerFrom"), c.get("careerTo")
    if typ in GREETING_CAREER:
        return GREETING_CAREER[typ], GREETING_CAREER[typ]
    if "NEW" in typ and ("EXPERIENCE" in typ or "CAREER" in typ):
        return "신입·경력", "신입/경력"
    if isinstance(lo, int):
        raw = f"경력 {lo}~{hi}년" if isinstance(hi, int) else f"경력 {lo}년 이상"
        return raw, classify_career(f"경력 {lo}년 이상") if lo > 0 else "경력무관"
    if typ:
        return typ, None
    return None, None
