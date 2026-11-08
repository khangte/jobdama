"""자동 수집 조건 모델. 화면(/collect)에서 바꾸고 data/collect_settings.json에 저장한다."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator
from pydantic_core import PydanticCustomError

log = logging.getLogger("jobdama.collect_config")

# 지원하는 수집 사이트 키(화면 표시·정렬 순서의 기준)
SITE_KEYS = ("saramin", "jobkorea", "wanted", "incruit", "jumpit")
# 사이트 키 → 화면에 보여줄 한글 이름
SITE_NAMES = {"saramin": "사람인", "jobkorea": "잡코리아", "wanted": "원티드", "incruit": "인크루트", "jumpit": "점핏"}

# 모든 선택지에 공통으로 들어가는 "무관" 항목
_ANY = {"any": "무관"}
# 필드 이름 → {선택 키: 화면 표시 이름}. 검증(_choice)과 화면 선택 상자가 함께 쓴다
CHOICES: dict[str, dict[str, str]] = {
    "job": {**_ANY, "backend": "백엔드·서버", "frontend": "프론트엔드", "app": "앱", "data_engineer": "데이터 엔지니어",
            "ml": "AI·머신러닝", "devops": "DevOps·인프라"},
    "region": {**_ANY, "seoul_gyeonggi": "서울·경기", "seoul": "서울", "gyeonggi": "경기", "incheon": "인천",
                "busan": "부산", "daegu": "대구", "gwangju": "광주", "daejeon": "대전", "ulsan": "울산", "sejong": "세종",
                "gangwon": "강원", "chungbuk": "충북", "chungnam": "충남", "jeonbuk": "전북", "jeonnam": "전남",
                "gyeongbuk": "경북", "gyeongnam": "경남", "jeju": "제주"},
    "career": {**_ANY, "new": "신입", "upto1": "1년 이하", "upto2": "2년 이하"},
    "education": {**_ANY, "high": "고졸", "college": "초대졸", "bachelor": "대졸(4년)", "master": "석사", "doctor": "박사"},
    "employment": {**_ANY, "fulltime": "정규직", "contract": "계약직", "intern": "인턴", "freelance": "프리랜서"},
    "salary": {**_ANY, "3000": "3,000만 원 이상", "4000": "4,000만 원 이상", "5000": "5,000만 원 이상"},
    "sort": {**_ANY, "latest": "최신순", "relevance": "관련도순"},
}
# 근무지 키 → 선별에서 주소에 있는지 볼 시·도 이름(별칭 포함). "충청" 같은 단독 별칭은 북·남을 못 가르므로 뺀다
REGION_NAMES: dict[str, tuple[str, ...]] = {
    "seoul_gyeonggi": ("서울", "경기"), "seoul": ("서울",), "gyeonggi": ("경기",), "incheon": ("인천",),
    "busan": ("부산",), "daegu": ("대구",), "gwangju": ("광주",), "daejeon": ("대전",), "ulsan": ("울산",),
    "sejong": ("세종",), "gangwon": ("강원",), "chungbuk": ("충북", "충청북도"), "chungnam": ("충남", "충청남도"),
    "jeonbuk": ("전북", "전라북도"), "jeonnam": ("전남", "전라남도"), "gyeongbuk": ("경북", "경상북도"),
    "gyeongnam": ("경남", "경상남도"), "jeju": ("제주",),
}
# REGION_NAMES에 나오는 모든 시·도 이름을 중복 없이 모은 목록(주소에서 시·도를 찾을 때 쓴다)
PROVINCE_NAMES: tuple[str, ...] = tuple(dict.fromkeys(n for names in REGION_NAMES.values() for n in names))
# 경력 선택 키 → 허용하는 최대 경력 연차
CAREER_YEARS = {"new": 0, "upto1": 1, "upto2": 2}

# 설정 파일을 못 읽어 기본값으로 시작했을 때 화면에 띄우는 경고 문구
CONFIG_PROBLEM = "설정 파일을 읽지 못해 기본값을 쓰고 있어요. 저장하면 새로 만들어요."
# 선별 단어 한 개의 최대 글자 수 / 단어 목록의 최대 개수 / 검색어의 최대 글자 수
MAX_WORD_LEN, MAX_WORDS, MAX_KEYWORD_LEN = 30, 100, 30

# 제목에 있으면 공고에서 뺄 단어 기본값(시니어·프론트엔드·비개발 직군 등)
DEFAULT_TITLE_EXCLUDE_WORDS = (
    "시니어", "senior", "팀장", "테크리드", "팀리드", "lead", "leader", "head", "principal",
    "프론트엔드", "frontend", "front-end", "UI", "UX", "웹퍼블리셔",
    "ios", "android", "안드로이드", "퍼블리셔", 
    "로봇", "robot", "QA", "검증", "검수", "품질", "품질보증",
    "디자이너", "강사", "영업",
    "기획", "마케팅", "인사담당", "인사팀", "재무", "회계", "총무", "법무", "CS", "고객센터", "사무"
)
# 제목·본문에 하나는 있어야 통과하는 필수 신호 단어 기본값(파이썬·데이터 파이프라인 계열)
DEFAULT_BODY_SIGNAL_WORDS = (
    "python", "파이썬", "django", "fastapi", "flask",
    "데이터 엔지니어", "data engineer", "데이터 엔지니어링", "data engineering", "etl", "elt",
    "데이터 파이프라인", "data pipeline"
)


def _error(field: str, message: str, **ctx) -> PydanticCustomError:
    """오류 type을 필드 이름으로 낸다. 화면이 type으로 오류를 해당 필드에 붙인다."""
    return PydanticCustomError(field, message, ctx or None)


class CollectConfig(BaseModel):
    """자동 수집 조건: 사이트 공통 검색 필터 8개와 우리 쪽 선별 조건 4개."""

    # 수집할 사이트 키 목록
    sites: list[str] = Field(default_factory=lambda: list(SITE_KEYS))
    # 사이트당 가져올 최대 공고 수(1~40)
    max_per_site: int = 20
    # 검색어(최대 30자, cp949로 인코딩 가능해야 함)
    keyword: str = "python"
    # 직무 필터(CHOICES["job"] 키)
    job: str = "any"
    # 근무지 필터(CHOICES["region"] 키)
    region: str = "seoul_gyeonggi"
    # 경력 필터: 허용하는 최대 경력(CHOICES["career"] 키)
    career: str = "upto2"
    # 학력 필터(CHOICES["education"] 키)
    education: str = "any"
    # 고용 형태 필터(CHOICES["employment"] 키)
    employment: str = "any"
    # 최소 연봉 필터(CHOICES["salary"] 키, 단위 만 원)
    salary: str = "any"
    # 검색 결과 정렬 기준(CHOICES["sort"] 키)
    sort: str = "latest"
    # 선별: 제목에 이 단어가 있으면 제외(소문자 비교)
    title_exclude_words: list[str] = Field(default_factory=lambda: list(DEFAULT_TITLE_EXCLUDE_WORDS), validate_default=True)
    # 선별: 제목·본문에 이 단어가 하나도 없으면 제외, 목록이 비면 검사 안 함(소문자 비교)
    body_signal_words: list[str] = Field(default_factory=lambda: list(DEFAULT_BODY_SIGNAL_WORDS), validate_default=True)

    @field_validator("keyword", mode="before")
    @classmethod
    def _keyword_type(cls, v: object) -> object:
        """문자열이 아닌 값은 필드 이름의 오류로 막는다."""
        if not isinstance(v, str):
            raise _error("keyword", "검색어를 글자로 입력하세요.")
        return v

    @field_validator("sites", "title_exclude_words", "body_signal_words", mode="before")
    @classmethod
    def _list_type(cls, v: object, info) -> object:
        """문자열 목록이 아닌 값은 필드 이름의 오류로 막는다."""
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise _error(info.field_name, "목록 형식이 올바르지 않아요.")
        return v

    @field_validator("keyword")
    @classmethod
    def _keyword(cls, v: str) -> str:
        """앞뒤 공백을 지우고 길이와 cp949 인코딩 가능 여부를 본다(인크루트가 cp949로 받는다)."""
        v = v.strip()
        if len(v) > MAX_KEYWORD_LEN:
            raise _error("keyword", "검색어는 30자 이내로 입력하세요.")
        try:
            v.encode("cp949")
        except UnicodeEncodeError:
            raise _error("keyword", "인크루트 검색에 쓸 수 없는 글자가 있어요.") from None
        return v

    @field_validator("sites")
    @classmethod
    def _sites(cls, v: list[str]) -> list[str]:
        """모르는 사이트를 막고 중복을 지운 뒤 SITE_KEYS 순서로 정렬한다."""
        for key in v:
            if key not in SITE_KEYS:
                raise _error("sites", "알 수 없는 사이트예요: {site}", site=key)
        if not v:
            raise _error("sites", "사이트를 하나 이상 고르세요.")
        return [k for k in SITE_KEYS if k in v]

    @field_validator("max_per_site", mode="before")
    @classmethod
    def _max_per_site(cls, v: object) -> int:
        """정수(또는 정수 문자열) 1~40만 받는다."""
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v)
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 40:
            raise _error("max_per_site", "1~40 사이 숫자를 입력하세요.")
        return v

    @field_validator("job", "region", "career", "education", "employment", "salary", "sort", mode="before")
    @classmethod
    def _choice(cls, v: object, info) -> object:
        """CHOICES에 있는 키만 받는다."""
        if not isinstance(v, str) or v not in CHOICES[info.field_name]:
            raise _error(info.field_name, "목록에서 고르세요.")
        return v

    @field_validator("title_exclude_words", "body_signal_words")
    @classmethod
    def _words(cls, v: list[str], info) -> list[str]:
        """공백을 지우고 소문자로 바꾼 뒤 빈 줄·중복을 버리고 단어·목록 길이를 제한한다."""
        words = list(dict.fromkeys(w for w in (x.strip().lower() for x in v) if w))
        for w in words:
            if len(w) > MAX_WORD_LEN:
                raise _error(info.field_name, "단어는 30자 이내로 입력하세요: {word}", word=w)
        if len(words) > MAX_WORDS:
            raise _error(info.field_name, "단어는 100개까지 넣을 수 있어요.")
        return words


def load_config(path: Path) -> tuple[CollectConfig, str | None]:
    """저장된 설정을 읽는다. 파일이 없으면 기본값, 읽기·형식·검증에 실패하면 기본값과 경고 문구."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return CollectConfig(), None
    except (OSError, UnicodeDecodeError) as e:  # UTF-8이 아닌 파일도 기본값으로 시작한다
        log.warning("수집 설정 읽기 실패: %s", e)
        return CollectConfig(), CONFIG_PROBLEM
    try:
        return CollectConfig.model_validate(json.loads(text)), None
    except (ValueError, ValidationError) as e:  # JSONDecodeError와 ValidationError 모두 ValueError
        log.warning("수집 설정이 올바르지 않아 기본값을 씀: %s", e)
        return CollectConfig(), CONFIG_PROBLEM


def save_config(path: Path, cfg: CollectConfig) -> None:
    """같은 폴더의 임시 파일에 쓴 뒤 교체해, 쓰다 끊겨도 이전 설정이 깨지지 않게 한다. OSError는 그대로 올린다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
