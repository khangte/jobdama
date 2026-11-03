"""잡코리아 공고 파서.

확인한 구조 (2026-11 기준, tests/fixtures 참고): JSON-LD(JobPosting) + 메타 설명 '경력 : ..., 마감일 : ...'. 본문은 GI_Read_Comt_Ifrm?Gno= 별도 페이지
"""

from __future__ import annotations

import re
from dataclasses import replace

from selectolax.lexbor import LexborHTMLParser

from app.models import Posting
from app.collection.urls import NormalizedURL
from app.parsers.common import classify_career, clean, parse_deadline
from app.parsers.generic import parse_generic
from app.parsers.html import meta_content


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


def meta_value(text: str, label: str) -> str | None:
    """'경력 : 경력 2년이상, 학력 : ...' 같은 메타 설명에서 라벨 값."""
    m = re.search(rf"{label}\s*[:：]\s*([^,<]+)", text or "")
    return clean(m.group(1)) if m else None
