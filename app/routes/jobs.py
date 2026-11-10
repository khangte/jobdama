"""공고 등록·목록 화면과 API, 상태 확인."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.routes.deps import ServiceDep, StateDep, templates

pages = APIRouter()  # 화면(/, /jobs)과 /health. API 접두사는 main에서 붙인다
api = APIRouter()


class SubmitIn(BaseModel):
    """공고 URL 등록 요청 본문."""

    url: str


@pages.get("/", response_class=HTMLResponse)
async def index(request: Request, s: StateDep):
    """메인 화면."""
    return templates.TemplateResponse(request, "index.html", {
        "problems": s.problems,
        "warnings": getattr(s.store, "warnings", []),  # 연결 전에는 store가 점검 전 상태일 수 있다
        "jobs": s.service.recent(s.settings.max_jobs_shown),
    })


@api.post("/jobs", status_code=202)
async def submit(body: SubmitIn, service: ServiceDep):
    """공고 URL을 받아 백그라운드 저장을 시작한다."""
    job = service.submit(body.url)
    return job.to_dict()


@api.get("/jobs")
async def list_jobs(s: StateDep):
    """최근 공고 처리 목록."""
    return [j.to_dict() for j in s.service.recent(s.settings.max_jobs_shown)]


@pages.get("/jobs", response_class=HTMLResponse)
async def jobs_partial(request: Request, s: StateDep):
    """공고 목록 화면 조각."""
    return templates.TemplateResponse(request, "_jobs.html", {"jobs": s.service.recent(s.settings.max_jobs_shown)})


@pages.get("/health")
async def health(service: ServiceDep):
    """서버 상태와 노션 연결 여부."""
    return JSONResponse({"ok": True, "notion_ready": service.notion_ready})


@pages.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
async def chrome_devtools_probe():
    """크롬 개발자 도구가 자동으로 요청하는 주소. 404 로그를 막으려고 빈 JSON을 돌려준다."""
    return {}
