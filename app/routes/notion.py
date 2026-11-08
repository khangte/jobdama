"""노션 연결 점검·설정·해제 API."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from notion_client.errors import HTTPResponseError
from pydantic import BaseModel, field_validator

from app.notion.errors import NotionError, describe_error
from app.notion.store import NotionStore
from app.routes.deps import StateDep, foreign_origin
from app.service import JobService

log = logging.getLogger("jobdama")
api = APIRouter()


@api.post("/recheck")
async def recheck(s: StateDep):
    """노션 스키마를 다시 점검한다."""
    store = s.store
    problems = await store.check_schema()
    if s.store is store:  # 점검하는 사이 설정 입력으로 store가 바뀌었으면 옛 결과는 버린다
        s.problems = problems
        s.service.notion_ready = not problems
    return RedirectResponse("/", status_code=303)


class NotionConnectIn(BaseModel):
    """노션 연결 요청 본문."""

    notion_token: str
    job_db: str  # 채용 지원 관리 DB 링크 또는 ID. 관심 기업 리스트 DB는 이 DB의 기업명 relation에서 찾는다

    @field_validator("notion_token", "job_db")
    @classmethod
    def single_token(cls, v: str) -> str:
        """앞뒤 공백을 지우고, 비었거나 공백이 들어 있으면 거절한다."""
        v = v.strip()
        if not v or any(c.isspace() for c in v):  # 토큰·링크·ID에는 공백이 없다
            raise ValueError("비어 있거나 공백이 들어 있음")
        return v


ENV_KEYS = ("notion_token", "job_data_source_id")
DISCONNECTED = "노션 연결 해제됨: 웹에서 노션 설정을 입력하거나 재시작하면 .env 값으로 다시 연결"
closing: set[asyncio.Task] = set()  # 이전 store를 닫는 백그라운드 작업(참조를 잡아 둬야 GC되지 않는다)


async def _close_when_idle(service: JobService, store: NotionStore) -> None:
    """진행 중인 작업이 끝나기를 기다린 뒤 이전 store를 닫는다."""
    await service.wait_idle()
    try:
        await store.aclose()
    except Exception:
        log.exception("이전 노션 연결 닫기 실패")


def _close_later(service: JobService, store: NotionStore) -> None:
    """이전 store 닫기를 백그라운드 작업으로 예약한다."""
    # 연결이 바뀌기 전에 시작된 작업이 옛 store를 쓰고 있을 수 있어, 응답은 바로 하고 작업이 끝난 뒤 닫는다
    task = asyncio.create_task(_close_when_idle(service, store))
    closing.add(task)
    task.add_done_callback(closing.discard)


@api.post("/notion/connect")
async def connect_notion(body: NotionConnectIn, request: Request, s: StateDep):
    """노션 미연결 상태에서 토큰과 DB 링크를 받아 점검 후 바로 연결한다.
    메모리에만 적용하므로 .env는 그대로이고, 재시작하면 .env 값으로 연결한다."""
    if denied := foreign_origin(request):
        return denied
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
async def disconnect_notion(request: Request, s: StateDep):
    """노션 연결을 메모리에서만 끊는다. .env는 그대로라 재시작하면 다시 연결된다. 이미 끊겨 있어도 성공."""
    if denied := foreign_origin(request):
        return denied
    # 토큰을 비운 store로 바꿔 둔다: 다시 점검(recheck)해도 옛 토큰으로 되살아나지 않게
    settings = s.settings.model_copy(update=dict.fromkeys(ENV_KEYS, ""))
    old, store = s.store, NotionStore(settings)
    s.settings, s.store, s.problems = settings, store, [DISCONNECTED]
    s.service.store = store
    s.service.notion_ready = False
    _close_later(s.service, old)
    return {"notion_ready": False}
