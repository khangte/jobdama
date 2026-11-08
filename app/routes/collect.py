"""자동 수집 화면과 API."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.datastructures import State

from app.collect_runner import AlreadyRunning, NotionNotReady
from app.collection.collect_config import CHOICES, SITE_NAMES, CollectConfig
from app.routes.deps import RunnerDep, StateDep, foreign_origin, templates

log = logging.getLogger("jobdama")
pages = APIRouter()
api = APIRouter()


class CollectIn(BaseModel):
    """수집 실행 요청 본문."""

    dry_run: bool = False  # True면 노션에서 읽기만 하고 쓰지 않는다
    trigger: Literal["schedule", "screen"] = "schedule"


@api.post("/collect", status_code=202)
async def start_collect(request: Request, runner: RunnerDep, body: CollectIn | None = None):
    """자동 수집을 백그라운드로 시작한다. 본문 없는 호출은 스케줄 실행으로 본다."""
    if denied := foreign_origin(request):
        return denied
    body = body or CollectIn()
    try:
        runner.start(body.trigger, body.dry_run)
    except NotionNotReady:
        return JSONResponse({"problems": ["노션 미연결: 먼저 노션을 연결"]}, status_code=503)
    except AlreadyRunning:
        return JSONResponse({"problems": ["이미 수집 실행 중"]}, status_code=409)
    return {"started": True, "dry_run": body.dry_run, "trigger": body.trigger}


@api.get("/collect/last")
async def last_collect(runner: RunnerDep):
    """실행 중이면 지금까지의 상태, 아니면 마지막 실행 결과(없으면 null)."""
    return {"running": runner.running, "run": runner.snapshot()}


FILTERS = (("all", "전체"), ("saved", "저장"), ("exists", "이미 있음"), ("skipped", "제외"), ("failed", "실패"))


@api.get("/collect/settings")
async def get_collect_settings(runner: RunnerDep):
    """저장된 수집 조건과 기본값, 설정 파일 문제 문구(없으면 null)."""
    return {"settings": runner.config.model_dump(), "defaults": CollectConfig().model_dump(), "problem": runner.config_problem}


@api.put("/collect/settings")
async def put_collect_settings(request: Request, body: CollectConfig, runner: RunnerDep):
    """수집 조건을 검증해 저장한다. 실행 중인 수집은 옛 조건으로 끝나고 다음 실행부터 적용된다."""
    if denied := foreign_origin(request):
        return denied
    try:
        runner.update_config(body)
    except OSError:
        log.exception("수집 조건 저장 실패")
        return JSONResponse({"problems": ["설정을 저장하지 못했어요."]}, status_code=500)
    return {"settings": body.model_dump()}


def _collect_context(s: State, status: str) -> dict:
    """수집 화면 템플릿 컨텍스트. 필터 값이 잘못되면 전체로 본다."""
    run = s.collect.snapshot()
    status = status if status in dict(FILTERS) else "all"
    all_items = run["items"] if run else []
    counts = {key: sum(1 for i in all_items if key == "all" or i["group"] == key) for key, _ in FILTERS}
    return {"run": run, "running": s.collect.running, "notion_ready": s.service.notion_ready, "status": status,
            "items": [i for i in all_items if status == "all" or i["group"] == status],
            "counts": counts, "filters": FILTERS, "site_names": {x["key"]: x["name"] for x in run["sites"]} if run else {}}


@pages.get("/collect", response_class=HTMLResponse)
async def collect_page(request: Request, s: StateDep, status: str = "all"):
    """자동 수집 화면 전체."""
    form = {"config": s.collect.config.model_dump(), "defaults": CollectConfig().model_dump(), "problem": s.collect.config_problem,
            "choices": {**CHOICES, "sites": list(SITE_NAMES.items())}}
    return templates.TemplateResponse(request, "collect.html", {**_collect_context(s, status), **form})


@pages.get("/collect/results", response_class=HTMLResponse)
async def collect_partial(request: Request, s: StateDep, status: str = "all"):
    """자동 수집 화면의 갱신 조각(실행 중 폴링·필터 전환용)."""
    return templates.TemplateResponse(request, "_collect.html", _collect_context(s, status))
