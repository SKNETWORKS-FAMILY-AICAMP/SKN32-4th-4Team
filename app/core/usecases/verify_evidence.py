"""Admin-only evidence attestation for the real PostgreSQL ledger."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.core.errors import ValidationErr
from app.core.ports.insurance_repository import InsuranceAdminRepositoryPort


@dataclass(frozen=True)
class EvidenceAttestationResult:
    submission_id: str
    evidence_id: str
    verification_id: str
    verification_method: str
    decision: str
    duplicate: bool


def attest(
    *,
    submission_id: str,
    basis: str,
    admin_login: str,
    repository: InsuranceAdminRepositoryPort,
) -> EvidenceAttestationResult:
    normalized_basis = basis.strip()
    if len(normalized_basis) < 5 or not submission_id.strip() or not admin_login.strip():
        raise ValidationErr("submission_id, admin login, 5자 이상의 검수 근거가 필요합니다.")
    with repository.transaction() as tx:
        evidence = tx.get_evidence_submission(submission_id=submission_id.strip())
        admin_id = tx.resolve_admin_user_id(login=admin_login.strip())
        duplicate = evidence.verification_id is not None
        if not duplicate:
            tx.record_evidence_consistency(
                evidence_id=evidence.evidence_id,
                consistent=True,
                details={
                    "status_source": "admin_review",
                    "submission_id": evidence.submission_id,
                    "basis": normalized_basis,
                },
            )
        verification_id = tx.record_evidence_verification(
            evidence_id=evidence.evidence_id,
            result="verified",
            verification_method="admin_attested",
            verified_by=admin_id,
            reason=normalized_basis,
        )
        if not duplicate:
            tx.record_audit(
                actor_id=admin_id,
                actor_type="admin",
                action="evidence.admin_attest",
                target_table="app.evidence",
                target_id=evidence.evidence_id,
                after={
                    "submission_id": evidence.submission_id,
                    "verification_id": verification_id,
                    "verification_method": "admin_attested",
                },
            )
    return EvidenceAttestationResult(
        submission_id=evidence.submission_id,
        evidence_id=evidence.evidence_id,
        verification_id=verification_id,
        verification_method="admin_attested",
        decision=evidence.decision,
        duplicate=duplicate,
    )


def pending(*, limit: int, repository: InsuranceAdminRepositoryPort) -> list[dict]:
    with repository.transaction() as tx:
        rows = tx.list_pending_evidence(limit=limit)
    return [asdict(row) for row in rows]


__all__ = ["EvidenceAttestationResult", "attest", "pending"]
