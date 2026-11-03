"""점핏 공고 파서.

확인한 구조 (2026-11 기준, tests/fixtures 참고): 서버 렌더링 HTML. h1, a.name, dt/dd(주요업무, 자격요건, 우대사항, 경력, 마감일, 근무지역)
"""

from __future__ import annotations

from dataclasses import replace

from selectolax.lexbor import LexborHTMLParser

from app.models import Posting
from app.collection.urls import NormalizedURL
from app.parsers.common import clean, format_title
from app.parsers.generic import parse_generic
from app.parsers.html import meta_content


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
