"""등록 외부 에이전트의 Bearer 인증·scope·요청 제한 의존성."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Callable

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.config_validation import require_agent_hash_secret
from app.core.domain.agent_access import (
    AgentPrincipal,
    hmac_hex,
    sensitive_payload_hash,
    validate_opaque_ref,
)
from app.core.errors import AuthErr, ConfigError, ForbiddenErr, RateLimitErr, ValidationErr
from app.obs.trace import get_trace_id


_bearer = HTTPBearer(auto_error=False, scheme_name="AgentBearer")


@dataclass(frozen=True)
class AgentRequestContext:
    principal: AgentPrincipal
    operation: str
    required_scope: str
    subject_hash: str
    trace_hash: str
    hash_secret: str
    registry: Any

    def hash_payload(self, payload: Any) -> str:
        return sensitive_payload_hash(self.hash_secret, payload)

    def hash_identifier(self, value: str) -> str:
        return hmac_hex(self.hash_secret, value)


def get_agent_registry():
    """DI 교체점. 실제 요청에서는 별도 agent PostgreSQL만 반환한다."""

    from db.postgres.pg_agent_access import PgAgentAccess

    return PgAgentAccess(get_settings().AGENT_PG_DSN)


def _trace_hash(secret: str) -> str:
    return hmac_hex(secret, get_trace_id() or "no-trace")


def get_agent_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    registry: Annotated[Any, Depends(get_agent_registry)],
) -> AgentPrincipal:
    """Authorization Bearer를 검증한다. JWT나 일반 사용자 토큰은 호환되지 않는다."""

    settings = get_settings()
    if not settings.AGENT_API_ENABLED:
        raise ConfigError("등록 외부 에이전트 API가 비활성화돼 있습니다.")
    secret = require_agent_hash_secret(settings)
    trace_hash = _trace_hash(secret)
    request.state.agent_registry = registry
    if credentials is None or credentials.scheme.lower() != "bearer":
        registry.record_auth_attempt(result="missing", trace_hash=trace_hash)
        raise AuthErr("Authorization: Bearer <agent-key>가 필요합니다.")
    principal = registry.authenticate(credentials.credentials, trace_hash=trace_hash)
    request.state.agent_principal = principal
    return principal


def require_agent_scope(required_scope: str, operation: str) -> Callable[..., AgentRequestContext]:
    """인증 principal에 endpoint scope와 client+subject+operation 한도를 강제한다."""

    def _dependency(
        request: Request,
        principal: Annotated[AgentPrincipal, Depends(get_agent_principal)],
        registry: Annotated[Any, Depends(get_agent_registry)],
        subject: Annotated[str | None, Header(alias="X-Agent-Subject")] = None,
    ) -> AgentRequestContext:
        secret = require_agent_hash_secret(get_settings())
        raw_subject = (subject or "").strip()
        subject_hash = hmac_hex(secret, raw_subject or "missing-subject")
        context = AgentRequestContext(
            principal=principal,
            operation=operation,
            required_scope=required_scope,
            subject_hash=subject_hash,
            trace_hash=_trace_hash(secret),
            hash_secret=secret,
            registry=registry,
        )
        # scope/입력/limit 오류도 middleware가 감사할 수 있게 먼저 남긴다.
        request.state.agent_registry = registry
        request.state.agent_context = context
        request.state.agent_audit = {}

        try:
            validate_opaque_ref(raw_subject, label="X-Agent-Subject")
        except ValueError as exc:
            raise ValidationErr(str(exc)) from exc
        if required_scope not in principal.scopes:
            raise ForbiddenErr(f"이 작업에는 {required_scope} scope가 필요합니다.")
        decision = registry.consume_rate_limit(
            principal,
            subject_hash=subject_hash,
            operation=operation,
        )
        if not decision.allowed:
            raise RateLimitErr(
                "등록 에이전트 요청 한도를 초과했습니다.",
                retry_after_seconds=decision.retry_after_seconds,
            )
        return context

    _dependency.__name__ = f"require_{operation}_{required_scope.replace(':', '_')}"
    return _dependency


def set_agent_request_audit(
    request: Request,
    context: AgentRequestContext,
    payload: Any,
    *,
    source_event_id: str | None = None,
) -> str:
    digest = context.hash_payload(payload)
    audit = getattr(request.state, "agent_audit", {})
    audit["request_hash"] = digest
    if source_event_id:
        audit["source_event_hash"] = context.hash_identifier(source_event_id)
    request.state.agent_audit = audit
    return digest


def set_agent_response_audit(
    request: Request,
    context: AgentRequestContext,
    payload: Any,
    **metadata: Any,
) -> None:
    audit = getattr(request.state, "agent_audit", {})
    audit["response_hash"] = context.hash_payload(payload)
    for key in (
        "verdict",
        "abstained",
        "reason_code",
        "rule_engine_version",
        "model_profile",
        "policy_version_ref",
        "citation_refs",
    ):
        if key in metadata:
            audit[key] = metadata[key]
    request.state.agent_audit = audit


__all__ = [
    "AgentRequestContext",
    "get_agent_principal",
    "get_agent_registry",
    "require_agent_scope",
    "set_agent_request_audit",
    "set_agent_response_audit",
]
