"""사이트 공통 규칙: 경력 분류, 날짜, 공고명 형식, 본문 구간 발췌, HTML·JSON-LD 도우미."""

from __future__ import annotations

import json
import re
from datetime import date

from selectolax.lexbor import LexborHTMLParser, LexborNode

from app.models import KST


# ---------------------------------------------------------------- 텍스트

_WS_RE = re.compile(r"\s+")


def clean(text: str | None) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", text.replace("\xa0", " ")).strip()


# ---------------------------------------------------------------- 기업명

_CORP_RE = re.compile(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\(재\)|재단법인|\(사\)|사단법인|\(합\)")


def normalize_company(name: str | None) -> str:
    """비교용 기업명. (주), 주식회사, 공백, 기호를 지운다."""
    if not name:
        return ""
    s = _CORP_RE.sub("", name)
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s).lower()


def format_title(raw_title: str, company: str | None) -> str:
    """노션 공고명 형식 [기업명] 공고명. 공고명 앞에 기업명이 이미 있으면 지운다."""
    title = clean(raw_title)
    company = clean(company)
    if not company:
        return title
    core = normalize_company(company)
    if core:
        m = re.match(r"^[\[【(]\s*([^\]】)]+?)\s*[\]】)]\s*(.+)$", title)
        if m and normalize_company(m.group(1)) == core:
            title = m.group(2)
        else:
            plain = clean(_CORP_RE.sub("", company))
            for prefix in (company, plain):
                if prefix and title.startswith(prefix):
                    title = title[len(prefix):].lstrip(" -_|:·")
                    break
    return f"[{company}] {title}".strip()


# ---------------------------------------------------------------- 경력

def classify_career(raw: str | None) -> str | None:
    """노션 경력 select 값. 확인 못 하면 None.

    순서: 신입/경력 → 경력무관 → 신입 → 최소 연차(1년 이상, 2년 이상, 3년 이상)
    '경력무관(신입포함)'처럼 무관 표기가 있으면 신입보다 경력무관을 우선한다.
    """
    s = clean(raw)
    if not s:
        return None
    has_new = "신입" in s or "졸업예정" in s
    has_exp = re.search(r"경력(?!\s*무관)", s) is not None or re.search(r"\d+\s*년", s) is not None
    if has_new and has_exp:
        return "신입/경력"
    if "무관" in s:
        return "경력무관"
    if has_new:
        return "신입"
    m = re.search(r"(\d+)\s*(?:년|~|-|–|↑|이상)", s)
    if m:
        years = int(m.group(1))
        if years <= 0:
            return "신입/경력"
        if years == 1:
            return "1년 이상"
        if years == 2:
            return "2년 이상"
        return "3년 이상"
    return None


# ---------------------------------------------------------------- 날짜

_DATE_RE = re.compile(r"(20\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
_OPEN_ENDED = ("채용시", "채용 시", "상시", "수시")


_MD_RE = re.compile(r"(?<![\d.])(\d{1,2})\s*[./월]\s*(\d{1,2})(?!\d)")


def parse_deadline(raw: str | None, today: date | None = None) -> date | None:
    """마감일 문자열 → 날짜. 기간이면 끝 날짜.

    today를 주면 '~11/30', '11.30(일)'처럼 연도 없는 날짜도 읽는다(지난 날짜면 다음 해로 본다).
    """
    s = clean(raw)
    if not s or any(k in s for k in _OPEN_ENDED):
        return None
    found = _DATE_RE.findall(s)
    if not found:
        if today is None:
            return None
        md = _MD_RE.findall(re.sub(r"\d{1,2}:\d{2}", "", s))
        if not md:
            return None
        mo, d = (int(x) for x in md[-1])
        try:
            guess = date(today.year, mo, d)
        except ValueError:
            return None
        if (today - guess).days > 60:
            guess = date(today.year + 1, mo, d)
        return guess
    y, mo, d = found[-1]  # '2026.10.22 ~ 2026.11.05' 같은 기간이면 끝 날짜
    try:
        return date(int(y), int(mo), int(d))
    except ValueError:
        return None


def parse_iso_deadline(value: str | None) -> date | None:
    """ISO 일시. 시간대가 있으면 한국 시간 날짜로 바꾼다."""
    if not value:
        return None
    from datetime import datetime

    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return parse_deadline(s)
    if dt.tzinfo is not None:
        dt = dt.astimezone(KST)
    return dt.date()


# ---------------------------------------------------------------- 본문 구간

SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "주요 업무": ("주요업무", "담당업무", "업무내용", "수행업무", "업무소개", "하는일", "합류하면함께할업무", "이런일을해요",
              "직무내용", "직무소개", "업무상세", "responsibilities", "whatyouwilldo", "whatyoulldo", "jobdescription"),
    "자격 요건": ("자격요건", "지원자격", "필수요건", "필수자격", "자격조건", "지원요건", "이런분을찾아요", "필요역량",
              "requirements", "qualifications", "minimumqualifications"),
    "우대 사항": ("우대사항", "우대조건", "우대요건", "이런분이면더좋아요", "preferredqualifications", "nicetohave",
              "pluses", "preferred"),
    "전형 절차": ("전형절차", "채용절차", "채용프로세스", "전형과정", "채용과정", "전형방법", "hiringprocess",
              "recruitmentprocess"),
}

STOP_KEYWORDS: tuple[str, ...] = (
    "근무조건", "근무환경", "근무형태", "근무지", "근무시간", "근무일시", "복리후생", "복지", "혜택",
    "접수기간", "접수방법", "지원방법", "제출서류", "회사소개", "회사정보", "팀소개", "포지션소개",
    "모집부문", "모집분야", "모집부분", "모집인원", "기타사항", "유의사항", "참고사항", "기술스택",
    "급여", "연봉", "문의", "채용조건", "우리가함께일하고싶은", "함께일하고싶은", "인재상",
    "꼭읽어주세요", "기업문화", "관련기사", "지원해야하는이유", "benefits", "perks", "aboutus",
    "abouttheteam", "공유하기", "지원하기", "기업정보", "기업소개", "회사위치",
)
# 구간 제목은 '<개발자 주요 업무>'처럼 앞뒤 수식어를 넉넉히 허용하고,
# 종료 제목은 '[실무 경험 및 기술 스택]' 같은 구간 안 소제목을 오인하지 않도록 좁게 본다.
_SECTION_SLACK = 6
_STOP_SLACK = 3

MAX_ITEMS_PER_SECTION = 15
MAX_ITEM_LEN = 300

_BULLET_RE = re.compile(r"^\s*(?:[-–—•·ㆍ*▪▶▷○●■□◆◇✔✓☑➤►✅▸]+|\d{1,2}[.)](?!\d)|[①-⑳])\s*")
_LABEL_RE = re.compile(r"^(.{1,24}?)\s*[:：]\s*(.*)$")
_SUBHEADING_RE = re.compile(r"^[\[【<].{1,40}[\]】>]$")


def _core(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text).lower()


def _match(core: str, keywords: tuple[str, ...], slack: int) -> bool:
    return any(core == kw or core.startswith(kw) or (kw in core and len(core) <= len(kw) + slack) for kw in keywords)


def classify_heading(line: str) -> tuple[str, str] | None:
    """제목 줄이면 (구간 키 또는 'STOP', 같은 줄의 나머지 내용)을 돌려준다."""
    if _BULLET_RE.match(line) and not line.lstrip().startswith(("*", "**")):
        return None
    label, rest = line, ""
    m = _LABEL_RE.match(line)
    if m:
        label, rest = m.group(1), m.group(2)
    core = _core(label)
    if not core or len(core) > 30:
        return None
    for key, kws in SECTION_KEYWORDS.items():
        if len(core) <= 16 and _match(core, kws, slack=_SECTION_SLACK):
            return key, clean(rest)
        if len(core) <= 30 and core.isascii() and _match(core, kws, slack=10):
            return key, clean(rest)
    if _match(core, STOP_KEYWORDS, slack=_STOP_SLACK):
        return "STOP", ""
    return None


def _strip_bullet(line: str) -> tuple[bool, str]:
    m = _BULLET_RE.match(line)
    if not m:
        return False, line
    return True, line[m.end():].strip()


class _Items:
    """한 구간의 항목 목록. 글머리표 없는 줄은 앞 글머리표 항목에 이어 붙인다(줄바꿈 복원)."""

    def __init__(self) -> None:
        self.items: list[str] = []
        self.prev_bulleted = False
        self.pending_bullet = False

    def add(self, line: str) -> None:
        bulleted, text = _strip_bullet(line)
        if bulleted and not text:
            self.pending_bullet = True
            return
        if self.pending_bullet:
            bulleted, self.pending_bullet = True, False
        if _SUBHEADING_RE.match(text):
            self._append(text)
            self.prev_bulleted = False
            return
        if not bulleted and self.prev_bulleted and self.items:
            self.items[-1] = (self.items[-1] + " " + text)[:MAX_ITEM_LEN]
            return
        if self._append(text):
            self.prev_bulleted = bulleted

    def _append(self, text: str) -> bool:
        if len(self.items) >= MAX_ITEMS_PER_SECTION:
            return False
        self.items.append(text[:MAX_ITEM_LEN])
        return True

    def reset_flow(self) -> None:
        self.prev_bulleted = False
        self.pending_bullet = False

    def result(self) -> list[str]:
        return list(dict.fromkeys(i for i in self.items if i))


def itemize(lines: list[str] | str) -> list[str]:
    """제목 없이 한 구간의 내용만 있을 때 항목 목록으로 만든다."""
    if isinstance(lines, str):
        lines = lines.split("\n")
    acc = _Items()
    for raw in lines:
        line = clean(raw)
        if line:
            acc.add(line)
    return acc.result()


def extract_sections(lines: list[str]) -> dict[str, list[str]]:
    """줄 목록에서 주요 업무, 자격 요건, 우대 사항, 전형 절차 구간을 발췌한다.

    같은 구간 제목이 여러 번 나오면(모집 부문이 여러 개) 이어 붙인다.
    """
    sections: dict[str, _Items] = {}
    current: str | None = None
    for raw in lines:
        line = clean(raw)
        if not line:
            continue
        heading = classify_heading(line)
        if heading:
            key, rest = heading
            current = None if key == "STOP" else key
            if current is not None:
                acc = sections.setdefault(current, _Items())
                acc.reset_flow()
                if rest:
                    acc.add(rest)
                    acc.reset_flow()
            continue
        if current is not None:
            sections[current].add(line)
    return {k: v for k, v in ((k, acc.result()) for k, acc in sections.items()) if v}


def merge_sections(*parts: dict[str, list[str]]) -> dict[str, list[str]]:
    """앞의 결과를 우선하고 비어 있는 구간만 뒤의 결과로 채운다."""
    out: dict[str, list[str]] = {}
    for part in parts:
        for k, v in (part or {}).items():
            if v and not out.get(k):
                out[k] = v
    return out


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
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
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


# ---------------------------------------------------------------- 값 변환

EMPLOYMENT_TYPES = {
    "full_time": "정규직", "fulltime": "정규직", "regular": "정규직", "permanent": "정규직",
    "contract": "계약직", "contractor": "계약직", "temporary": "계약직",
    "intern": "인턴", "internship": "인턴", "part_time": "파트타임", "parttime": "파트타임",
    "military_service_exception": "병역특례", "dispatch": "파견직", "freelance": "프리랜서",
}


def employment_label(value) -> str | None:
    if not value:
        return None
    values = value if isinstance(value, list) else [value]
    out = []
    for v in values:
        s = clean(str(v))
        out.append(EMPLOYMENT_TYPES.get(s.lower().replace("-", "_"), s))
    return ", ".join(dict.fromkeys(x for x in out if x)) or None


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


SITE_TITLE_TOKENS = (
    "사람인", "잡코리아", "인크루트 채용정보", "인크루트 채용", "인크루트", "원티드", "점핏", "캐치", "자소설닷컴",
    "프로그래머스 커리어", "프로그래머스", "Wanted", "JOBKOREA", "Programmers",
)


def clean_page_title(title: str | None) -> str:
    """'공고명 | 사이트명', '점핏 | 공고명' 같은 사이트 이름 꼬리표를 지운다."""
    t = clean(title)
    changed = True
    while t and changed:
        changed = False
        for token in SITE_TITLE_TOKENS:
            for sep in (" | ", " - ", " : ", " · "):
                if t.endswith(sep + token):
                    t, changed = t[: -len(sep + token)].strip(), True
                if t.startswith(token + sep):
                    t, changed = t[len(token + sep):].strip(), True
    t = re.sub(r"\s*\((?:D-\d+|오늘마감|내일마감|상시채용|채용시 마감|~[^)]{1,20})\)\s*$", "", t)
    return t


def is_site_default_title(title: str) -> bool:
    core = _core(title)
    if not core:
        return True
    return any(core == _core(tok) for tok in SITE_TITLE_TOKENS) or core in ("채용", "채용공고", "채용정보", "recruit", "careers")
