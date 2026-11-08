"""자동 수집 선별 규칙. 기준값은 CollectConfig(화면 /collect)에서 바꾼다."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from functools import lru_cache

from app.collection.collect_config import CAREER_YEARS, CHOICES, PROVINCE_NAMES, REGION_NAMES, CollectConfig
from app.models import Posting

_RANK = {None: 0, "신입": 0, "신입/경력": 0, "경력무관": 0, "1년 이상": 1, "2년 이상": 2, "3년 이상": 3}
_YEARS_RE = re.compile(r"(\d{1,2})\s*(?:년\s*(?:차\s*)?(?:이상|↑)|(?:년\s*(?:차\s*)?)?[~\-–]\s*\d{1,2}\s*년)")


def with_body_career(posting: Posting) -> Posting:
    """`자격 요건` 본문(우대 줄 제외)의 연차가 더 높으면 경력 선택값을 올린 사본을 돌려준다."""
    years = [
        int(m.group(1))
        for line in posting.sections.get("자격 요건", [])
        if "우대" not in line
        for m in _YEARS_RE.finditer(line)
    ]
    n = max(years, default=0)
    if n <= _RANK.get(posting.career_select, 0):
        return posting
    return replace(posting, career_select=f"{min(n, 3)}년 이상")


@lru_cache(maxsize=32)
def word_re(words: tuple[str, ...]) -> re.Pattern | None:
    """단어들을 한 정규식으로 묶는다(본문 신호·제목 제외 단어 공용). 단어가 없으면 None.

    영문·숫자만인 단어는 ASCII 경계로 본다(\\b는 "Python으로"처럼 한글 조사가 붙으면 경계로 보지 않는다).
    그 밖의 단어는 이스케이프하고 공백은 \\s*로 바꿔 띄어쓰기 차이를 허용한다.
    """
    parts = []
    for w in words:
        if re.fullmatch(r"[a-z0-9]+", w):
            parts.append(rf"(?<![a-z0-9]){w}(?![a-z0-9])")
        else:
            parts.append(r"\s*".join(re.escape(x) for x in w.split()))
    return re.compile("|".join(parts), re.IGNORECASE) if parts else None


def _has_name(location: str, names: tuple[str, ...]) -> bool:
    """시·도 이름이 주소에 단어 시작으로 들어 있는지 본다. "해운대구"의 "대구"처럼 한글 중간에 낀 건 세지 않는다."""
    return any(re.search(rf"(?<![가-힣]){re.escape(n)}", location) for n in names)


def evaluate(posting: Posting, today: date, cfg: CollectConfig) -> str | None:
    """제외 사유를 돌려준다. 통과하면 None. 순서: 마감 → 근무지 → 경력 → 고용형태 → 공고명 → 본문 신호."""
    if posting.expired or (posting.deadline and posting.deadline < today):
        return "마감됨"
    loc = posting.location
    if cfg.region != "any":
        if not loc:
            return "근무지 확인 불가"
        # 시·도 없이 구 이름만 있는 주소(서초구 …)는 통과시킨다
        if not _has_name(loc, REGION_NAMES[cfg.region]) and _has_name(loc, PROVINCE_NAMES):
            return f"근무지 제외: {loc}"
    if cfg.career != "any" and _RANK.get(posting.career_select, 0) > CAREER_YEARS[cfg.career]:
        return f"경력 {CAREER_YEARS[cfg.career] + 1}년 이상"
    et = posting.employment_type
    if cfg.employment != "any" and et and CHOICES["employment"][cfg.employment] not in et:
        return f"고용형태 제외: {et}"
    # 기업명이 붙은 title이 아니라 공고명만 본다. 단어 경계로 찾아 "Linux"의 "ux"에 걸리지 않게 한다
    exclude = word_re(tuple(cfg.title_exclude_words))
    if exclude and (m := exclude.search(posting.raw_title)):
        return f"공고명 제외 단어: {m.group().lower()}"
    pattern = word_re(tuple(cfg.body_signal_words))
    if pattern and posting.body_found:  # 본문을 못 읽은 공고(이미지 등)는 판단 근거가 없으니 통과시킨다
        text = " ".join([posting.title, *(line for lines in posting.sections.values() for line in lines)])
        if not pattern.search(text):
            return "본문에 필수 신호 단어 없음"
    return None


def screen(posting: Posting, today: date, cfg: CollectConfig) -> tuple[Posting, str | None]:
    """본문 경력을 반영한 공고와 제외 사유를 함께 돌려준다."""
    p = with_body_career(posting)
    return p, evaluate(p, today, cfg)
