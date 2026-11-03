"""인크루트 공고 파서.

확인한 구조 (2026-11 기준, tests/fixtures 참고): JSON-LD(JobPosting, description에 '경력: ...'). 본문은 jobpostcont.asp?job= 별도 페이지. EUC-KR
"""

from __future__ import annotations

from dataclasses import replace

from selectolax.lexbor import LexborHTMLParser

from app.models import Posting
from app.collection.urls import NormalizedURL
from app.parsers.common import classify_career, parse_deadline, split_label
from app.parsers.generic import parse_generic
from app.parsers.html import html_to_lines, jsonld_jobposting


def parse_incruit(html: str, body_html: str | None, target: NormalizedURL) -> Posting:
    p = parse_generic(html, target, body_html, page_sections=False)
    ld = jsonld_jobposting(LexborHTMLParser(html)) or {}
    desc_lines = html_to_lines(ld.get("description"))
    info = {k: v for k, v in (split_label(x) for x in desc_lines) if k}
    updates: dict = {}
    career = info.get("경력")
    if career:
        updates.update(career_raw=career, career_select=classify_career(career))
    if info.get("고용형태"):
        updates["employment_type"] = info["고용형태"]
    if info.get("접수기간"):
        updates.update(deadline_raw=info["접수기간"], deadline=parse_deadline(info["접수기간"]) or p.deadline)
    return replace(p, **updates)
