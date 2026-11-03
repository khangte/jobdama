"""자소설닷컴 공고 파서.

확인한 구조 (2026-11 기준, tests/fixtures 참고): __NEXT_DATA__ props.pageProps.initialEmploymentCompany (title, name, end_time, content HTML) + JSON-LD
"""

from __future__ import annotations

from dataclasses import replace

from selectolax.lexbor import LexborHTMLParser

from app.models import Posting
from app.collection.urls import NormalizedURL
from app.parsers.common import clean, format_title, parse_iso_deadline
from app.parsers.generic import parse_generic
from app.parsers.html import html_to_lines, jsonld_jobposting, next_data
from app.parsers.sections import extract_sections


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
