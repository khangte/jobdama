from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import get_settings
from app.collection.fetcher import BrowserFetcher, HttpFetcher
from app.notion_store import NotionStore
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
        if browser is not None:
            await browser.close()
        await http_client.aclose()
        await store.aclose()


app = FastAPI(title="채용공고 노션 저장", lifespan=lifespan)
# DNS rebinding 방어: 다른 사이트 도메인으로 들어온 요청은 거절한다
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


class SubmitIn(BaseModel):
    url: str


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"problems": request.app.state.problems})


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
    s.problems = await s.store.check_schema()
    s.service.notion_ready = not s.problems
    return RedirectResponse("/", status_code=303)


@app.get("/health")
async def health(request: Request):
    return JSONResponse({"ok": True, "notion_ready": request.app.state.service.notion_ready})
