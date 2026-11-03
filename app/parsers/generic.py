"""범용 파서. JSON-LD(JobPosting) → 메타 태그 → dt/dd 라벨 → 본문 텍스트 순서로 채운다.

등록 사이트 파서도 이 결과를 바탕으로 사이트별 값을 덮어쓴다.
"""

from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser

from app.models import KST, Posting
from app.parsers.common import (
    classify_career, clean, clean_page_title, employment_label, format_title, is_site_default_title, parse_deadline,
    parse_iso_deadline,
)
from app.parsers.html import (
    dl_pairs, find_label, html_to_lines, jsonld_company, jsonld_experience, jsonld_jobposting, jsonld_location,
    label_in_lines, meta_content, node_lines,
)
from app.parsers.sections import extract_sections, merge_sections
from app.parsers.errors import ParseError
from app.urls import GENERIC, NormalizedURL

CONTENT_SELECTORS = ("main", "article", "[role=main]", "#content", "#contents", ".content", "body")
CAREER_LABELS = ("경력", "경력사항", "경력조건", "지원경력", "경력구분", "Experience")
DEADLINE_LABELS = ("마감일", "접수마감", "마감일자", "접수기간", "모집기간", "지원기간", "Deadline")
LOCATION_LABELS = ("근무지", "근무지역", "근무장소", "근무위치", "Location")
EMPLOYMENT_LABELS = ("고용형태", "근무형태", "채용형태", "고용 형태", "Employment Type")


def content_lines(tree: LexborHTMLParser) -> list[str]:
    for junk in tree.css("header, nav, footer, aside, script, style, noscript, svg, template"):
        junk.decompose()
    for sel in CONTENT_SELECTORS:
        node = tree.css_first(sel)
        if node is not None:
            lines = node_lines(node)
            if len(lines) >= 5 or sel == "body":
                return lines
    return []


def pick_title(tree: LexborHTMLParser, ld: dict | None) -> str:
    candidates = [
        clean(ld.get("title")) if ld else "",
        clean_page_title(meta_content(tree, "og:title", "twitter:title")),
        clean(tree.css_first("h1").text(separator=" ")) if tree.css_first("h1") is not None else "",
        clean_page_title(tree.css_first("title").text() if tree.css_first("title") is not None else ""),
    ]
    for c in candidates:
        if c and not is_site_default_title(c):
            return c
    return ""


def parse_generic(html: str, target: NormalizedURL, body_html: str | None = None,
                  source: str | None = None, page_sections: bool = True) -> Posting:
    """page_sections=False: 본문을 별도 페이지(body_html)에서 받는 사이트. 공고 페이지의
    요약·메뉴 영역을 본문 구간으로 오인하지 않도록 본문 구간은 body_html에서만 찾는다."""
    tree = LexborHTMLParser(html)
    ld = jsonld_jobposting(tree)
    pairs = dl_pairs(tree)

    raw_title = pick_title(tree, ld)
    if not raw_title:
        raise ParseError("공고 제목을 찾지 못함 (차단 화면이거나 공고 페이지가 아님)")

    company = jsonld_company(ld)
    if not company and target.site is GENERIC:
        og_title = meta_content(tree, "og:title")
        if " | " in og_title:
            head, tail = (clean(x) for x in og_title.rsplit(" | ", 1))
            if 1 <= len(tail) <= 30 and tail not in head:
                company = tail
        if not company:
            site_name = meta_content(tree, "og:site_name")
            if site_name and site_name != og_title and len(site_name) <= 30 and not is_site_default_title(site_name):
                company = site_name
        if company and raw_title.endswith(f" | {company}"):
            raw_title = raw_title[: -len(f" | {company}")].strip()

    body_tree_lines = html_to_lines(body_html) if body_html else []
    page_lines = content_lines(LexborHTMLParser(html))

    # JSON-LD는 '경력'처럼 연차 없이 주는 경우가 있어, select로 분류되는 값을 우선한다
    career_raw = pick_classifiable(
        jsonld_experience(ld), find_label(pairs, *CAREER_LABELS), label_in_lines(page_lines, *CAREER_LABELS))
    deadline_raw = find_label(pairs, *DEADLINE_LABELS) or label_in_lines(page_lines, *DEADLINE_LABELS)
    deadline = parse_deadline(deadline_raw, today=today_kst())
    if deadline is None and not deadline_raw and ld and ld.get("validThrough"):
        deadline_raw = str(ld["validThrough"])
        deadline = parse_iso_deadline(deadline_raw)

    location = jsonld_location(ld) or find_label(pairs, *LOCATION_LABELS) or label_in_lines(page_lines, *LOCATION_LABELS)
    employment = (employment_label(ld.get("employmentType")) if ld else None) or \
        find_label(pairs, *EMPLOYMENT_LABELS) or label_in_lines(page_lines, *EMPLOYMENT_LABELS)

    sections = drop_summary_sections(merge_sections(
        extract_sections(body_tree_lines),
        extract_sections(page_lines) if page_sections else {},
        extract_sections(html_to_lines(ld.get("description"))) if page_sections and ld and ld.get("description") else {},
    ))

    if target.site is GENERIC and not ld and not sections and not _looks_like_job(target.url, raw_title):
        raise ParseError("채용공고 페이지로 보이지 않음 (JSON-LD·본문 구간·채용 관련 단어 없음)")

    return Posting(
        source=source or target.site.name,
        url=target.url,
        title=format_title(raw_title, company),
        raw_title=raw_title,
        company=company,
        career_raw=_short(career_raw),
        career_select=classify_career(career_raw),
        employment_type=_short(employment),
        location=_short(clean_location(location)),
        deadline=deadline,
        deadline_raw=_short(deadline_raw),
        expired=False,
        sections=sections,
    )


def pick_classifiable(*candidates: str | None) -> str | None:
    """경력 후보 중 select 값으로 분류되는 첫 값. 없으면 처음 나온 값."""
    present = [c for c in candidates if c]
    for c in present:
        if classify_career(c):
            return c
    return present[0] if present else None


# 공고 요약 영역에 쓰이는 라벨. 구간 항목이 전부 'XX : 값' 형태의 이런 라벨이면 본문이 아니라 요약 영역이다.
SUMMARY_LABELS = {
    "경력", "학력", "모집직무", "직무", "고용형태", "근무형태", "근무지", "근무지역", "급여", "연봉", "마감일",
    "접수기간", "모집인원", "직급", "직책", "채용형태", "모집분야", "기업형태", "업종", "산업",
}


def drop_summary_sections(sections: dict[str, list[str]]) -> dict[str, list[str]]:
    out = {}
    for key, items in sections.items():
        labels = [re.split(r"\s*[:：]\s*", i, maxsplit=1)[0] for i in items if re.search(r"[:：]", i)]
        is_summary = items and len(labels) == len(items) and all(
            re.sub(r"[^가-힣A-Za-z]", "", lb) in SUMMARY_LABELS for lb in labels)
        if not is_summary:
            out[key] = items
    return out


def today_kst():
    from datetime import datetime
    return datetime.now(KST).date()


_JOB_WORDS = re.compile(
    r"채용|모집|공고|인턴|신입|경력|recruit|career|job|position|posting|opening|hiring|vacanc|apply", re.IGNORECASE)


def _looks_like_job(url: str, title: str) -> bool:
    """자체 채용 사이트에서 공고가 아닌 페이지(뉴스, SNS 피드 등)를 저장하지 않기 위한 최소 확인."""
    return bool(_JOB_WORDS.search(url) or _JOB_WORDS.search(title))


_LOCATION_JUNK = re.compile(r"\s*(지도\s*보기|지도보기|주소\s*복사|길찾기|크게보기)\s*[·|]?\s*")


def clean_location(value: str | None) -> str | None:
    if not value:
        return None
    s = _LOCATION_JUNK.sub(" ", value)
    return clean(s.strip(" ·|")) or None


def _short(value: str | None, limit: int = 120) -> str | None:
    value = clean(value)
    if not value:
        return None
    return value if len(value) <= limit else value[:limit].rstrip() + "…"


def meta_value(text: str, label: str) -> str | None:
    """'경력 : 경력 2년이상, 학력 : ...' 같은 메타 설명에서 라벨 값."""
    m = re.search(rf"{label}\s*[:：]\s*([^,<]+)", text or "")
    return clean(m.group(1)) if m else None
