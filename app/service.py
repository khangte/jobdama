"""작업 접수와 처리 흐름.

URL 검증·정규화 → (같은 URL 잠금) → 같은 URL 조회 → 수집·필드 추출(app.collection.collector) → 기업 조회·생성 → 공고 페이지 생성

작업 상태는 단일 서버 프로세스의 메모리에만 보관한다(서버를 재시작하면 목록이 비워진다).
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import replace
from typing import Protocol

from app.collection.collector import Collector
from app.collection.fetcher import FetchError
from app.collection.urls import NormalizedURL, UnsupportedURL, normalize
from app.models import Job, JobStatus, Posting
from app.notion_store import is_uncertain_write_error, page_url
from app.parsers.common import today_kst

log = logging.getLogger(__name__)

MAX_JOBS_KEPT = 200


class Store(Protocol):
    async def find_by_url(self, target: NormalizedURL) -> dict | None: ...
    async def find_or_create_company(self, name: str) -> str | None: ...
    async def create_posting(self, p: Posting, company_page_id: str | None) -> dict: ...


class JobService:
    def __init__(self, collector: Collector, store: Store, notion_ready: bool = True):
        self.collector = collector
        self.store = store
        self.notion_ready = notion_ready
        self.jobs: OrderedDict[str, Job] = OrderedDict()
        self._url_locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------ 접수
    def submit(self, raw_url: str) -> Job:
        job = Job(input_url=raw_url.strip())
        self.jobs[job.id] = job
        while len(self.jobs) > MAX_JOBS_KEPT:
            self.jobs.popitem(last=False)
        try:
            target = normalize(raw_url)
        except UnsupportedURL as e:
            self._finish(job, JobStatus.INVALID, str(e))
            return job
        job.normalized_url = target.url
        job.site = target.site.name
        task = asyncio.create_task(self._run(job, target))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def recent(self, limit: int) -> list[Job]:
        return list(reversed(self.jobs.values()))[:limit]

    async def wait_idle(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ------------------------------------------------------------ 처리
    async def _run(self, job: Job, target: NormalizedURL) -> None:
        try:
            if not self.notion_ready:
                self._finish(job, JobStatus.SAVE_FAILED, "노션 설정 문제로 저장할 수 없음 (화면 상단 안내 확인)")
                return
            lock = self._url_locks.setdefault(target.url, asyncio.Lock())
            async with lock:  # 같은 URL은 조회부터 생성까지 한 번에 하나씩
                await self._process(job, target)
        except Exception as e:  # 예상 못 한 오류도 화면에 남긴다
            log.exception("작업 처리 오류")
            self._finish(job, JobStatus.SAVE_FAILED, f"처리 중 오류: {type(e).__name__}")

    async def _process(self, job: Job, target: NormalizedURL) -> None:
        store = self.store  # 연결이 바뀌어도 한 작업은 처음 잡은 store만 쓴다
        try:
            existing = await store.find_by_url(target)
        except Exception as e:
            self._finish(job, JobStatus.SAVE_FAILED, f"노션 조회 실패: {type(e).__name__}")
            return
        if existing:
            job.notion_url = page_url(existing)
            self._finish(job, JobStatus.EXISTS, "같은 공고 URL의 항목이 이미 있음")
            return

        try:
            posting = await self.collector.collect(target)
        except FetchError as e:
            self._finish(job, JobStatus.FETCH_FAILED, str(e))
            return
        today = today_kst()
        if posting.deadline and posting.deadline < today and not posting.expired:
            posting = replace(posting, expired=True)
        job.title = posting.title

        try:
            company_id = await store.find_or_create_company(posting.company) if posting.company else None
        except Exception as e:
            self._finish(job, JobStatus.SAVE_FAILED, f"기업 조회·생성 실패: {type(e).__name__}")
            return

        try:
            page = await self._create_with_recheck(store, posting, company_id, target)
        except Exception as e:
            self._finish(job, JobStatus.SAVE_FAILED, f"공고 페이지 생성 실패: {type(e).__name__}")
            return

        job.notion_url = page_url(page)
        notes = []
        if not posting.company:
            notes.append("기업명 미확인으로 기업 연결 생략")
        if posting.expired:
            notes.append("마감된 공고")
        if not target.site.verified:
            notes.append(f"{target.site.name}는 미검증 사이트라 노션에서 값 확인 필요")
        if posting.body_found:
            self._finish(job, JobStatus.SAVED, ", ".join(notes))
        else:
            notes.insert(0, "본문 구간을 찾지 못해 확인된 속성만 저장")
            self._finish(job, JobStatus.PARTIAL, ", ".join(notes))

    async def _create_with_recheck(self, store: Store, posting: Posting, company_id: str | None, target: NormalizedURL) -> dict:
        """생성 응답이 끊기면(시간 초과, 연결 끊김, 5xx) 다시 조회한 뒤 없을 때만 1회 재시도."""
        try:
            return await store.create_posting(posting, company_id)
        except Exception as e:
            if not is_uncertain_write_error(e):
                raise
            log.warning("생성 응답 불확실, 재조회: %s", e)
        existing = await store.find_by_url(target)
        if existing:
            return existing
        return await store.create_posting(posting, company_id)

    @staticmethod
    def _finish(job: Job, status: JobStatus, message: str = "") -> None:
        job.status = status
        job.message = message
