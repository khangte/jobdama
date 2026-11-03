"""캐치 공고 파서.

확인한 구조 (2026-11 기준, tests/fixtures 참고): JSON-LD(JobPosting, description에 '경력 : ...'), 본문은 /controls/recruitDetail/{번호} 별도 페이지
  (div#iframe_wrapper 안에 텍스트 또는 이미지)
"""

from __future__ import annotations

import re
from dataclasses import replace

from selectolax.lexbor import LexborHTMLParser

from app.models import Posting
from app.collection.urls import NormalizedURL
from app.parsers.common import (
    classify_career, clean, format_title, parse_deadline, pick_classifiable, split_label, today_kst,
)
from app.parsers.generic import parse_generic
from app.parsers.html import jsonld_jobposting, meta_content


def parse_catch(html: str, body_html: str | None, target: NormalizedURL) -> Posting:
    p = parse_generic(html, target, body_html, page_sections=False)
    tree = LexborHTMLParser(html)
    ld = jsonld_jobposting(tree) or {}
    info = {k: v for k, v in (split_label(x) for x in (ld.get("description") or "").split("\n")) if k}
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
