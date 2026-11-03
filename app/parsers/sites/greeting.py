"""그리팅 공고 파서.

확인한 구조 (2026-11 기준, tests/fixtures 참고): __NEXT_DATA__ dehydratedState 의 getOpeningById (openingsInfo.detail HTML, jobPositions)
"""

from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser

from app.models import Posting
from app.collection.urls import GREETING, NormalizedURL
from app.parsers.common import classify_career, clean, employment_label, format_title, parse_iso_deadline
from app.parsers.generic import parse_generic
from app.parsers.html import html_to_lines, next_data
from app.parsers.sections import extract_sections


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
