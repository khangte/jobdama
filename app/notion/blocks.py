"""노션에 보낼 속성 값과 본문 블록을 만든다."""

from __future__ import annotations

from datetime import datetime

from app.models import KST, SECTION_KEYS, Posting

NOT_FOUND_TEXT = "공고에서 확인 불가"
MAX_TEXT = 1900  # 노션 rich_text 한 덩어리 한도(2000자) 여유


def prop_value(ptype: str, value: str) -> dict:
    """열 형식에 맞는 노션 속성 값 본문을 만든다."""
    if ptype in ("select", "multi_select"):
        value = value.replace(",", " ")  # 선택 옵션 이름에는 쉼표를 쓸 수 없다
    return {
        "title": lambda: {"title": [_rt(value)]},
        "rich_text": lambda: {"rich_text": [_rt(value)]},
        "url": lambda: {"url": value},
        "date": lambda: {"date": {"start": value}},
        "select": lambda: {"select": {"name": value}},
        "multi_select": lambda: {"multi_select": [{"name": value}]},
        "status": lambda: {"status": {"name": value}},
        "relation": lambda: {"relation": [{"id": value}]},
    }[ptype]()


def _rt(text: str, link: str | None = None, bold: bool = False) -> dict:
    """rich_text 조각 하나를 만든다."""
    item: dict = {"type": "text", "text": {"content": text[:MAX_TEXT]}}
    if link:
        item["text"]["link"] = {"url": link}
    if bold:
        item["annotations"] = {"bold": True}
    return item


def _heading(text: str) -> dict:
    """heading_2 블록을 만든다."""
    return {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [_rt(text)]}}


def _bullet(text: str, link: str | None = None) -> dict:
    """글머리 블록을 만든다."""
    return {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [_rt(text, link)]}}


def _labeled(label: str, value: str | None) -> dict:
    """'라벨: 값' 글머리 블록을 만든다. 값이 없으면 확인 불가 문구를 넣는다."""
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [_rt(f"{label}: ", bold=True), _rt(value or NOT_FOUND_TEXT)]},
    }


def build_children(p: Posting, collected_at: datetime | None = None) -> list[dict]:
    """공고 페이지 본문 블록 목록을 만든다."""
    collected_at = collected_at or datetime.now(KST)
    blocks: list[dict] = []
    if not p.body_found:
        blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [_rt("상세 페이지 미확인 - 원문 링크에서 직접 확인 필요")],
                "icon": {"type": "emoji", "emoji": "⚠️"},
            },
        })

    if p.deadline:
        deadline = p.deadline.isoformat()
    elif p.deadline_raw:
        deadline = p.deadline_raw
    else:
        deadline = None
    if deadline and p.expired:
        deadline += " (마감된 공고)"

    blocks.append(_heading("공고 요약"))
    blocks += [
        _labeled("기업", p.company),
        _labeled("출처", p.source),
        _labeled("근무지", p.location),
        _labeled("경력 조건", p.career_raw),
        _labeled("고용 형태", p.employment_type),
        _labeled("마감일", deadline),
        _labeled("수집일", collected_at.date().isoformat()),
    ]
    for key in SECTION_KEYS:
        blocks.append(_heading(key))
        items = p.sections.get(key) or []
        blocks += [_bullet(i) for i in items] if items else [_bullet(NOT_FOUND_TEXT)]
    blocks.append(_heading("원문 링크"))
    blocks.append(_bullet(p.url, link=p.url))
    return blocks
