"""원티드 공고 파서.

확인한 구조 (2026-11 기준, tests/fixtures 참고): __NEXT_DATA__ props.pageProps.initialData (main_tasks, requirements, preferred_points, hire_rounds ...)
"""

from __future__ import annotations

from selectolax.lexbor import LexborHTMLParser

from app.models import Posting
from app.collection.urls import NormalizedURL
from app.parsers.common import classify_career, clean, employment_label, format_title, parse_iso_deadline
from app.parsers.generic import parse_generic
from app.parsers.html import next_data
from app.parsers.sections import itemize, merge_sections


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
