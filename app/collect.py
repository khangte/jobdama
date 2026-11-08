"""자동 수집 실행기.

사이트 목록 → 후보 링크 → 한 건씩 JobService.run_one(선별·중복 판정·저장) 순서로 돌리고,
끝나면 결과를 JSON 파일에 원자적으로 써서 재시작 뒤에도 마지막 결과를 보여 준다.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from app.collection import filters
from app.collection.collect_config import CAREER_YEARS, CollectConfig, load_config, save_config
from app.collection.collector import Collector, Fetcher
from app.collection.fetcher import FetchError
from app.collection.listing import ListingSource, build_sources, extract_links
from app.collection.urls import UnsupportedURL, normalize
from app.models import KST, JobStatus
from app.parsers.common import today_kst
from app.service import JobService, Screening

log = logging.getLogger(__name__)

BLOCK_MARKERS = ("봇 확인", "HTTP 403", "HTTP 429")  # 상세 실패 메시지에 있으면 그 사이트를 바로 중단
MAX_CONSECUTIVE_FETCH_FAILURES = 3


class AlreadyRunning(Exception):
    """이미 수집이 실행 중."""


class NotionNotReady(Exception):
    """노션이 연결되지 않아 수집할 수 없음."""


def load_last(path: Path) -> dict | None:
    """마지막 실행 결과 파일을 읽는다. 없거나 깨졌으면 None."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("수집 결과 파일을 읽지 못함 %s: %s", path, e)
        return None
    if not (isinstance(data, dict) and isinstance(data.get("items"), list) and isinstance(data.get("sites"), list)
            and data.get("started_at")):
        log.warning("수집 결과 파일 형식이 맞지 않음 %s", path)
        return None
    return data


def _now() -> str:
    """KST 현재 시각 ISO 문자열."""
    return datetime.now(KST).isoformat(timespec="seconds")


class CollectRunner:
    """한 번에 하나의 수집 실행을 관리한다."""

    def __init__(self, service: JobService, fetcher: Fetcher, result_path: Path, config_path: Path, *,
                 sources: tuple[ListingSource, ...] | None = None, screen=None):
        """sources·screen은 테스트용 덮어쓰기다. 주면 저장된 설정으로 만든 값보다 우선한다."""
        self._service = service
        self._fetcher = fetcher
        self._path = result_path
        self._config_path = config_path
        self._sources = sources
        self._screen = screen
        self.config, self.config_problem = load_config(config_path)
        self._collector = Collector(fetcher, None)  # 브라우저 재시도 없음: 봇 확인 화면을 우회하지 않는다
        self._last = load_last(result_path)
        self._current: dict | None = None
        self._task: asyncio.Task | None = None

    def update_config(self, cfg: CollectConfig) -> None:
        """설정을 파일에 먼저 저장하고 성공하면 메모리를 바꾼다. 저장이 실패하면 OSError를 올리고 이전 설정을 유지한다."""
        save_config(self._config_path, cfg)
        self.config, self.config_problem = cfg, None

    @property
    def running(self) -> bool:
        """수집이 실행 중인지."""
        return self._task is not None and not self._task.done()

    def start(self, trigger: str, dry_run: bool) -> None:
        """백그라운드 실행을 시작한다. 실행 중이면 AlreadyRunning, 노션 미연결이면 NotionNotReady."""
        if self.running:
            raise AlreadyRunning
        if not self._service.notion_ready:
            raise NotionNotReady
        self._current = {"started_at": _now(), "finished_at": None, "trigger": trigger, "dry_run": dry_run,
                         "error": None, "sites": [], "items": []}
        # 지금의 설정을 실행에 고정한다: 실행 중 update_config는 다음 실행부터 적용된다
        self._task = asyncio.create_task(self._run(self._current, self.config))

    async def wait(self) -> None:
        """현재 실행이 끝날 때까지 기다린다."""
        if self._task:
            await asyncio.wait([self._task])

    def snapshot(self) -> dict | None:
        """실행 중이면 지금까지의 상태, 아니면 마지막 결과(없으면 None)."""
        return copy.deepcopy(self._current if self.running else self._last)

    async def aclose(self) -> None:
        """실행 중이면 취소하고 기다린다. 취소된 실행은 파일을 쓰지 않아 이전 결과가 남는다."""
        if self.running:
            self._task.cancel()
            await asyncio.wait([self._task])

    # ------------------------------------------------------------ 실행
    async def _run(self, run: dict, cfg: CollectConfig) -> None:
        """전체 사이트를 돌고 결과를 기록한다. 예상 못 한 오류는 실행 오류로 남기고 끝낸다."""
        try:
            screen = self._screen or (lambda p: filters.screen(p, today_kst(), cfg))
            screening = Screening(screen, self._collector, run["dry_run"])
            seen: set[str] = set()
            sources = self._sources if self._sources is not None else build_sources(cfg)
            for source in sources:
                await self._run_site(run, source, screening, seen, cfg)
        except asyncio.CancelledError:
            self._current = None
            raise
        except Exception as e:
            log.exception("수집 실행 오류")
            run["error"] = type(e).__name__
        run["finished_at"] = _now()
        self._last, self._current = run, None
        try:
            self._write(run)
        except OSError:
            log.exception("수집 결과 파일 쓰기 실패")

    async def _candidates(self, source: ListingSource, site: dict, seen: set[str], cfg: CollectConfig) -> list[str]:
        """목록 페이지를 받아 아직 안 본 후보 URL을 앞에서 상한까지 돌려준다. 실패하면 site['error']를 채우고, 일부 페이지만 실패했으면 받은 링크는 유지한다."""
        links: list[str] = []
        try:
            for page in source.pages:
                html, _ = await self._fetcher.fetch(page, source.site)
                links += extract_links(source.site.key, html, CAREER_YEARS.get(cfg.career))
        except (FetchError, ValueError) as e:
            site["error"] = str(e)  # 일부 페이지만 실패했으면 받은 링크는 그대로 처리한다
            if not links:
                return []
        if not links:
            site["error"] = "공고 링크 0건"
            return []
        urls = []
        for link in links:
            try:
                url = normalize(link).url
            except UnsupportedURL:
                continue
            if url not in seen:
                seen.add(url)
                urls.append(url)
        return urls[:cfg.max_per_site]

    async def _run_site(self, run: dict, source: ListingSource, screening: Screening, seen: set[str],
                        cfg: CollectConfig) -> None:
        """한 사이트의 후보를 한 건씩 처리한다. 차단·연속 실패면 남은 후보를 처리하지 않는다."""
        key = source.site.key
        site = {"key": key, "name": source.site.name, "candidates": 0, "saved": 0, "exists": 0,
                "skipped": 0, "failed": 0, "error": None}
        run["sites"].append(site)
        urls = await self._candidates(source, site, seen, cfg)
        site["candidates"] = len(urls)
        failures = 0
        for url in urls:
            job = await self._service.run_one(url, screening)
            site[job.status.group] += 1
            run["items"].append({
                "site": key, "title": job.title or job.normalized_url or job.input_url, "status": job.status.value,
                "css": job.status.css, "group": job.status.group, "message": job.message,
                "notion_url": job.notion_url, "url": job.normalized_url or job.input_url})
            failures = failures + 1 if job.status is JobStatus.FETCH_FAILED else 0
            if job.status is JobStatus.FETCH_FAILED and (
                    any(m in job.message for m in BLOCK_MARKERS) or failures >= MAX_CONSECUTIVE_FETCH_FAILURES):
                site["error"] = f"차단·연속 실패로 중단: {job.message}"
                return

    def _write(self, run: dict) -> None:
        """같은 폴더의 임시 파일에 쓴 뒤 교체해, 쓰다 끊겨도 이전 결과가 깨지지 않게 한다."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._path)
