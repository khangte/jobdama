"""사이트 공통 규칙: 텍스트 정리, 기업명·공고명 형식, 경력 분류, 날짜, 값 변환."""

from __future__ import annotations

import re
from datetime import date

from app.models import KST


# ---------------------------------------------------------------- 텍스트

_WS_RE = re.compile(r"\s+")


def clean(text: str | None) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", text.replace("\xa0", " ")).strip()


def _core(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text).lower()


# '경력 : 신입'처럼 라벨과 값이 한 줄에 있는 형태
_LABEL_RE = re.compile(r"^(.{1,24}?)\s*[:：]\s*(.*)$")


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
        # 괄호 종류별로 짝을 맞춘다: '[(주)X]'처럼 안쪽에 ')'가 있어도 끊기지 않게
        m = re.match(r"^(?:\[\s*([^\]]+?)\s*\]|【\s*([^】]+?)\s*】|\(\s*([^)]+?)\s*\))\s*(.+)$", title)
        if m and normalize_company(m.group(1) or m.group(2) or m.group(3)) == core:
            title = m.group(4)
        else:
            title = re.sub(rf"^(?:{_CORP_RE.pattern})\s*", "", title)  # '(주)X 공고'의 앞머리 (주)
            plain = clean(_CORP_RE.sub("", company))
            for prefix in (company, plain):
                if prefix and title.startswith(prefix):
                    rest = title[len(prefix):]
                    if rest[:1].isalnum():  # '토스'와 '토스뱅크'처럼 단어 중간이면 자르지 않는다
                        continue
                    title = rest.lstrip(" -_|:·")
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
    # 최소 연차가 적힌 표기(N년 이상, N년↑, N~M년)를 먼저 본다. '1~3년 이하'도 최소 1년이다.
    m = re.search(r"(\d+)\s*년?\s*(?:이상|↑|[~\-–]\s*\d)", s)
    if m is None:
        if re.search(r"\d+\s*년?\s*(?:이하|미만|↓)", s):  # 상한만 있으면 최소 연차를 알 수 없다
            return None
        m = re.search(r"(\d+)\s*(?:년|~|-|–)", s)  # '경력 3년', '3년~'
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



def pick_classifiable(*candidates: str | None) -> str | None:
    """경력 후보 중 select 값으로 분류되는 첫 값. 없으면 처음 나온 값."""
    present = [c for c in candidates if c]
    for c in present:
        if classify_career(c):
            return c
    return present[0] if present else None



def today_kst():
    from datetime import datetime
    return datetime.now(KST).date()


def split_label(line: str) -> tuple[str | None, str]:
    """'라벨: 값' 한 줄을 (라벨, 값)으로. 콜론이 없으면 (None, '')."""
    if ":" in line:
        k, v = line.split(":", 1)
        return clean(k), clean(v)
    return None, ""
