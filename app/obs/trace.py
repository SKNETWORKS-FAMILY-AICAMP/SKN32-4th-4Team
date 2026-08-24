"""trace_id 상관추적 (NFR-OBS-01, TEST-OBS-001).

요청마다 trace_id를 발급(또는 클라이언트의 X-Trace-ID 승계)해 contextvar에 담고, 응답에
X-Trace-ID 헤더로 되돌린다. 하위 계층(라우터·이벤트 기록)은 get_trace_id()로 상관 키를 얻는다.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_TRACE_HEADER = "X-Trace-ID"
_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def get_trace_id() -> str | None:
    """현재 요청의 trace_id(없으면 None — 요청 밖 컨텍스트)."""
    return _trace_id.get()


def set_trace_id(value: str) -> None:
    _trace_id.set(value)


class TraceMiddleware(BaseHTTPMiddleware):
    """요청별 trace_id 설정 + 응답 헤더 부착.

    한계(문서화): 정상 응답과 **타입 있는 오류(AppError→핸들러가 응답 반환)** 는 헤더를 받는다.
    미처리 예외로 인한 500(버그 경로)은 상위 ServerErrorMiddleware가 응답을 만들어 헤더가 빠질 수
    있다 — 정상 운영 흐름의 오류는 전부 AppError라 실질 영향은 낮다.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get(_TRACE_HEADER)
        trace_id = incoming.strip() if incoming and incoming.strip() else new_trace_id()
        token = _trace_id.set(trace_id)  # 토큰 보관 → finally에서 복원(요청 간 값 잔존 방지)
        try:
            response = await call_next(request)
            response.headers[_TRACE_HEADER] = trace_id
            return response
        finally:
            _trace_id.reset(token)
