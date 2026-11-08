from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.collect_runner import CollectRunner
from app.collection.collector import Collector
from app.collection.fetcher import BrowserFetcher, HttpFetcher, Pacer
from app.config import get_settings
from app.notion.store import NotionStore
from app.routes import collect, jobs, notion
from app.routes.notion import closing
from app.service import JobService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jobdama")

BASE = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 시작 시 노션 점검과 서비스 조립, 종료 시 정리를 맡는다."""
    settings = get_settings()
    http_client = httpx.AsyncClient(timeout=settings.http_timeout_sec)
    browser = BrowserFetcher() if settings.use_playwright else None
    store = NotionStore(settings)
    problems = await store.check_schema()
    for p in problems + store.warnings:
        log.warning("노션 점검: %s", p)
    service = JobService(
        Collector(HttpFetcher(http_client), browser),
        store,
        notion_ready=not problems,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.service = service
    app.state.collect = CollectRunner(
        service, HttpFetcher(http_client, concurrency=1, pacer=Pacer(1.0, 1.5)), Path(settings.collect_result_path),
        Path(settings.collect_config_path))
    app.state.problems = problems
    try:
        yield
    finally:
        await app.state.collect.aclose()  # 실행 중인 수집을 먼저 취소해야 service가 한가해진다
        await service.wait_idle()
        await asyncio.gather(*closing)
        if browser is not None:
            await browser.close()
        await http_client.aclose()
        await app.state.store.aclose()  # 설정 입력으로 교체됐을 수 있다


app = FastAPI(title="채용공고 노션 저장", lifespan=lifespan)
# DNS rebinding 방어: 다른 사이트 도메인으로 들어온 요청은 거절한다
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])


class RevalidatedStaticFiles(StaticFiles):
    """브라우저가 예전 app.js를 묻지 않고 재사용하지 않게 매번 ETag로 확인시킨다(바뀌지 않았으면 304)."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", RevalidatedStaticFiles(directory=BASE / "static"), name="static")

api = APIRouter(prefix="/api/v1")  # API 경로에 버전을 붙인다. 화면(/, /jobs)과 /health는 제외
api.include_router(jobs.api)
api.include_router(notion.api)
api.include_router(collect.api)
# 라우트를 모두 등록한 뒤 붙여야 한다
app.include_router(api)
app.include_router(jobs.pages)
app.include_router(collect.pages)
