from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

SECTION_KEYS = ("주요 업무", "자격 요건", "우대 사항", "전형 절차")


class JobStatus(str, Enum):
    PROCESSING = "처리 중"
    SAVED = "저장 완료"
    PARTIAL = "부분 저장"
    EXISTS = "이미 있음"
    FETCH_FAILED = "수집 실패"
    SAVE_FAILED = "저장 실패"
    INVALID = "입력 오류"
    SKIPPED = "저장 안 함"  # 자동 수집에서 선별 조건에 걸려 저장하지 않은 공고
    PREVIEW = "저장 대상"  # 자동 수집 미리보기에서 조건을 통과한 공고

    @property
    def css(self) -> str:
        """화면 배지·카드에 붙일 클래스 이름."""
        return {
            JobStatus.PROCESSING: "processing",
            JobStatus.SAVED: "saved",
            JobStatus.PARTIAL: "partial",
            JobStatus.EXISTS: "exists",
            JobStatus.FETCH_FAILED: "failed",
            JobStatus.SAVE_FAILED: "failed",
            JobStatus.INVALID: "failed",
            JobStatus.SKIPPED: "skipped",
            JobStatus.PREVIEW: "preview",
        }[self]

    @property
    def group(self) -> str:
        """수집 결과 집계·필터용 묶음."""
        return {
            JobStatus.PROCESSING: "processing",
            JobStatus.SAVED: "saved",
            JobStatus.PARTIAL: "saved",
            JobStatus.PREVIEW: "saved",
            JobStatus.EXISTS: "exists",
            JobStatus.SKIPPED: "skipped",
            JobStatus.FETCH_FAILED: "failed",
            JobStatus.SAVE_FAILED: "failed",
            JobStatus.INVALID: "failed",
        }[self]


@dataclass
class Job:
    input_url: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    normalized_url: str | None = None
    site: str | None = None
    status: JobStatus = JobStatus.PROCESSING
    message: str = ""
    title: str | None = None
    notion_url: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(KST))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "input_url": self.input_url,
            "normalized_url": self.normalized_url,
            "site": self.site,
            "status": self.status.value,
            "message": self.message,
            "title": self.title,
            "notion_url": self.notion_url,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class Posting:
    source: str
    url: str
    title: str  # 노션 공고명 형식: [기업명] 공고명
    raw_title: str
    company: str | None
    career_raw: str | None = None
    career_select: str | None = None
    employment_type: str | None = None
    location: str | None = None
    deadline: date | None = None
    deadline_raw: str | None = None
    expired: bool = False
    sections: dict[str, list[str]] = field(default_factory=dict)

    @property
    def body_found(self) -> bool:
        return any(self.sections.get(k) for k in SECTION_KEYS)
