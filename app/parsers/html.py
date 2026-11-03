"""HTML·JSON-LD 도우미: 줄 추출, 메타값, dt/dd 쌍, JSON-LD 필드."""

from __future__ import annotations

import json
import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.parsers.common import _LABEL_RE, _core, clean
from app.parsers.sections import classify_heading


# ---------------------------------------------------------------- HTML 도우미

def node_lines(node: LexborNode | None) -> list[str]:
    if node is None:
        return []
    for junk in node.css("script, style, noscript, svg, template"):
        junk.decompose()
    return [ln for ln in (clean(x) for x in node.text(separator="\n").split("\n")) if ln]


def html_to_lines(fragment: str | None) -> list[str]:
    if not fragment:
        return []
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.IGNORECASE)
    tree = LexborHTMLParser(f"<div id='__root'>{fragment}</div>")
    return node_lines(tree.css_first("#__root"))


def meta_content(tree: LexborHTMLParser, *keys: str) -> str:
    """og:title, description 같은 메타값. 먼저 찾은 값을 돌려준다."""
    for key in keys:
        for attr in ("property", "name"):
            node = tree.css_first(f'meta[{attr}="{key}"]')
            if node is not None:
                value = clean(node.attributes.get("content"))
                if value:
                    return value
    return ""


def jsonld_jobposting(tree: LexborHTMLParser) -> dict | None:
    """문서 순서상 첫 JobPosting. 페이지 아래쪽의 추천 공고 JSON-LD보다 본 공고가 먼저 나온다."""
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text(deep=True) or "")
        except ValueError:
            continue
        stack = [data]
        while stack:
            item = stack.pop(0)
            if isinstance(item, list):
                stack[0:0] = item
            elif isinstance(item, dict):
                typ = item.get("@type")
                if typ == "JobPosting" or (isinstance(typ, list) and "JobPosting" in typ):
                    return item
                if "@graph" in item:
                    stack.append(item["@graph"])
    return None


def next_data(tree: LexborHTMLParser) -> dict | None:
    node = tree.css_first("script#__NEXT_DATA__")
    if node is None:
        return None
    try:
        return json.loads(node.text(deep=True) or "")
    except ValueError:
        return None


def dl_pairs(tree: LexborHTMLParser | LexborNode) -> dict[str, str]:
    """페이지의 dt/dd 쌍. 같은 라벨은 처음 값을 쓴다."""
    out: dict[str, str] = {}
    for dl in tree.css("dl"):
        label: str | None = None
        for child in dl.iter():
            if child.tag == "dt":
                label = clean(child.text(separator=" "))
            elif child.tag == "dd" and label:
                for junk in child.css("button, svg, script, style, .toolTip, .toolTipWrap"):
                    junk.decompose()
                out.setdefault(label, clean(child.text(separator=" ")))
                label = None
    return out


def find_label(pairs: dict[str, str], *labels: str) -> str | None:
    for want in labels:
        for k, v in pairs.items():
            if _core(k) == _core(want) and v:
                return v
    return None


def label_in_lines(lines: list[str], *labels: str) -> str | None:
    """'경력 : 신입' 또는 '경력' 다음 줄 값 형태로 적힌 라벨 값."""
    cores = {_core(x) for x in labels}
    for i, line in enumerate(lines):
        m = _LABEL_RE.match(line)
        if m and _core(m.group(1)) in cores and clean(m.group(2)):
            return clean(m.group(2))
        if _core(line) in cores and i + 1 < len(lines):
            nxt = lines[i + 1]
            if not classify_heading(nxt) and len(nxt) <= 80:
                return nxt
    return None


def jsonld_company(ld: dict | None) -> str | None:
    if not ld:
        return None
    org = ld.get("hiringOrganization")
    if isinstance(org, list):
        org = org[0] if org else None
    if isinstance(org, dict):
        return clean(org.get("name")) or None
    if isinstance(org, str):
        return clean(org) or None
    return None


def jsonld_location(ld: dict | None) -> str | None:
    if not ld:
        return None
    loc = ld.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if not isinstance(loc, dict):
        return None
    addr = loc.get("address")
    if isinstance(addr, str):
        return clean(addr) or None
    if isinstance(addr, dict):
        return join_address(addr.get("addressRegion"), addr.get("addressLocality"), addr.get("streetAddress"))
    return None


def _in_address(part: str, street: str) -> bool:
    """'경기'·'경기도', '서울'·'서울특별시'처럼 표기가 달라도 이미 들어 있으면 True."""
    if part in street:
        return True
    stem = re.sub(r"(특별자치도|특별자치시|특별시|광역시|도|시)$", "", part)
    return len(stem) >= 2 and stem in street


def join_address(region: str | None, locality: str | None, street: str | None) -> str | None:
    """시·도, 시·군·구를 도로명 앞에 붙인다. 도로명에 이미 들어 있으면 붙이지 않는다.

    서울/구로구/디지털로26길 72 → 서울 구로구 디지털로26길 72
    경기/성남시/분당구 대왕판교로645번길 16 → 경기 성남시 분당구 대왕판교로645번길 16
    경기도/성남시/경기도 성남시 분당구 새나리로 25 → 그대로
    """
    street = clean(street)
    prefix = [p for p in (clean(region), clean(locality)) if p and not (street and _in_address(p, street))]
    return " ".join(prefix + ([street] if street else [])) or None


def jsonld_experience(ld: dict | None) -> str | None:
    if not ld:
        return None
    exp = ld.get("experienceRequirements")
    if isinstance(exp, list):
        exp = "·".join(clean(str(x)) for x in exp if x)
    elif isinstance(exp, dict):
        months = exp.get("monthsOfExperience")
        exp = f"경력 {int(months) // 12}년 이상" if isinstance(months, (int, float)) and months >= 12 else None
    exp = clean(exp) if isinstance(exp, str) else None
    if exp and exp.lower() in ("no requirements", "none"):
        return None
    return exp or None
