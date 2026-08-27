"""Persist a final claim outcome and one evidence record for an exact precheck."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.core.errors import ConflictErr, ValidationErr
from app.core.ports.insurance_repository import (
    InsuranceRepositoryPort,
    InsuranceStoredOutcome,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DECISION = {"paid": "approved", "partial": "partial", "denied": "denied"}


@dataclass(frozen=True)
class PersistOutcomeCommand:
    precheck_trace_id: str
    precheck_idempotency_key: str
    claimed_on: date
    decided_on: date
    outcome: str
    outcome_reason: str
    evidence_doc_type: str
    evidence_sha256: str
    evidence_stored_ref: str
    idempotency_key: str
    idempotency_secret: str
    client_ref: str
    channel: str
    request_snapshot: dict[str, object]
    claimed_amount: Decimal | None = None
    paid_amount: Decimal | None = None
    agent_client_id: str | None = None


@dataclass(frozen=True)
class PersistOutcomeResult:
    submission_id: str
    review_id: str
    claim_id: str
    outcome_id: str
    evidence_id: str
    duplicate: bool = False


def _canonical_hash(value: dict[str, object]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _key_hash(command: PersistOutcomeCommand) -> str:
    if len(command.idempotency_secret) < 32:
        raise ValidationErr("observation idempotency secret은 32자 이상이어야 합니다.")
    key = command.idempotency_key.strip()
    if len(key) < 8:
        raise ValidationErr("observation idempotency key는 8자 이상이어야 합니다.")
    scope = command.agent_client_id or f"{command.channel}:{command.client_ref}"
    message = f"{scope}\x1f{key}".encode("utf-8")
    return hmac.new(
        command.idempotency_secret.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


def _precheck_key_hash(command: PersistOutcomeCommand) -> str:
    key = command.precheck_idempotency_key.strip()
    if len(key) < 8:
        raise ValidationErr("precheck idempotency key는 8자 이상이어야 합니다.")
    scope = command.agent_client_id or f"anonymous:{command.channel}"
    message = f"{scope}\x1f{key}".encode("utf-8")
    return hmac.new(
        command.idempotency_secret.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


def _as_result(
    stored: InsuranceStoredOutcome, *, duplicate: bool
) -> PersistOutcomeResult:
    return PersistOutcomeResult(
        submission_id=stored.submission_id,
        review_id=stored.review_id,
        claim_id=stored.claim_id,
        outcome_id=stored.outcome_id,
        evidence_id=stored.evidence_id,
        duplicate=duplicate,
    )


def _replay(
    stored: InsuranceStoredOutcome | None, *, payload_hash: str
) -> PersistOutcomeResult | None:
    if stored is None:
        return None
    if not hmac.compare_digest(stored.source_payload_hash, payload_hash):
        raise ConflictErr("같은 observation identity에 다른 payload가 있습니다.")
    return _as_result(stored, duplicate=True)


def persist(
    command: PersistOutcomeCommand,
    *,
    repository: InsuranceRepositoryPort,
) -> PersistOutcomeResult:
    """Write claim, outcome, and evidence atomically; never infer missing facts."""

    trace_id = command.precheck_trace_id.strip()
    client_ref = command.client_ref.strip()
    doc_type = command.evidence_doc_type.strip()
    stored_ref = command.evidence_stored_ref.strip()
    sha256 = command.evidence_sha256.strip().lower()
    if not trace_id or not client_ref:
        raise ValidationErr("precheck_trace_id와 client_ref가 필요합니다.")
    if command.outcome not in _DECISION:
        raise ValidationErr("PostgreSQL 원장에는 paid/partial/denied 최종 결과만 저장합니다.")
    if command.decided_on < command.claimed_on:
        raise ValidationErr("decided_on은 claimed_on보다 빠를 수 없습니다.")
    if not doc_type or not stored_ref or not _SHA256.fullmatch(sha256):
        raise ValidationErr("증빙 종류·SHA-256·보관 참조가 모두 필요합니다.")
    if command.claimed_amount is not None and command.claimed_amount < 0:
        raise ValidationErr("claimed_amount는 음수일 수 없습니다.")
    if command.paid_amount is not None and command.paid_amount < 0:
        raise ValidationErr("paid_amount는 음수일 수 없습니다.")

    key_hash = _key_hash(command)
    precheck_key_hash = _precheck_key_hash(command)
    payload_hash = _canonical_hash(command.request_snapshot)
    submission_id = f"obs_{key_hash[:32]}"

    with repository.transaction() as tx:
        if command.channel == "registered-agent":
            client = tx.get_agent_client(command.agent_client_id or "")
            if client.status != "active":
                raise ValidationErr("비활성화된 agent client는 보험 원장에 저장할 수 없습니다.")
        tx.lock_outcome_request(source_event_key_hash=key_hash)
        replay = _replay(
            tx.get_outcome_by_request(source_event_key_hash=key_hash),
            payload_hash=payload_hash,
        )
        if replay is not None:
            return replay

        review = tx.get_review_by_trace(
            trace_id, request_key_hash=precheck_key_hash
        )
        if len(review.assessment_ids) != 1:
            raise ConflictErr(
                "claim을 연결할 assessment가 정확히 한 건이어야 합니다."
            )
        replay = _replay(
            tx.get_outcome_by_review(review_id=review.review_id),
            payload_hash=payload_hash,
        )
        if replay is not None:
            return replay

        claim_id = tx.create_claim(
            review_id=review.review_id,
            assessment_id=review.assessment_ids[0],
            claimed_on=command.claimed_on,
            claimed_amount=command.claimed_amount,
            submission_id=submission_id,
            source_event_key_hash=key_hash,
            source_payload_hash=payload_hash,
        )
        outcome_id = tx.create_outcome(
            claim_id=claim_id,
            decision=_DECISION[command.outcome],
            decided_on=command.decided_on,
            paid_amount=command.paid_amount,
            reason=command.outcome_reason.strip() or None,
        )
        evidence_id = tx.create_evidence(
            outcome_id=outcome_id,
            doc_type=doc_type,
            sha256_hash=sha256,
            stored_ref=stored_ref,
            submission_id=submission_id,
        )
        if command.channel == "registered-agent":
            tx.record_interaction(
                channel=command.channel,
                actor_kind="agent",
                agent_client_id=command.agent_client_id,
                source_event_id=submission_id,
                question_masked="observation",
                answer=_DECISION[command.outcome],
                abstained=False,
            )
            tx.record_audit(
                action="agent.outcome.persisted",
                actor_id=None,
                actor_type="agent",
                target_table="app.claim",
                target_id=claim_id,
                after={
                    "submission_id": submission_id,
                    "agent_client_id": command.agent_client_id,
                    "decision": _DECISION[command.outcome],
                },
            )

    return PersistOutcomeResult(
        submission_id=submission_id,
        review_id=review.review_id,
        claim_id=claim_id,
        outcome_id=outcome_id,
        evidence_id=evidence_id,
    )


__all__ = ["PersistOutcomeCommand", "PersistOutcomeResult", "persist"]
