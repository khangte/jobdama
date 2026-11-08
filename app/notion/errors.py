"""노션 오류 타입과 오류 분류 도우미."""

from __future__ import annotations

import httpx
from notion_client.errors import HTTPResponseError, RequestTimeoutError


class NotionError(Exception):
    """노션 연동에서 사용자에게 알릴 만한 오류."""


def is_uncertain_write_error(e: Exception) -> bool:
    """요청이 서버에 반영됐는지 알 수 없는 오류(시간 초과, 연결 끊김, 5xx)."""
    if isinstance(e, (RequestTimeoutError, httpx.TransportError)):
        return True
    if isinstance(e, HTTPResponseError):
        return (getattr(e, "status", 0) or 0) >= 500
    return False


def is_schema_error(e: Exception) -> bool:
    """열 이름·형식이 맞지 않아 노션이 요청을 거부한 오류. 거부된 요청은 반영되지 않는다."""
    return isinstance(e, HTTPResponseError) and e.status == 400 and getattr(e.code, "value", e.code) == "validation_error"


def describe_error(e: Exception) -> str:
    """원인을 짐작할 수 있게 HTTP 상태와 코드를 적는다. 401과 404가 같은 예외 클래스라 이름만으로는 구분이 안 된다."""
    if isinstance(e, HTTPResponseError):
        hint = {401: "토큰 확인", 403: "권한 확인", 404: "integration 연결과 링크·ID 확인"}.get(e.status)
        code = getattr(e.code, "value", e.code)  # 알려진 코드는 APIErrorCode enum으로 온다
        return f"HTTP {e.status} {code}" + (f": {hint}" if hint else "")
    return type(e).__name__
