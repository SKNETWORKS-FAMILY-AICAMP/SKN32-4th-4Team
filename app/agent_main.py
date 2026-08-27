"""등록 외부 에이전트 전용 FastAPI 앱.

고객 UI·합성 API·관리자 API·정적 파일을 싣지 않는다. 실제 원격 노출은
``scripts.run_agent_server``의 명시적 bind 검사를 통과해야 한다.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.obs.agent_audit import AgentAuditMiddleware
from app.obs.trace import TraceMiddleware
from app.routers import agent


def create_agent_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title=f"{settings.BRAND_NAME} 등록 에이전트 API",
        version="1.0.0",
    )
    # add_middleware는 역순 조립된다. Trace가 바깥에서 audit 응답에도 X-Trace-ID를 붙인다.
    application.add_middleware(AgentAuditMiddleware)
    application.add_middleware(TraceMiddleware)
    register_exception_handlers(application)
    application.include_router(agent.router)

    @application.get("/api/health", include_in_schema=False)
    def health() -> dict:
        return {
            "status": "ok",
            "service": "registered-agent-api",
            "enabled": get_settings().AGENT_API_ENABLED,
        }

    @application.get("/api/health/ready", include_in_schema=False)
    def ready() -> dict:
        from db.postgres.pg_agent_access import PgAgentAccess

        current = get_settings()
        try:
            result = PgAgentAccess(current.AGENT_PG_DSN).readiness()
            database_ready = result.get("ready") is True
        except Exception:  # noqa: BLE001 - public health must not expose setup errors
            database_ready = False
        return {
            "enabled": bool(current.AGENT_API_ENABLED),
            "ready": bool(database_ready and current.AGENT_API_ENABLED),
        }

    @application.get("/llms.txt", include_in_schema=False)
    def llms_txt() -> PlainTextResponse:
        return PlainTextResponse(
            """# 등록 외부 에이전트 API

Authorization: Bearer <agent-key>와 X-Agent-Subject(opaque 참조)가 모든 /v1/agent 요청에 필요합니다.
POST /v1/agent/observations에는 Idempotency-Key도 필수입니다.

- GET  /v1/agent/support-manifest  scope=precheck:read
- POST /v1/agent/prechecks         scope=precheck:read
- POST /v1/agent/terms/explain     scope=terms:read
- GET  /v1/agent/cohorts           scope=cohort:read
- POST /v1/agent/observations      scope=observations:write

질병기호·사용자 식별 원문을 로그나 감사 DB에 기록하지 않습니다. 사례는 항상 unverified로 접수됩니다.
""",
            media_type="text/plain; charset=utf-8",
        )

    return application


agent_app = create_agent_app()


__all__ = ["agent_app", "create_agent_app"]
