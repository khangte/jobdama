"""노션 페이지 값을 읽는 순수 도우미. notion_client에 의존하지 않는다."""

from __future__ import annotations

import re

from app.models import PROVINCE_NAMES
from app.parsers.common import normalize_company

_LEAD_BRACKETS_RE = re.compile(r"^\s*(?:\[([^\]]*)\]|【([^】]*)】|\(([^)]*)\))\s*")
# 공고를 가르는 괄호 안 단어: 이 단어로만 된 머리말은 지우지 않는다("[서울반도체]"처럼 기업명 일부는 부분 일치로 보지 않는다)
_KEEP_BRACKET_WORDS = frozenset({"신입", "경력", "인턴", "계약직", "정규직", *PROVINCE_NAMES})
_BRACKET_SPLIT_RE = re.compile(r"[\s/·,]+")
_SPACES_RE = re.compile(r"\s+")


def normalize_title(title: str, company: str | None) -> str:
    """제목 중복 비교용 키. 앞쪽 괄호 머리말·공백·기업명을 지우고 소문자로 바꾼다. 신입·경력·근무지만 든 괄호는 남긴다."""
    while (m := _LEAD_BRACKETS_RE.match(title)):
        words = [w for w in _BRACKET_SPLIT_RE.split("".join(g or "" for g in m.groups()).strip()) if w]
        if words and all(w in _KEEP_BRACKET_WORDS for w in words):
            break
        title = title[m.end():]
    key = _SPACES_RE.sub("", title.lower())
    for name in (normalize_company(company), _SPACES_RE.sub("", (company or "").lower())):
        if name:
            key = key.replace(name, "")
    return key


def is_closed(page: dict) -> bool:
    """페이지의 상태·선택 속성 값에 '종료'가 들어 있는지."""
    for prop in page.get("properties", {}).values():
        kind = prop.get("type")
        if kind in ("status", "select") and "종료" in ((prop.get(kind) or {}).get("name") or ""):
            return True
    return False


def page_url(page: dict) -> str:
    """페이지의 노션 URL. 응답에 url이 없으면 ID로 만든다."""
    return page.get("url") or f"https://www.notion.so/{page['id'].replace('-', '')}"
