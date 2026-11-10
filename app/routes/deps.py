"""라우터가 함께 쓰는 템플릿과 요청 검사."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.datastructures import State

from app.collect_runner import CollectRunner
from app.parsers.common import company_mark
from app.service import JobService

templates = Jinja2Templates(directory=Path(__file__).parent.parent / "templates")
templates.env.globals["company_mark"] = company_mark


def foreign_origin(request: Request) -> JSONResponse | None:
    """사용자가 연 다른 사이트가 보낸 요청으로 연결이 바뀌지 않게 한다."""
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).hostname not in ("localhost", "127.0.0.1"):
        return JSONResponse({"problems": ["다른 사이트에서 온 요청"]}, status_code=403)
    return None


def get_state(request: Request) -> State:
    """앱 상태(app.state)를 돌려준다. 테스트는 dependency_overrides로 통째로 바꿔 끼운다."""
    return request.app.state


StateDep = Annotated[State, Depends(get_state)]


def get_service(s: StateDep) -> JobService:
    """공고 저장 서비스."""
    return s.service


def get_runner(s: StateDep) -> CollectRunner:
    """자동 수집 실행기."""
    return s.collect


ServiceDep = Annotated[JobService, Depends(get_service)]
RunnerDep = Annotated[CollectRunner, Depends(get_runner)]
