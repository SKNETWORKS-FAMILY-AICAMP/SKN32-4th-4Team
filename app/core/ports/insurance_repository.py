"""보험 PostgreSQL 영속화 경계.

안쪽 계층은 psycopg·SQLAlchemy·구체 DB를 모른다. 한 업무 흐름에서 만들어지는
``subject → evidence`` 사슬은 반드시 :meth:`transaction` 하나 안에서 기록한다.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class InsuranceReviewSnapshot:
    """coverage review와 직접 연결된 하위 레코드의 최소 추적 정보."""

    review_id: str
    subject_id: str | None
    policy_holding_id: str | None
    incident_on: date
    channel: str
    diagnosis_ids: tuple[str, ...]
    assessment_ids: tuple[str, ...]


@dataclass(frozen=True)
class InsuranceAgentClientSnapshot:
    agent_client_id: str
    name: str
    api_key_hash: str
    rate_limit_rpm: int
    status: str


@dataclass(frozen=True)
class InsuranceInteractionResult:
    interaction_id: str
    duplicate: bool = False


@dataclass(frozen=True)
class InsurancePolicyReference:
    policy_version_id: str
    product_id: str


@dataclass(frozen=True)
class InsuranceKcdReference:
    kcd_code_id: str
    version_label: str
    code: str


@dataclass(frozen=True)
class InsuranceClauseReference:
    policy_clause_id: str
    policy_version_id: str
    source_kind: str
    ordinal: int
    content_hash: str
    kind: str
    locator: dict[str, object]


@dataclass(frozen=True)
class InsuranceStoredPrecheck:
    review_id: str
    request_payload_hash: str
    response_snapshot: dict[str, object]


@dataclass(frozen=True)
class InsuranceStoredOutcome:
    submission_id: str
    review_id: str
    claim_id: str
    outcome_id: str
    evidence_id: str
    source_payload_hash: str


@dataclass(frozen=True)
class InsuranceEvidenceSubmission:
    submission_id: str
    evidence_id: str
    decision: str
    doc_type: str
    sha256_hash: str
    stored_ref: str
    verification_id: str | None = None
    verification_result: str | None = None
    verification_method: str | None = None
    verification_reason: str | None = None


@runtime_checkable
class InsuranceTransactionPort(Protocol):
    def create_subject(
        self,
        *,
        age_band: str | None = None,
        sex: str | None = None,
        subject_ref_hash: str | None = None,
        retention_until: datetime | None = None,
    ) -> str: ...

    def create_policy_holding(
        self,
        *,
        subject_id: str,
        product_id: str,
        policy_version_id: str,
        enrolled_on: date,
        terminated_on: date | None = None,
    ) -> str: ...

    def create_coverage_review(
        self,
        *,
        policy_holding_id: str | None,
        incident_on: date,
        channel: str,
        subject_id: str | None = None,
        agent_client_id: str | None = None,
        retention_until: datetime | None = None,
        request_key_hash: str | None = None,
        request_payload_hash: str | None = None,
        response_snapshot: dict[str, object] | None = None,
        trace_id: str | None = None,
    ) -> str: ...

    def add_diagnosis(
        self,
        *,
        review_id: str,
        kcd_code_id: str | None = None,
        raw_kcd_code: str | None = None,
        ocr_confidence: Decimal | None = None,
        user_corrected: bool = False,
        corrected_at: datetime | None = None,
    ) -> str: ...

    def create_assessment(
        self,
        *,
        review_id: str,
        policy_version_id: str | None,
        verdict: str,
        rule_engine_version: str,
        abstained: bool = False,
        abstain_reason: str | None = None,
        missing_documents: Sequence[str] | None = None,
        as_of: datetime | None = None,
    ) -> str: ...

    def add_assessment_citation(
        self,
        *,
        assessment_id: str,
        policy_clause_id: str,
        policy_version_id: str,
        role: str,
        content_hash: str,
        quote: str,
        locator: dict[str, object],
    ) -> None: ...

    def create_claim(
        self,
        *,
        review_id: str,
        assessment_id: str,
        claimed_on: date,
        claimed_amount: Decimal | None = None,
        submission_id: str | None = None,
        source_event_key_hash: str | None = None,
        source_payload_hash: str | None = None,
    ) -> str: ...

    def create_outcome(
        self,
        *,
        claim_id: str,
        decision: str,
        decided_on: date,
        paid_amount: Decimal | None = None,
        reason: str | None = None,
    ) -> str: ...

    def create_evidence(
        self,
        *,
        outcome_id: str,
        doc_type: str,
        sha256_hash: str,
        stored_ref: str,
        submission_id: str | None = None,
    ) -> str: ...

    def record_evidence_consistency(
        self,
        *,
        evidence_id: str,
        consistent: bool,
        details: dict[str, object] | None = None,
    ) -> dict[str, object]: ...

    def record_evidence_verification(
        self,
        *,
        evidence_id: str,
        result: str,
        verification_method: str,
        verified_by: str | None,
        reason: str | None = None,
    ) -> str: ...

    def get_review(self, review_id: str) -> InsuranceReviewSnapshot: ...

    def get_review_by_trace(
        self, trace_id: str, *, request_key_hash: str | None = None
    ) -> InsuranceReviewSnapshot: ...

    def lock_outcome_request(self, *, source_event_key_hash: str) -> None: ...

    def get_outcome_by_request(
        self, *, source_event_key_hash: str
    ) -> InsuranceStoredOutcome | None: ...

    def get_outcome_by_review(
        self, *, review_id: str
    ) -> InsuranceStoredOutcome | None: ...

    def lock_precheck_request(
        self,
        *,
        channel: str,
        request_key_hash: str,
        agent_client_id: str | None = None,
    ) -> None: ...

    def get_precheck_by_request(
        self,
        *,
        channel: str,
        request_key_hash: str,
        agent_client_id: str | None = None,
    ) -> InsuranceStoredPrecheck | None: ...

    def get_agent_client(self, agent_client_id: str) -> InsuranceAgentClientSnapshot: ...

    def record_agent_auth_attempt(
        self,
        *,
        log_id: str,
        result: str,
        agent_client_id: str | None = None,
        retention_until: datetime | None = None,
    ) -> None: ...

    def grant_consent(
        self,
        *,
        subject_id: str,
        purpose: str,
        policy_version_id: str | None = None,
        granted_at: datetime | None = None,
        retention_until: datetime | None = None,
    ) -> str: ...

    def revoke_consent(
        self,
        *,
        consent_id: str,
        revoked_at: datetime | None = None,
    ) -> str: ...

    def record_interaction(
        self,
        *,
        channel: str,
        actor_kind: str,
        abstained: bool,
        agent_client_id: str | None = None,
        source_event_id: str | None = None,
        session_token: str | None = None,
        question_masked: str | None = None,
        answer: str | None = None,
        gap_status: str | None = None,
        promoted_ref: str | None = None,
    ) -> InsuranceInteractionResult: ...

    def record_audit(
        self,
        *,
        action: str,
        actor_id: str | None = None,
        actor_type: str | None = None,
        target_table: str | None = None,
        target_id: str | None = None,
        before: dict[str, object] | None = None,
        after: dict[str, object] | None = None,
    ) -> int: ...


@runtime_checkable
class InsuranceRepositoryPort(Protocol):
    def transaction(self) -> AbstractContextManager[InsuranceTransactionPort]: ...

    def get_precheck_by_request(
        self,
        *,
        channel: str,
        request_key_hash: str,
        agent_client_id: str | None = None,
    ) -> InsuranceStoredPrecheck | None: ...

    def resolve_policy_reference(self, *, document_sha256: str) -> InsurancePolicyReference: ...

    def resolve_kcd_reference(
        self, *, code: str, incident_on: date
    ) -> InsuranceKcdReference: ...

    def resolve_clause_reference(
        self,
        *,
        policy_version_id: str,
        document_sha256: str,
        source_kind: str,
        ordinal: int,
        quote: str,
    ) -> InsuranceClauseReference: ...


@runtime_checkable
class InsuranceAdminTransactionPort(InsuranceTransactionPort, Protocol):
    def resolve_admin_user_id(self, *, login: str) -> str: ...

    def get_evidence_submission(
        self, *, submission_id: str
    ) -> InsuranceEvidenceSubmission: ...

    def list_pending_evidence(
        self, *, limit: int
    ) -> tuple[InsuranceEvidenceSubmission, ...]: ...

    def register_agent_client(
        self,
        *,
        agent_client_id: str,
        name: str,
        api_key_hash: str,
        rate_limit_rpm: int,
    ) -> InsuranceAgentClientSnapshot: ...

    def rotate_agent_key_hash(
        self,
        *,
        agent_client_id: str,
        api_key_hash: str,
    ) -> None: ...

    def disable_agent_client(
        self,
        *,
        agent_client_id: str,
        disabled_at: datetime | None = None,
    ) -> None: ...

    def list_agent_clients(
        self,
    ) -> tuple[InsuranceAgentClientSnapshot, ...]: ...

    def sync_agent_client_mirror(
        self,
        *,
        agent_client_id: str,
        name: str,
        api_key_hash: str,
        rate_limit_rpm: int,
        status: str,
    ) -> InsuranceAgentClientSnapshot: ...


@runtime_checkable
class InsuranceAdminRepositoryPort(Protocol):
    def transaction(self) -> AbstractContextManager[InsuranceAdminTransactionPort]: ...


__all__ = [
    "InsuranceAdminRepositoryPort",
    "InsuranceAdminTransactionPort",
    "InsuranceAgentClientSnapshot",
    "InsuranceClauseReference",
    "InsuranceEvidenceSubmission",
    "InsuranceInteractionResult",
    "InsuranceKcdReference",
    "InsurancePolicyReference",
    "InsuranceRepositoryPort",
    "InsuranceReviewSnapshot",
    "InsuranceStoredPrecheck",
    "InsuranceStoredOutcome",
    "InsuranceTransactionPort",
]
