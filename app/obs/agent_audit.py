"""등록 에이전트 API의 PII-free 감사 middleware."""

from __future__ import annotations

import time

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.domain.agent_access import AgentAuditRecord


class AgentAuditMiddleware(BaseHTTPMiddleware):
    """인증된 요청의 결과를 원문 없이 append하고, 감사 장애는 503으로 닫는다."""

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        response = None
        raised: Exception | None = None
        try:
            response = await call_next(request)
        except Exception as exc:  # 감사 후 원래 예외를 그대로 다시 올린다.
            raised = exc

        context = getattr(request.state, "agent_context", None)
        registry = getattr(request.state, "agent_registry", None)
        if context is not None and registry is not None:
            audit = getattr(request.state, "agent_audit", {})
            status_code = response.status_code if response is not None else 500
            latency_ms = max(0, round((time.perf_counter() - started) * 1000))
            try:
                registry.append_audit(
                    AgentAuditRecord(
                        client_id=context.principal.client_id,
                        operation=context.operation,
                        required_scope=context.required_scope,
                        subject_hash=context.subject_hash,
                        request_hash=audit.get("request_hash", ""),
                        response_hash=audit.get("response_hash"),
                        trace_hash=context.trace_hash,
                        source_event_hash=audit.get("source_event_hash"),
                        http_status=status_code,
                        latency_ms=latency_ms,
                        verdict=_as_optional_text(audit.get("verdict")),
                        abstained=audit.get("abstained"),
                        reason_code=_as_optional_text(audit.get("reason_code")),
                        rule_engine_version=_as_optional_text(
                            audit.get("rule_engine_version")
                        ),
                        model_profile=_as_optional_text(audit.get("model_profile")),
                        policy_version_ref=_as_optional_text(
                            audit.get("policy_version_ref")
                        ),
                        citation_refs=tuple(audit.get("citation_refs") or ()),
                    )
                )
            except Exception:  # noqa: BLE001 - 원문 예외를 응답에 노출하지 않는다.
                return JSONResponse(
                    status_code=503,
                    content={
                        "ok": False,
                        "error_code": "agent_audit_unavailable",
                        "message": "외부 에이전트 요청 감사를 기록하지 못했습니다.",
                    },
                )

        if raised is not None:
            raise raised
        return response


def _as_optional_text(value) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    return text[:160] or None


__all__ = ["AgentAuditMiddleware"]
