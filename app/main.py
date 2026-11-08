from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.templating import Jinja2Templates
from notion_client.errors import HTTPResponseError
from pydantic import BaseModel, field_validator

from app.config import get_settings
from app.collect import AlreadyRunning, CollectRunner, NotionNotReady
from app.collection.fetcher import BrowserFetcher, HttpFetcher, Pacer
from app.notion_store import NotionError, NotionStore, describe_error
from app.collection.collect_config import CHOICES, SITE_NAMES, CollectConfig
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
        await asyncio.gather(*_closing)
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


class SubmitIn(BaseModel):
    url: str


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    s = request.app.state
    return templates.TemplateResponse(request, "index.html", {
        "problems": s.problems,
        "warnings": getattr(s.store, "warnings", []),  # 연결 전에는 store가 점검 전 상태일 수 있다
        "jobs": s.service.recent(s.settings.max_jobs_shown),
    })


@api.post("/jobs", status_code=202)
async def submit(body: SubmitIn, request: Request):
    job = request.app.state.service.submit(body.url)
    return job.to_dict()


@api.get("/jobs")
async def list_jobs(request: Request):
    s = request.app.state
    return [j.to_dict() for j in s.service.recent(s.settings.max_jobs_shown)]


@app.get("/jobs", response_class=HTMLResponse)
async def jobs_partial(request: Request):
    s = request.app.state
    return templates.TemplateResponse(request, "_jobs.html", {"jobs": s.service.recent(s.settings.max_jobs_shown)})


@api.post("/recheck")
async def recheck(request: Request):
    s = request.app.state
    store = s.store
    problems = await store.check_schema()
    if s.store is store:  # 점검하는 사이 설정 입력으로 store가 바뀌었으면 옛 결과는 버린다
        s.problems = problems
        s.service.notion_ready = not problems
    return RedirectResponse("/", status_code=303)


class NotionConnectIn(BaseModel):
    notion_token: str
    job_db: str  # 채용 지원 관리 DB 링크 또는 ID. 관심 기업 리스트 DB는 이 DB의 기업명 relation에서 찾는다

    @field_validator("notion_token", "job_db")
    @classmethod
    def single_token(cls, v: str) -> str:
        v = v.strip()
        if not v or any(c.isspace() for c in v):  # 토큰·링크·ID에는 공백이 없다
            raise ValueError("비어 있거나 공백이 들어 있음")
        return v


ENV_KEYS = ("notion_token", "job_data_source_id")
DISCONNECTED = "노션 연결 해제됨: 웹에서 노션 설정을 입력하거나 재시작하면 .env 값으로 다시 연결"
_closing: set[asyncio.Task] = set()  # 이전 store를 닫는 백그라운드 작업(참조를 잡아 둬야 GC되지 않는다)


def _foreign_origin(request: Request) -> JSONResponse | None:
    """사용자가 연 다른 사이트가 보낸 요청으로 연결이 바뀌지 않게 한다."""
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).hostname not in ("localhost", "127.0.0.1"):
        return JSONResponse({"problems": ["다른 사이트에서 온 요청"]}, status_code=403)
    return None


async def _close_when_idle(service: JobService, store: NotionStore) -> None:
    await service.wait_idle()
    try:
        await store.aclose()
    except Exception:
        log.exception("이전 노션 연결 닫기 실패")


def _close_later(service: JobService, store: NotionStore) -> None:
    # 연결이 바뀌기 전에 시작된 작업이 옛 store를 쓰고 있을 수 있어, 응답은 바로 하고 작업이 끝난 뒤 닫는다
    task = asyncio.create_task(_close_when_idle(service, store))
    _closing.add(task)
    task.add_done_callback(_closing.discard)


@api.post("/notion/connect")
async def connect_notion(body: NotionConnectIn, request: Request):
    """노션 미연결 상태에서 토큰과 DB 링크를 받아 점검 후 바로 연결한다.
    메모리에만 적용하므로 .env는 그대로이고, 재시작하면 .env 값으로 연결한다."""
    if denied := _foreign_origin(request):
        return denied
    s = request.app.state
    if s.service.notion_ready:
        return JSONResponse({"problems": ["이미 노션에 연결됨: 먼저 연결을 해제"]}, status_code=409)
    settings = s.settings.model_copy(update={"notion_token": body.notion_token})
    store = NotionStore(settings)
    swapped = False
    try:
        name = "채용 지원 관리 DB"
        try:
            job_id = await store.resolve_data_source_id(body.job_db)
        except NotionError as e:
            return JSONResponse({"problems": [f"{name}: {e}"]}, status_code=400)
        except Exception as e:
            # 4xx는 입력 문제, 시간 초과·5xx는 노션 쪽 문제
            status = 400 if isinstance(e, HTTPResponseError) and e.status < 500 else 502
            return JSONResponse({"problems": [f"{name} 접근 실패 ({describe_error(e)})"]}, status_code=status)
        store.s = settings = settings.model_copy(update={"job_data_source_id": job_id})
        problems = await store.check_schema()
        if problems:
            return JSONResponse({"problems": problems}, status_code=400)
        if s.service.notion_ready:  # 점검하는 사이 다른 요청이 먼저 연결함
            return JSONResponse({"problems": ["이미 노션에 연결됨: 먼저 연결을 해제"]}, status_code=409)
        old = s.store
        s.settings, s.store, s.problems = settings, store, []
        s.service.store = store
        s.service.notion_ready = True
        swapped = True
    finally:
        if not swapped:
            await store.aclose()
    _close_later(s.service, old)
    return {"notion_ready": True, "job_data_source_id": job_id, "company_data_source_id": store.company_source_id}


@api.post("/notion/disconnect")
async def disconnect_notion(request: Request):
    """노션 연결을 메모리에서만 끊는다. .env는 그대로라 재시작하면 다시 연결된다. 이미 끊겨 있어도 성공."""
    if denied := _foreign_origin(request):
        return denied
    s = request.app.state
    # 토큰을 비운 store로 바꿔 둔다: 다시 점검(recheck)해도 옛 토큰으로 되살아나지 않게
    settings = s.settings.model_copy(update=dict.fromkeys(ENV_KEYS, ""))
    old, store = s.store, NotionStore(settings)
    s.settings, s.store, s.problems = settings, store, [DISCONNECTED]
    s.service.store = store
    s.service.notion_ready = False
    _close_later(s.service, old)
    return {"notion_ready": False}


class CollectIn(BaseModel):
    """수집 실행 요청 본문."""

    dry_run: bool = False  # True면 노션에서 읽기만 하고 쓰지 않는다
    trigger: Literal["schedule", "screen"] = "schedule"


@api.post("/collect", status_code=202)
async def start_collect(request: Request, body: CollectIn | None = None):
    """자동 수집을 백그라운드로 시작한다. 본문 없는 호출은 스케줄 실행으로 본다."""
    if denied := _foreign_origin(request):
        return denied
    body = body or CollectIn()
    try:
        request.app.state.collect.start(body.trigger, body.dry_run)
    except NotionNotReady:
        return JSONResponse({"problems": ["노션 미연결: 먼저 노션을 연결"]}, status_code=503)
    except AlreadyRunning:
        return JSONResponse({"problems": ["이미 수집 실행 중"]}, status_code=409)
    return {"started": True, "dry_run": body.dry_run, "trigger": body.trigger}


@api.get("/collect/last")
async def last_collect(request: Request):
    """실행 중이면 지금까지의 상태, 아니면 마지막 실행 결과(없으면 null)."""
    runner = request.app.state.collect
    return {"running": runner.running, "run": runner.snapshot()}


FILTERS = (("all", "전체"), ("saved", "저장"), ("exists", "이미 있음"), ("skipped", "제외"), ("failed", "실패"))


@api.get("/collect/settings")
async def get_collect_settings(request: Request):
    """저장된 수집 조건과 기본값, 설정 파일 문제 문구(없으면 null)."""
    runner = request.app.state.collect
    return {"settings": runner.config.model_dump(), "defaults": CollectConfig().model_dump(), "problem": runner.config_problem}


@api.put("/collect/settings")
async def put_collect_settings(request: Request, body: CollectConfig):
    """수집 조건을 검증해 저장한다. 실행 중인 수집은 옛 조건으로 끝나고 다음 실행부터 적용된다."""
    if denied := _foreign_origin(request):
        return denied
    try:
        request.app.state.collect.update_config(body)
    except OSError:
        log.exception("수집 조건 저장 실패")
        return JSONResponse({"problems": ["설정을 저장하지 못했어요."]}, status_code=500)
    return {"settings": body.model_dump()}


def _collect_context(request: Request, status: str) -> dict:
    """수집 화면 템플릿 컨텍스트. 필터 값이 잘못되면 전체로 본다."""
    s = request.app.state
    run = s.collect.snapshot()
    status = status if status in dict(FILTERS) else "all"
    all_items = run["items"] if run else []
    counts = {key: sum(1 for i in all_items if key == "all" or i["group"] == key) for key, _ in FILTERS}
    return {"run": run, "running": s.collect.running, "notion_ready": s.service.notion_ready, "status": status,
            "items": [i for i in all_items if status == "all" or i["group"] == status],
            "counts": counts, "filters": FILTERS, "site_names": {x["key"]: x["name"] for x in run["sites"]} if run else {}}


@app.get("/collect", response_class=HTMLResponse)
async def collect_page(request: Request, status: str = "all"):
    """자동 수집 화면 전체."""
    s = request.app.state
    form = {"config": s.collect.config.model_dump(), "defaults": CollectConfig().model_dump(), "problem": s.collect.config_problem,
            "choices": {**CHOICES, "sites": list(SITE_NAMES.items())}}
    return templates.TemplateResponse(request, "collect.html", {**_collect_context(request, status), **form})


@app.get("/collect/results", response_class=HTMLResponse)
async def collect_partial(request: Request, status: str = "all"):
    """자동 수집 화면의 갱신 조각(실행 중 폴링·필터 전환용)."""
    return templates.TemplateResponse(request, "_collect.html", _collect_context(request, status))


@app.get("/health")
async def health(request: Request):
    return JSONResponse({"ok": True, "notion_ready": request.app.state.service.notion_ready})


# 라우트를 모두 등록한 뒤 붙여야 한다
app.include_router(api)
