"""사전판정 결과를 실제 보험 원장에 원자·멱등 저장한다."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import date, datetime

from app.core.domain import kcd_ranges
from app.core.domain.precheck_result import PrecheckOutcome
from app.core.errors import ConflictErr, ValidationErr
from app.core.ports.insurance_repository import (
    InsuranceClauseReference,
    InsuranceRepositoryPort,
    InsuranceStoredPrecheck,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class PersistPrecheckCommand:
    outcome: PrecheckOutcome
    enrolled_on: date
    incident_on: date
    kcd_codes: tuple[str, ...]
    channel: str
    idempotency_key: str
    idempotency_secret: str
    request_snapshot: dict[str, object]
    response_snapshot: dict[str, object]
    agent_client_id: str | None = None
    subject_ref_hash: str | None = None
    consent_purpose: str | None = None
    age_band: str | None = None
    sex: str | None = None
    retention_until: datetime | None = None


@dataclass(frozen=True)
class PersistPrecheckResult:
    review_id: str
    duplicate: bool
    response_snapshot: dict[str, object]


def _canonical_hash(value: dict[str, object]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _request_key_hash(command: PersistPrecheckCommand) -> str:
    if len(command.idempotency_secret) < 32:
        raise ValidationErr("precheck idempotency secret은 32자 이상이어야 합니다.")
    key = command.idempotency_key.strip()
    if len(key) < 8:
        raise ValidationErr("precheck idempotency key는 8자 이상이어야 합니다.")
    scope = command.agent_client_id or f"anonymous:{command.channel}"
    message = f"{scope}\x1f{key}".encode("utf-8")
    return hmac.new(command.idempotency_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _occurrence(value: str) -> tuple[str, str, int, str]:
    """`occurrence_id` 를 뜯는다. **v2 만 받는다.**

    ★★v1(`릴리스:sha256:source_kind:ordinal`)은 **거절한다** (2026-08-27).

        v1 의 `ordinal` 은 산출물의 순번이 아니라 **검색용 재번호**였다.
        색인에 무엇이 드는지를 **인용 게이트**가 정하므로, 게이트 판정이 바뀌면
        그 번호가 통째로 밀린다 — 어제 발급한 인용이 다른 조항을 가리킨다.

        ★**조용히 v1 을 받아 주면 안 된다.** 형식이 비슷해서 파싱은 되는데
          가리키는 곳이 다르다. 그런 것이 제일 위험하다 — 틀린 근거가 조용히 저장된다.
          그래서 **모양을 보고 v1 이면 그렇다고 말하며 거절한다.**
    """
    if not isinstance(value, str) or not value.startswith("v2:"):
        raise ValidationErr(
            "인용 occurrence_id 가 v2 형식이 아닙니다"
            "(v2:릴리스:sha256:source_kind:source_ordinal:content_hash). "
            "옛 형식은 검색용 재번호를 쓰고 있어 게이트가 바뀌면 다른 조항을 가리킵니다 "
            "— 받지 않습니다."
        )
    try:
        #: ★★**오른쪽부터 자른다.** `release_id` 에 콜론이 들어갈 수 있기 때문이다.
        #:   왼쪽부터 자르면(`split(":", 5)`) 릴리스가 쪼개져 뒤 필드가 통째로 밀린다 —
        #:   내가 v2 로 옮기면서 그렇게 썼고 `test_occurrence는_release에_colon이…` 가 잡았다.
        #:   v1 은 `rsplit(":", 3)` 으로 이미 이 문제를 피하고 있었다(2026-08-27).
        _release, sha256, source_kind, ordinal, content_hash = value[3:].rsplit(":", 4)
        parsed_ordinal = int(ordinal)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValidationErr("인용 occurrence_id 형식이 올바르지 않습니다.") from exc
    if not _HEX64.fullmatch(sha256) or source_kind not in {"clause", "annex"}:
        raise ValidationErr("인용 occurrence_id의 SHA/source_kind가 올바르지 않습니다.")
    if parsed_ordinal < 0:
        raise ValidationErr("인용 occurrence ordinal은 0 이상이어야 합니다.")
    if not content_hash.strip():
        #: ★내용 해시가 비면 「자리는 맞는데 내용이 뭔지 모른다」다. 그대로 두면
        #:   자리가 밀렸는지 확인할 방법이 없다.
        raise ValidationErr("인용 occurrence_id 에 content_hash 가 없습니다.")
    return sha256, source_kind, parsed_ordinal, content_hash


def _replay(
    existing: InsuranceStoredPrecheck | None,
    *,
    payload_hash: str,
) -> PersistPrecheckResult | None:
    if existing is None:
        return None
    if not hmac.compare_digest(existing.request_payload_hash, payload_hash):
        raise ConflictErr("같은 precheck idempotency key에 다른 payload가 있습니다.")
    return PersistPrecheckResult(
        review_id=existing.review_id,
        duplicate=True,
        response_snapshot=existing.response_snapshot,
    )


def persist(
    command: PersistPrecheckCommand,
    *,
    repository: InsuranceRepositoryPort,
) -> PersistPrecheckResult:
    """판정은 이미 끝난 상태에서 UUID를 정확히 해소하고 write transaction만 연다."""

    key_hash = _request_key_hash(command)
    if command.subject_ref_hash is not None and not _HEX64.fullmatch(command.subject_ref_hash):
        raise ValidationErr("subject reference hash는 HMAC-SHA256 hex 값이어야 합니다.")
    if command.channel == "registered-agent" and (
        not command.subject_ref_hash or not command.consent_purpose
    ):
        raise ValidationErr("registered-agent PostgreSQL 저장에는 subject hash와 consent purpose가 필요합니다.")
    payload_hash = _canonical_hash(command.request_snapshot)
    replay = _replay(
        repository.get_precheck_by_request(
            channel=command.channel,
            request_key_hash=key_hash,
            agent_client_id=command.agent_client_id,
        ),
        payload_hash=payload_hash,
    )
    if replay is not None:
        return replay

    outcome = command.outcome
    policy = None
    document_sha = ""
    if outcome.applied_policy is not None:
        document_sha = outcome.applied_policy.sha256
        if not _HEX64.fullmatch(document_sha):
            raise ValidationErr("적용 약관의 SHA-256이 없거나 올바르지 않습니다.")
        policy = repository.resolve_policy_reference(document_sha256=document_sha)
    elif outcome.citations:
        raise ValidationErr("적용 약관 없이 인용을 저장할 수 없습니다.")

    kcd_ids: list[tuple[str, str | None]] = []
    for raw_code in command.kcd_codes:
        parsed = kcd_ranges.CodeRef.parse(raw_code)
        if parsed is None:
            kcd_ids.append((raw_code, None))
            continue
        resolved = repository.resolve_kcd_reference(
            code=str(parsed), incident_on=command.incident_on
        )
        kcd_ids.append((raw_code, resolved.kcd_code_id))

    citations: list[tuple[object, InsuranceClauseReference]] = []
    for citation in outcome.citations:
        if policy is None:
            raise ValidationErr("policy version 없이 citation을 저장할 수 없습니다.")
        citation_sha, source_kind, ordinal, content_hash = _occurrence(citation.occurrence_id)
        if not hmac.compare_digest(citation_sha, document_sha):
            raise ValidationErr("인용 occurrence가 적용 약관과 다른 문서를 가리킵니다.")
        reference = repository.resolve_clause_reference(
            policy_version_id=policy.policy_version_id,
            document_sha256=document_sha,
            source_kind=source_kind,
            ordinal=ordinal,
            #: ★★자리와 **내용**을 함께 넘긴다. 자리만 맞추면 그 자리가 밀렸을 때
            #:   다른 조항을 저장한다. 한 문서 안에 같은 내용이 두 번 실리는 자리가
            #:   2,789개 있으므로 **내용만으로도 부족하다** — 둘 다 본다.
            content_hash=content_hash,
            quote=citation.quote,
        )
        citations.append((citation, reference))

    abstain_reason = None
    if outcome.abstained:
        if outcome.reason_code is None:
            raise ValidationErr("기권 결과에는 reason_code가 필요합니다.")
        abstain_reason = outcome.reason_code.value

    with repository.transaction() as tx:
        tx.lock_precheck_request(
            channel=command.channel,
            request_key_hash=key_hash,
            agent_client_id=command.agent_client_id,
        )
        replay = _replay(
            tx.get_precheck_by_request(
                channel=command.channel,
                request_key_hash=key_hash,
                agent_client_id=command.agent_client_id,
            ),
            payload_hash=payload_hash,
        )
        if replay is not None:
            return replay

        if command.channel == "registered-agent":
            client = tx.get_agent_client(command.agent_client_id or "")
            if client.status != "active":
                raise ValidationErr("비활성화된 agent client는 보험 원장에 저장할 수 없습니다.")

        subject_kwargs = {
            "age_band": command.age_band,
            "sex": command.sex,
            "retention_until": command.retention_until,
        }
        if command.subject_ref_hash is not None:
            subject_kwargs["subject_ref_hash"] = command.subject_ref_hash
        subject_id = tx.create_subject(**subject_kwargs)
        if command.consent_purpose:
            tx.grant_consent(
                subject_id=subject_id,
                purpose=command.consent_purpose,
                retention_until=command.retention_until,
            )
        holding_id = None
        if policy is not None:
            holding_id = tx.create_policy_holding(
                subject_id=subject_id,
                product_id=policy.product_id,
                policy_version_id=policy.policy_version_id,
                enrolled_on=command.enrolled_on,
            )
        review_id = tx.create_coverage_review(
            subject_id=subject_id,
            policy_holding_id=holding_id,
            incident_on=command.incident_on,
            channel=command.channel,
            agent_client_id=command.agent_client_id,
            retention_until=command.retention_until,
            request_key_hash=key_hash,
            request_payload_hash=payload_hash,
            response_snapshot=command.response_snapshot,
            trace_id=outcome.trace_id or None,
        )
        for raw_code, kcd_code_id in kcd_ids:
            tx.add_diagnosis(
                review_id=review_id,
                kcd_code_id=kcd_code_id,
                raw_kcd_code=raw_code,
            )
        assessment_id = tx.create_assessment(
            review_id=review_id,
            policy_version_id=policy.policy_version_id if policy else None,
            verdict=outcome.verdict.value,
            abstained=outcome.abstained,
            abstain_reason=abstain_reason,
            rule_engine_version=outcome.rule_engine_version,
        )
        for citation, reference in citations:
            tx.add_assessment_citation(
                assessment_id=assessment_id,
                policy_clause_id=reference.policy_clause_id,
                policy_version_id=reference.policy_version_id,
                role="exclusion" if reference.kind == "exclusion" else "ground",
                content_hash=reference.content_hash,
                quote=citation.quote,
                locator=reference.locator,
            )
        if command.channel == "registered-agent":
            tx.record_interaction(
                channel=command.channel,
                actor_kind="agent",
                agent_client_id=command.agent_client_id,
                source_event_id=key_hash,
                question_masked="precheck",
                answer=outcome.verdict.value,
                abstained=outcome.abstained,
                gap_status=abstain_reason,
            )
            tx.record_audit(
                action="agent.precheck.persisted",
                actor_id=None,
                actor_type="agent",
                target_table="app.coverage_review",
                target_id=review_id,
                after={
                    "channel": command.channel,
                    "agent_client_id": command.agent_client_id,
                    "abstained": outcome.abstained,
                },
            )

    return PersistPrecheckResult(
        review_id=review_id,
        duplicate=False,
        response_snapshot=command.response_snapshot,
    )


__all__ = ["PersistPrecheckCommand", "PersistPrecheckResult", "persist"]
