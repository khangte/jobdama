"""본문 구간 발췌: 주요 업무, 자격 요건, 우대 사항, 전형 절차."""

from __future__ import annotations

import re

from app.parsers.common import _LABEL_RE, _core, clean


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
_SUBHEADING_RE = re.compile(r"^[\[【<].{1,40}[\]】>]$")


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
