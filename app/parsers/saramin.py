"""사람인 공고 상세 페이지 파서.

확인한 구조 (2026-11 기준)
- 공고 제목: h1.tit_job
- 기업명: a.company
- 핵심 정보: .jv_summary dl > dt/dd (경력, 근무형태, 근무지역 등)
- 접수기간: dl.info_period > dt/dd (마감일)
- 본문: .user_content (인라인)
- 마감 공고: .jv_detail.expired
- 대체 정보: meta description = "기업명, 공고명, 경력:..., 학력:..., ..., 마감일:YYYY-MM-DD, 홈페이지:..."
"""

from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.models import Posting
from app.parsers.common import classify_career, clean, extract_sections, format_title, parse_deadline


from app.parsers.errors import ParseError, PostingMismatch  # noqa: E402,F401  (기존 import 경로 호환)


def _text(node: LexborNode | None) -> str:
    return clean(node.text(separator=" ")) if node is not None else ""


def _meta(tree: LexborHTMLParser, attr: str, value: str) -> str:
    node = tree.css_first(f'meta[{attr}="{value}"]')
    return clean(node.attributes.get("content")) if node is not None else ""


def _dl_pairs(dl: LexborNode) -> list[tuple[str, LexborNode]]:
    pairs: list[tuple[str, LexborNode]] = []
    label: str | None = None
    for child in dl.iter():
        if child.tag == "dt":
            label = _text(child)
        elif child.tag == "dd" and label is not None:
            pairs.append((label, child))
            label = None
    return pairs


def _dd_text(dd: LexborNode) -> str:
    for junk in dd.css("button, .toolTip, .toolTipWrap, svg"):
        junk.decompose()
    return _text(dd)


def is_same_posting(html: str, rec_idx: str) -> bool:
    markers = (
        f"jobsViewDetail_{rec_idx}",
        f"quickApplyForm(&#39;{rec_idx}&#39;",
        f"quickApplyForm('{rec_idx}'",
        f"rec_idx={rec_idx}\"",
        f"rec_idx={rec_idx}&",
    )
    return any(m in html for m in markers)


def parse(html: str, url: str, rec_idx: str) -> Posting:
    tree = LexborHTMLParser(html)

    raw_title = _text(tree.css_first("h1.tit_job"))
    company = _text(tree.css_first("a.company"))
    desc = _meta(tree, "name", "description") or _meta(tree, "property", "og:description")
    desc_parts = [p.strip() for p in desc.split(",")] if desc else []

    if not raw_title:
        og = _meta(tree, "property", "og:title")
        og = re.sub(r"\s*-\s*사람인\s*$", "", og)
        og = re.sub(r"\s*\((?:D-\d+|오늘마감|내일마감|상시채용)\)\s*$", "", og)
        if company and og.startswith(f"[{company}]"):
            og = og[len(company) + 2:].strip()
        raw_title = og
    if not company and desc_parts:
        company = desc_parts[0]

    if not raw_title:
        raise ParseError("공고 제목을 찾지 못함 (차단 화면이거나 페이지 구조가 다름)")
    if not is_same_posting(html, rec_idx):
        raise PostingMismatch("요청한 공고와 다른 페이지가 열림")

    summary: dict[str, str] = {}
    for dl in tree.css(".jv_summary dl"):
        for label, dd in _dl_pairs(dl):
            if label == "경력":
                strong = dd.css_first("strong")
                summary[label] = _text(strong) or _dd_text(dd)
            else:
                summary[label] = _dd_text(dd)

    career_raw = summary.get("경력")
    if not career_raw:
        m = re.search(r"경력:([^,]+)", desc)
        career_raw = clean(m.group(1)) if m else None

    deadline_raw = None
    period = tree.css_first("dl.info_period")
    if period is not None:
        for label, dd in _dl_pairs(period):
            if "마감" in label:
                deadline_raw = _text(dd)
    if not deadline_raw:
        m = re.search(r"마감일:([^,]+)", desc)
        deadline_raw = clean(m.group(1)) if m else None

    expired = tree.css_first(".jv_detail.expired") is not None

    sections: dict[str, list[str]] = {}
    body = tree.css_first(".user_content")
    if body is not None:
        lines = body.text(separator="\n").split("\n")
        sections = extract_sections(lines)

    return Posting(
        source="사람인",
        url=url,
        title=format_title(raw_title, company),
        raw_title=raw_title,
        company=company or None,
        career_raw=career_raw or None,
        career_select=classify_career(career_raw),
        employment_type=summary.get("근무형태") or None,
        location=(summary.get("근무지역") or "").replace("지도보기", "").strip() or None,
        deadline=parse_deadline(deadline_raw),
        deadline_raw=deadline_raw,
        expired=expired,
        sections=sections,
    )
