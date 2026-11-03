from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.templating import Jinja2Templates
from notion_client.errors import HTTPResponseError
from pydantic import BaseModel, field_validator

from app.config import EnvValueExists, filled_env_keys, get_settings, write_env
from app.collection.fetcher import BrowserFetcher, HttpFetcher
from app.notion_store import NotionError, NotionStore, describe_error
from app.collection.collector import Collector
from app.service import JobService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("jobdama")

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    http_client = httpx.AsyncClient(timeout=settings.http_timeout_sec)
    browser = BrowserFetcher() if settings.use_playwright else None
    store = NotionStore(settings)
    problems = await store.check_schema()
    for p in problems:
        log.warning("노션 점검: %s", p)
    service = JobService(
        Collector(HttpFetcher(http_client), browser),
        store,
        notion_ready=not problems,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.service = service
    app.state.problems = problems
    try:
        yield
    finally:
        await service.wait_idle()
        await asyncio.gather(*_closing)
        if browser is not None:
            await browser.close()
        await http_client.aclose()
        await app.state.store.aclose()  # 설정 입력으로 교체됐을 수 있다


app = FastAPI(title="채용공고 노션 저장", lifespan=lifespan)
# DNS rebinding 방어: 다른 사이트 도메인으로 들어온 요청은 거절한다
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


class SubmitIn(BaseModel):
    url: str


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    s = request.app.state
    return templates.TemplateResponse(request, "index.html", {
        "problems": s.problems,
        "jobs": s.service.recent(s.settings.max_jobs_shown),
    })


@app.post("/api/jobs", status_code=202)
async def submit(body: SubmitIn, request: Request):
    job = request.app.state.service.submit(body.url)
    return job.to_dict()


@app.get("/api/jobs")
async def list_jobs(request: Request):
    s = request.app.state
    return [j.to_dict() for j in s.service.recent(s.settings.max_jobs_shown)]


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_partial(request: Request):
    s = request.app.state
    return templates.TemplateResponse(request, "_jobs.html", {"jobs": s.service.recent(s.settings.max_jobs_shown)})


@app.post("/api/recheck")
async def recheck(request: Request):
    s = request.app.state
    store = s.store
    problems = await store.check_schema()
    if s.store is store:  # 점검하는 사이 설정 입력으로 store가 바뀌었으면 옛 결과는 버린다
        s.problems = problems
        s.service.notion_ready = not problems
    return RedirectResponse("/", status_code=303)


class NotionSettingsIn(BaseModel):
    notion_token: str
    job_db: str  # 채용 지원 관리 DB 링크 또는 ID
    company_db: str  # 관심 기업 리스트 DB 링크 또는 ID
    overwrite: bool = False  # .env에 이미 있는 값을 덮어쓸지

    @field_validator("notion_token", "job_db", "company_db")
    @classmethod
    def single_token(cls, v: str) -> str:
        v = v.strip()
        if not v or any(c.isspace() for c in v):  # .env 줄 주입 방지
            raise ValueError("비어 있거나 공백이 들어 있음")
        return v


ENV_KEYS = ("notion_token", "job_data_source_id", "company_data_source_id")
_closing: set[asyncio.Task] = set()  # 이전 store를 닫는 백그라운드 작업(참조를 잡아 둬야 GC되지 않는다)


def _env_exists(keys: str) -> JSONResponse:
    return JSONResponse({"problems": [f".env에 {keys} 값이 이미 있어 덮어쓰지 않음: 덮어쓰려면 overwrite를 켜고 다시 시도"]},
                        status_code=409)


async def _close_when_idle(service: JobService, store: NotionStore) -> None:
    await service.wait_idle()
    try:
        await store.aclose()
    except Exception:
        log.exception("이전 노션 연결 닫기 실패")


@app.post("/api/notion/settings")
async def save_notion_settings(body: NotionSettingsIn, request: Request):
    """노션 미연결 상태에서 토큰과 DB 링크를 받아 점검 후 .env에 저장하고 바로 연결한다."""
    origin = request.headers.get("origin")
    # 사용자가 연 다른 사이트가 보낸 요청으로 설정이 바뀌지 않게 한다
    if origin and urlsplit(origin).hostname not in ("localhost", "127.0.0.1"):
        return JSONResponse({"problems": ["다른 사이트에서 온 요청"]}, status_code=403)
    s = request.app.state
    if s.service.notion_ready:
        return JSONResponse({"problems": ["이미 노션에 연결됨"]}, status_code=409)
    # 사용자가 .env에 직접 적은 값은 웹에서 덮어쓰지 않는다
    if not body.overwrite and (filled := filled_env_keys(ENV_KEYS)):
        return _env_exists(", ".join(filled))
    settings = s.settings.model_copy(update={"notion_token": body.notion_token})
    store = NotionStore(settings)
    swapped = False
    try:
        ids = {}
        for key, link, name in (
            ("job_data_source_id", body.job_db, "채용 지원 관리 DB"),
            ("company_data_source_id", body.company_db, "관심 기업 리스트 DB"),
        ):
            try:
                ids[key] = await store.resolve_data_source_id(link)
            except NotionError as e:
                return JSONResponse({"problems": [f"{name}: {e}"]}, status_code=400)
            except Exception as e:
                # 4xx는 입력 문제, 시간 초과·5xx는 노션 쪽 문제
                status = 400 if isinstance(e, HTTPResponseError) and e.status < 500 else 502
                return JSONResponse({"problems": [f"{name} 접근 실패 ({describe_error(e)})"]}, status_code=status)
        store.s = settings = settings.model_copy(update=ids)
        problems = await store.check_schema()
        if problems:
            return JSONResponse({"problems": problems}, status_code=400)
        if s.service.notion_ready:  # 점검하는 사이 다른 요청이 먼저 연결함
            return JSONResponse({"problems": ["이미 노션에 연결됨"]}, status_code=409)
        try:
            write_env({k: getattr(settings, k) for k in ENV_KEYS}, overwrite=body.overwrite)
        except EnvValueExists as e:  # 노션을 확인하는 사이 사용자가 .env에 값을 적음
            return _env_exists(str(e))
        old = s.store
        s.settings, s.store, s.problems = settings, store, []
        s.service.store = store
        s.service.notion_ready = True
        swapped = True
    finally:
        if not swapped:
            await store.aclose()
    # 연결이 끊기기 전에 시작된 작업이 옛 store를 쓰고 있을 수 있어, 응답은 바로 하고 작업이 끝난 뒤 닫는다
    task = asyncio.create_task(_close_when_idle(s.service, old))
    _closing.add(task)
    task.add_done_callback(_closing.discard)
    # 셸·systemd 환경변수는 재시작할 때 .env보다 우선한다
    keys = {k.upper() for k in ENV_KEYS}
    overridden = sorted(k.upper() for k, v in os.environ.items() if v and k.upper() in keys)
    warnings = [f"{k} 환경변수가 있어 재시작하면 .env 값 대신 쓰임" for k in overridden]
    return {"notion_ready": True, **ids, "warnings": warnings}


@app.get("/health")
async def health(request: Request):
    return JSONResponse({"ok": True, "notion_ready": request.app.state.service.notion_ready})
