"""등록 외부 에이전트 전용 보호 REST API."""

from __future__ import annotations

import inspect
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

from app.application.agent_facade import get_agent_facade
from app.auth.agent_client import (
    AgentRequestContext,
    get_agent_principal,
    require_agent_scope,
    set_agent_request_audit,
    set_agent_response_audit,
)
from app.core.errors import InfraError, NotFoundErr, RateLimitErr, ValidationErr
from app.obs.trace import get_trace_id
from app.schemas.agent import (
    AgentCohortResponse,
    AgentErrorResponse,
    AgentObservationRequest,
    AgentPrecheckRequest,
    AgentSupportManifestResponse,
    AgentTermRequest,
    AgentTermResponse,
    ObservationReceipt,
)
from app.schemas.precheck import PrecheckResult


router = APIRouter(
    prefix="/v1/agent",
    tags=["registered-agent"],
    dependencies=[Depends(get_agent_principal)],
    responses={
        401: {"model": AgentErrorResponse},
        403: {"model": AgentErrorResponse},
        409: {"model": AgentErrorResponse},
        429: {"model": AgentErrorResponse},
        503: {"model": AgentErrorResponse},
    },
)

_support_access = require_agent_scope("precheck:read", "support_manifest")
_precheck_access = require_agent_scope("precheck:read", "precheck")
_terms_access = require_agent_scope("terms:read", "terms_explain")
_cohort_access = require_agent_scope("cohort:read", "cohort")
_observation_access = require_agent_scope("observations:write", "observation")


def _call_facade(fn, *args, **kwargs):
    """기존 HTTP 래퍼의 예외를 보호 API 공통 AppError 계약으로 변환한다."""

    try:
        return fn(*args, **kwargs)
    except HTTPException as exc:
        message = str(exc.detail)
        if exc.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY:
            raise ValidationErr(message) from exc
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            raise NotFoundErr(message) from exc
        if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            retry_after = 60
            if exc.headers:
                try:
                    retry_after = int(exc.headers.get("Retry-After", retry_after))
                except (TypeError, ValueError):
                    retry_after = 60
            raise RateLimitErr(
                message,
                retry_after_seconds=retry_after,
            ) from exc
        raise InfraError(message) from exc


@router.get("/support-manifest", response_model=AgentSupportManifestResponse)
def support_manifest(
    request: Request,
    context: Annotated[AgentRequestContext, Depends(_support_access)],
    facade: Annotated[Any, Depends(get_agent_facade)],
) -> dict:
    set_agent_request_audit(request, context, {"operation": "support_manifest"})
    result = _call_facade(facade.support_manifest)
    set_agent_response_audit(request, context, result)
    return result


@router.post("/prechecks", response_model=PrecheckResult)
def precheck(
    body: AgentPrecheckRequest,
    request: Request,
    context: Annotated[AgentRequestContext, Depends(_precheck_access)],
    facade: Annotated[Any, Depends(get_agent_facade)],
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]{7,127}$",
        ),
    ] = None,
) -> PrecheckResult:
    payload = body.model_dump()
    set_agent_request_audit(request, context, payload)
    facade_kwargs: dict[str, Any] = {
        "client_id": context.principal.client_id,
    }
    if "subject_hash" in inspect.signature(facade.precheck).parameters:
        facade_kwargs["subject_hash"] = context.subject_hash
    if idempotency_key is not None:
        facade_kwargs["idempotency_key"] = idempotency_key
    result = _call_facade(facade.precheck, body, **facade_kwargs)
    policy_ref = result.applied_policy.sha256 if result.applied_policy else None
    set_agent_response_audit(
        request,
        context,
        result.model_dump(mode="json"),
        verdict=result.verdict,
        abstained=result.abstained,
        reason_code=result.reason_code,
        rule_engine_version=result.rule_engine_version,
        policy_version_ref=policy_ref,
        citation_refs=[citation.clause_id for citation in result.citations],
    )
    return result


@router.post("/terms/explain", response_model=AgentTermResponse)
def explain_term(
    body: AgentTermRequest,
    request: Request,
    context: Annotated[AgentRequestContext, Depends(_terms_access)],
    facade: Annotated[Any, Depends(get_agent_facade)],
) -> dict:
    payload = body.model_dump()
    set_agent_request_audit(request, context, payload)
    result = _call_facade(facade.explain_term, body)
    model = (result.get("llm") or {}).get("model")
    set_agent_response_audit(request, context, result, model_profile=model)
    return result


@router.get("/cohorts", response_model=AgentCohortResponse)
def cohorts(
    request: Request,
    context: Annotated[AgentRequestContext, Depends(_cohort_access)],
    facade: Annotated[Any, Depends(get_agent_facade)],
    code: str = Query(min_length=1, max_length=20),
    product_id: str = Query(default="", max_length=200),
    age_band: str | None = Query(default=None, max_length=40),
) -> dict:
    payload = {"code": code, "product_id": product_id, "age_band": age_band}
    set_agent_request_audit(request, context, payload)
    result = _call_facade(
        facade.cohort,
        code=code,
        product_id=product_id,
        age_band=age_band,
    )
    set_agent_response_audit(request, context, result)
    return result


@router.post(
    "/observations",
    response_model=ObservationReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_observation(
    body: AgentObservationRequest,
    request: Request,
    context: Annotated[AgentRequestContext, Depends(_observation_access)],
    facade: Annotated[Any, Depends(get_agent_facade)],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]{7,127}$",
        ),
    ],
) -> ObservationReceipt:
    payload = {
        **body.model_dump(),
        "client_ref": context.principal.client_id,
        "idempotency_key": idempotency_key,
    }
    request_hash = set_agent_request_audit(
        request,
        context,
        payload,
        source_event_id=idempotency_key,
    )
    idempotency_hash = context.hash_identifier(idempotency_key)
    reservation = context.registry.reserve_idempotency(
        client_id=context.principal.client_id,
        idempotency_hash=idempotency_hash,
        request_hash=request_hash,
    )
    if reservation.replayed:
        receipt = ObservationReceipt(
            stored=False,
            duplicate=True,
            replayed=True,
            submission_id=reservation.submission_id,
            trace_id=get_trace_id() or "",
            note="이미 완료된 동일 요청입니다. 새 사례를 쌓지 않았습니다.",
        )
        set_agent_response_audit(request, context, receipt.model_dump(mode="json"))
        return receipt

    try:
        stored = facade.submit_observation(
            body,
            client_id=context.principal.client_id,
            idempotency_key=idempotency_key,
        )
        submission_id = stored.submission_id
        context.registry.complete_idempotency(
            client_id=context.principal.client_id,
            idempotency_hash=idempotency_hash,
            request_hash=request_hash,
            submission_id=submission_id,
            lease_token=reservation.lease_token,
        )
    except Exception:
        context.registry.fail_idempotency(
            client_id=context.principal.client_id,
            idempotency_hash=idempotency_hash,
            request_hash=request_hash,
            lease_token=reservation.lease_token,
        )
        raise

    receipt = ObservationReceipt(
        stored=stored.stored,
        duplicate=stored.duplicate,
        replayed=stored.duplicate,
        submission_id=submission_id,
        trace_id=get_trace_id() or "",
        note=(
            "이미 저장된 동일 보고입니다. 새 사례를 쌓지 않았습니다."
            if stored.duplicate
            else "미검증 사례로 접수했습니다. 검수 전에는 실제 코호트에 반영되지 않습니다."
        ),
    )
    set_agent_response_audit(request, context, receipt.model_dump(mode="json"))
    return receipt


__all__ = ["router"]
