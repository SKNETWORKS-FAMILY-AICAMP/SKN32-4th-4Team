"""실제 보험 ``core/app/ops`` PostgreSQL용 명시 SQL repository.

기존 ``app.db.Base``는 SQLite 커머스·인증 모델과 결합돼 있으므로 가져오지 않는다.
연결 실패나 PostgreSQL 오류를 SQLite/file 결과로 폴백하지 않는다.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Iterator, Sequence

from app.core.errors import (
    AppError,
    ConflictErr,
    ForbiddenErr,
    InfraError,
    NotFoundErr,
    TransientInfraError,
    ValidationErr,
)
from app.core.ports.insurance_repository import (
    InsuranceAgentClientSnapshot,
    InsuranceClauseReference,
    InsuranceEvidenceSubmission,
    InsuranceInteractionResult,
    InsuranceKcdReference,
    InsurancePolicyReference,
    InsuranceReviewSnapshot,
    InsuranceStoredPrecheck,
    InsuranceStoredOutcome,
    InsuranceTransactionPort,
)
from db.postgres.pool import connection


def _postgres_error(exc: Exception) -> AppError:
    state = getattr(exc, "sqlstate", None) or "unknown"
    diag = getattr(exc, "diag", None)
    constraint = getattr(diag, "constraint_name", None) if diag else None
    marker = constraint or state
    if state in {"23505", "23P01"}:
        return ConflictErr(f"보험 데이터가 기존 레코드와 충돌합니다 ({marker}).")
    if state in {"23503", "23514", "22P02", "22007", "22023"}:
        return ValidationErr(f"보험 데이터 무결성 조건을 만족하지 않습니다 ({marker}).")
    if state == "42501":
        return ForbiddenErr(f"보험 데이터 쓰기 권한이 없습니다 ({marker}).")
    if state in {"40001", "40P01", "55P03", "57014", "53300"} or state.startswith("08"):
        return TransientInfraError(
            f"보험 PostgreSQL의 일시적 충돌 또는 자원 문제입니다 ({state})."
        )
    return InfraError(f"보험 PostgreSQL 작업에 실패했습니다 (SQLSTATE {state}).")


class PgInsuranceTransaction:
    """호출자가 소유한 한 PostgreSQL transaction 안에서만 동작한다."""

    def __init__(self, conn):
        self._conn = conn

    @staticmethod
    def _id(row) -> str:
        return str(row[0])

    def create_subject(
        self,
        *,
        age_band: str | None = None,
        sex: str | None = None,
        subject_ref_hash: str | None = None,
        retention_until: datetime | None = None,
    ) -> str:
        row = self._conn.execute(
            """
            INSERT INTO app.subject(age_band,sex,subject_ref_hash,retention_until)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (subject_ref_hash)
                WHERE subject_ref_hash IS NOT NULL AND deleted_at IS NULL DO UPDATE
                SET subject_ref_hash=EXCLUDED.subject_ref_hash
            RETURNING id
            """,
            (age_band, sex, subject_ref_hash, retention_until),
        ).fetchone()
        return self._id(row)

    def create_policy_holding(
        self,
        *,
        subject_id: str,
        product_id: str,
        policy_version_id: str,
        enrolled_on: date,
        terminated_on: date | None = None,
    ) -> str:
        row = self._conn.execute(
            "INSERT INTO app.policy_holding("
            "subject_id,product_id,policy_version_id,enrolled_on,terminated_on) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (subject_id, product_id, policy_version_id, enrolled_on, terminated_on),
        ).fetchone()
        return self._id(row)

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
    ) -> str:
        from psycopg.types.json import Jsonb

        row = self._conn.execute(
            "INSERT INTO app.coverage_review("
            "subject_id,policy_holding_id,incident_on,channel,agent_client_id,"
            "retention_until,request_key_hash,request_payload_hash,response_snapshot,"
            "trace_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                subject_id,
                policy_holding_id,
                incident_on,
                channel,
                agent_client_id,
                retention_until,
                request_key_hash,
                request_payload_hash,
                Jsonb(response_snapshot) if response_snapshot is not None else None,
                trace_id,
            ),
        ).fetchone()
        return self._id(row)

    def add_diagnosis(
        self,
        *,
        review_id: str,
        kcd_code_id: str | None = None,
        raw_kcd_code: str | None = None,
        ocr_confidence: Decimal | None = None,
        user_corrected: bool = False,
        corrected_at: datetime | None = None,
    ) -> str:
        row = self._conn.execute(
            "INSERT INTO app.case_diagnosis("
            "case_id,kcd_code_id,raw_kcd_code,ocr_confidence,user_corrected,corrected_at) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                review_id,
                kcd_code_id,
                raw_kcd_code,
                ocr_confidence,
                user_corrected,
                corrected_at,
            ),
        ).fetchone()
        return self._id(row)

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
    ) -> str:
        from psycopg.types.json import Jsonb

        missing = Jsonb(list(missing_documents)) if missing_documents is not None else None
        row = self._conn.execute(
            "INSERT INTO app.assessment("
            "case_id,policy_version_id,verdict,abstained,abstain_reason,"
            "missing_documents,rule_engine_version,as_of) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,coalesce(%s,now())) RETURNING id",
            (
                review_id,
                policy_version_id,
                verdict,
                abstained,
                abstain_reason,
                missing,
                rule_engine_version,
                as_of,
            ),
        ).fetchone()
        return self._id(row)

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
    ) -> None:
        from psycopg.types.json import Jsonb

        self._conn.execute(
            "INSERT INTO app.assessment_clause_citation("
            "assessment_id,policy_clause_id,citeable,role,content_hash,quote,"
            "locator,policy_version_id) VALUES (%s,%s,true,%s,%s,%s,%s,%s)",
            (
                assessment_id,
                policy_clause_id,
                role,
                content_hash,
                quote,
                Jsonb(locator),
                policy_version_id,
            ),
        )

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
    ) -> str:
        row = self._conn.execute(
            "INSERT INTO app.claim(case_id,assessment_id,claimed_on,claimed_amount,"
            "submission_id,source_event_key_hash,source_payload_hash) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                review_id,
                assessment_id,
                claimed_on,
                claimed_amount,
                submission_id,
                source_event_key_hash,
                source_payload_hash,
            ),
        ).fetchone()
        return self._id(row)

    def create_outcome(
        self,
        *,
        claim_id: str,
        decision: str,
        decided_on: date,
        paid_amount: Decimal | None = None,
        reason: str | None = None,
    ) -> str:
        row = self._conn.execute(
            "INSERT INTO app.outcome(claim_id,decision,paid_amount,decided_on,reason) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (claim_id, decision, paid_amount, decided_on, reason),
        ).fetchone()
        return self._id(row)

    def create_evidence(
        self,
        *,
        outcome_id: str,
        doc_type: str,
        sha256_hash: str,
        stored_ref: str,
        submission_id: str | None = None,
    ) -> str:
        row = self._conn.execute(
            "INSERT INTO app.evidence("
            "outcome_id,doc_type,sha256_hash,stored_ref,submission_id) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (outcome_id, doc_type, sha256_hash, stored_ref, submission_id),
        ).fetchone()
        return self._id(row)

    def record_evidence_consistency(
        self,
        *,
        evidence_id: str,
        consistent: bool,
        details: dict[str, object] | None = None,
    ) -> dict[str, object]:
        from psycopg.types.json import Jsonb

        row = self._conn.execute(
            "SELECT app.record_evidence_consistency(%s,%s,%s)",
            (evidence_id, consistent, Jsonb(details or {})),
        ).fetchone()
        return dict(row[0])

    def record_evidence_verification(
        self,
        *,
        evidence_id: str,
        result: str,
        verification_method: str,
        verified_by: str | None,
        reason: str | None = None,
    ) -> str:
        row = self._conn.execute(
            "SELECT app.record_evidence_verification(%s,%s,%s,%s,%s)",
            (evidence_id, result, verification_method, verified_by, reason),
        ).fetchone()
        return self._id(row)

    def get_review(self, review_id: str) -> InsuranceReviewSnapshot:
        row = self._conn.execute(
            "SELECT id,subject_id,policy_holding_id,incident_on,channel "
            "FROM app.coverage_review WHERE id=%s AND deleted_at IS NULL",
            (review_id,),
        ).fetchone()
        if row is None:
            raise NotFoundErr("coverage review를 찾지 못했습니다.")
        diagnosis_ids = tuple(
            str(item[0])
            for item in self._conn.execute(
                "SELECT id FROM app.case_diagnosis WHERE case_id=%s ORDER BY id",
                (review_id,),
            ).fetchall()
        )
        assessment_ids = tuple(
            str(item[0])
            for item in self._conn.execute(
                "SELECT id FROM app.assessment WHERE case_id=%s ORDER BY as_of,id",
                (review_id,),
            ).fetchall()
        )
        return InsuranceReviewSnapshot(
            review_id=str(row[0]),
            subject_id=str(row[1]) if row[1] is not None else None,
            policy_holding_id=str(row[2]) if row[2] is not None else None,
            incident_on=row[3],
            channel=row[4],
            diagnosis_ids=diagnosis_ids,
            assessment_ids=assessment_ids,
        )

    def get_review_by_trace(
        self, trace_id: str, *, request_key_hash: str | None = None
    ) -> InsuranceReviewSnapshot:
        rows = self._conn.execute(
            "SELECT id FROM app.coverage_review "
            "WHERE trace_id=%s AND deleted_at IS NULL "
            "AND (%s::text IS NULL OR request_key_hash=%s::text) ORDER BY id",
            (trace_id, request_key_hash, request_key_hash),
        ).fetchall()
        if not rows:
            raise NotFoundErr("precheck trace에 대응하는 coverage review를 찾지 못했습니다.")
        if len(rows) != 1:
            raise InfraError("precheck trace에 둘 이상의 coverage review가 연결돼 있습니다.")
        return self.get_review(str(rows[0][0]))

    def lock_outcome_request(self, *, source_event_key_hash: str) -> None:
        self._conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"outcome\x1f{source_event_key_hash}",),
        )

    @staticmethod
    def _stored_outcome(rows) -> InsuranceStoredOutcome | None:
        if not rows:
            return None
        if len(rows) != 1:
            raise InfraError("하나의 claim observation에 증빙이 둘 이상 연결돼 있습니다.")
        row = rows[0]
        return InsuranceStoredOutcome(
            submission_id=row[0],
            review_id=str(row[1]),
            claim_id=str(row[2]),
            outcome_id=str(row[3]),
            evidence_id=str(row[4]),
            source_payload_hash=row[5],
        )

    def get_outcome_by_request(
        self, *, source_event_key_hash: str
    ) -> InsuranceStoredOutcome | None:
        rows = self._conn.execute(
            "SELECT c.submission_id,c.case_id,c.id,o.id,e.id,c.source_payload_hash "
            "FROM app.claim c JOIN app.outcome o ON o.claim_id=c.id "
            "JOIN app.evidence e ON e.outcome_id=o.id AND e.submission_id=c.submission_id "
            "WHERE c.source_event_key_hash=%s ORDER BY e.id",
            (source_event_key_hash,),
        ).fetchall()
        return self._stored_outcome(rows)

    def get_outcome_by_review(
        self, *, review_id: str
    ) -> InsuranceStoredOutcome | None:
        rows = self._conn.execute(
            "SELECT c.submission_id,c.case_id,c.id,o.id,e.id,c.source_payload_hash "
            "FROM app.claim c JOIN app.outcome o ON o.claim_id=c.id "
            "JOIN app.evidence e ON e.outcome_id=o.id AND e.submission_id=c.submission_id "
            "WHERE c.case_id=%s ORDER BY e.id",
            (review_id,),
        ).fetchall()
        return self._stored_outcome(rows)

    def lock_precheck_request(
        self,
        *,
        channel: str,
        request_key_hash: str,
        agent_client_id: str | None = None,
    ) -> None:
        lock_scope = agent_client_id or f"anonymous:{channel}"
        self._conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"precheck\x1f{lock_scope}\x1f{request_key_hash}",),
        )

    def get_precheck_by_request(
        self,
        *,
        channel: str,
        request_key_hash: str,
        agent_client_id: str | None = None,
    ) -> InsuranceStoredPrecheck | None:
        if agent_client_id is None:
            row = self._conn.execute(
                "SELECT id,request_payload_hash,response_snapshot "
                "FROM app.coverage_review WHERE agent_client_id IS NULL "
                "AND channel=%s AND request_key_hash=%s AND deleted_at IS NULL",
                (channel, request_key_hash),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT id,request_payload_hash,response_snapshot "
                "FROM app.coverage_review WHERE agent_client_id=%s "
                "AND request_key_hash=%s AND deleted_at IS NULL",
                (agent_client_id, request_key_hash),
            ).fetchone()
        if row is None:
            return None
        return InsuranceStoredPrecheck(
            review_id=str(row[0]),
            request_payload_hash=str(row[1]),
            response_snapshot=dict(row[2]),
        )

    def get_agent_client(self, agent_client_id: str) -> InsuranceAgentClientSnapshot:
        row = self._conn.execute(
            "SELECT agent_client_id,name,api_key_hash,rate_limit_rpm,status "
            "FROM ops.agent_client WHERE agent_client_id=%s",
            (agent_client_id,),
        ).fetchone()
        if row is None:
            raise NotFoundErr("agent client를 찾지 못했습니다.")
        return InsuranceAgentClientSnapshot(
            agent_client_id=row[0],
            name=row[1],
            api_key_hash=row[2],
            rate_limit_rpm=int(row[3]),
            status=row[4],
        )

    def record_agent_auth_attempt(
        self,
        *,
        log_id: str,
        result: str,
        agent_client_id: str | None = None,
        retention_until: datetime | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO ops.agent_client_auth_log("
            "log_id,agent_client_id,result,retention_until) VALUES (%s,%s,%s,%s)",
            (log_id, agent_client_id, result, retention_until),
        )

    def grant_consent(
        self,
        *,
        subject_id: str,
        purpose: str,
        policy_version_id: str | None = None,
        granted_at: datetime | None = None,
        retention_until: datetime | None = None,
    ) -> str:
        row = self._conn.execute(
            "SELECT ops.grant_consent(%s,%s,%s,coalesce(%s,now()),%s)",
            (
                subject_id,
                purpose,
                policy_version_id,
                granted_at,
                retention_until,
            ),
        ).fetchone()
        return self._id(row)

    def revoke_consent(
        self,
        *,
        consent_id: str,
        revoked_at: datetime | None = None,
    ) -> str:
        row = self._conn.execute(
            "SELECT ops.revoke_consent(%s,coalesce(%s,now()))",
            (consent_id, revoked_at),
        ).fetchone()
        return self._id(row)

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
    ) -> InsuranceInteractionResult:
        values = (
            channel,
            agent_client_id,
            source_event_id,
            session_token,
            actor_kind,
            question_masked,
            answer,
            abstained,
            gap_status,
            promoted_ref,
        )
        row = self._conn.execute(
            "INSERT INTO ops.interaction_log("
            "channel,agent_client_id,source_event_id,session_token,actor_kind,"
            "question_masked,answer,abstained,gap_status,promoted_ref) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING RETURNING id",
            values,
        ).fetchone()
        if row is not None:
            return InsuranceInteractionResult(self._id(row))
        if source_event_id is None:
            raise ConflictErr("source_event_id 없는 interaction이 예상치 못하게 충돌했습니다.")

        if agent_client_id is None:
            existing = self._conn.execute(
                "SELECT id,channel,agent_client_id,source_event_id,session_token,"
                "actor_kind,question_masked,answer,abstained,gap_status,promoted_ref "
                "FROM ops.interaction_log WHERE agent_client_id IS NULL "
                "AND channel=%s AND source_event_id=%s",
                (channel, source_event_id),
            ).fetchone()
        else:
            existing = self._conn.execute(
                "SELECT id,channel,agent_client_id,source_event_id,session_token,"
                "actor_kind,question_masked,answer,abstained,gap_status,promoted_ref "
                "FROM ops.interaction_log WHERE agent_client_id=%s AND source_event_id=%s",
                (agent_client_id, source_event_id),
            ).fetchone()
        if existing is None or tuple(existing[1:]) != values:
            raise ConflictErr("같은 source_event_id에 다른 interaction payload가 있습니다.")
        return InsuranceInteractionResult(str(existing[0]), duplicate=True)

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
    ) -> int:
        from psycopg.types.json import Jsonb

        row = self._conn.execute(
            "INSERT INTO ops.audit_log("
            "actor_id,actor_type,action,target_table,target_id,before,after) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (
                actor_id,
                actor_type,
                action,
                target_table,
                target_id,
                Jsonb(before) if before is not None else None,
                Jsonb(after) if after is not None else None,
            ),
        ).fetchone()
        return int(row[0])


class PgInsuranceAdminTransaction(PgInsuranceTransaction):
    """insurance_owner 멤버인 운영자 로그인으로만 사용하는 변경 경계."""

    def resolve_admin_user_id(self, *, login: str) -> str:
        rows = self._conn.execute(
            "SELECT id FROM ops.admin_user WHERE login=%s "
            "AND role IN ('admin','reviewer') ORDER BY id",
            (login,),
        ).fetchall()
        if not rows:
            raise NotFoundErr("실제 보험 원장의 reviewer/admin 계정을 찾지 못했습니다.")
        if len(rows) != 1:
            raise InfraError("관리자 login이 실제 보험 원장에서 중복됐습니다.")
        return str(rows[0][0])

    @staticmethod
    def _evidence_submission(row) -> InsuranceEvidenceSubmission:
        return InsuranceEvidenceSubmission(
            submission_id=row[0],
            evidence_id=str(row[1]),
            decision=row[2],
            doc_type=row[3],
            sha256_hash=row[4],
            stored_ref=row[5],
            verification_id=str(row[6]) if row[6] is not None else None,
            verification_result=row[7],
            verification_method=row[8],
            verification_reason=row[9],
        )

    def get_evidence_submission(
        self, *, submission_id: str
    ) -> InsuranceEvidenceSubmission:
        rows = self._conn.execute(
            "SELECT c.submission_id,e.id,o.decision,e.doc_type,e.sha256_hash,"
            "e.stored_ref,v.id,v.result,v.verification_method,v.reason "
            "FROM app.claim c JOIN app.outcome o ON o.claim_id=c.id "
            "JOIN app.evidence e ON e.outcome_id=o.id AND e.submission_id=c.submission_id "
            "LEFT JOIN app.evidence_verification v ON v.evidence_id=e.id "
            "WHERE c.submission_id=%s ORDER BY e.id",
            (submission_id,),
        ).fetchall()
        if not rows:
            raise NotFoundErr("검수할 claim submission을 찾지 못했습니다.")
        if len(rows) != 1:
            raise InfraError("claim submission에 증빙이 정확히 한 건 연결되지 않았습니다.")
        return self._evidence_submission(rows[0])

    def list_pending_evidence(
        self, *, limit: int
    ) -> tuple[InsuranceEvidenceSubmission, ...]:
        rows = self._conn.execute(
            "SELECT c.submission_id,e.id,o.decision,e.doc_type,e.sha256_hash,"
            "e.stored_ref,NULL,NULL,NULL,NULL "
            "FROM app.claim c JOIN app.outcome o ON o.claim_id=c.id "
            "JOIN app.evidence e ON e.outcome_id=o.id AND e.submission_id=c.submission_id "
            "LEFT JOIN app.evidence_verification v ON v.evidence_id=e.id "
            "WHERE c.submission_id IS NOT NULL AND v.id IS NULL "
            "ORDER BY e.submitted_at,e.id LIMIT %s",
            (limit,),
        ).fetchall()
        return tuple(self._evidence_submission(row) for row in rows)

    def register_agent_client(
        self,
        *,
        agent_client_id: str,
        name: str,
        api_key_hash: str,
        rate_limit_rpm: int,
    ) -> InsuranceAgentClientSnapshot:
        row = self._conn.execute(
            "INSERT INTO ops.agent_client("
            "agent_client_id,name,api_key_hash,rate_limit_rpm,status) "
            "VALUES (%s,%s,%s,%s,'active') ON CONFLICT DO NOTHING "
            "RETURNING agent_client_id,name,api_key_hash,rate_limit_rpm,status",
            (agent_client_id, name, api_key_hash, rate_limit_rpm),
        ).fetchone()
        if row is None:
            existing = self.get_agent_client(agent_client_id)
            if existing != InsuranceAgentClientSnapshot(
                agent_client_id=agent_client_id,
                name=name,
                api_key_hash=api_key_hash,
                rate_limit_rpm=rate_limit_rpm,
                status="active",
            ):
                raise ConflictErr("같은 agent_client_id에 다른 등록 정보가 있습니다.")
            return existing
        return InsuranceAgentClientSnapshot(
            agent_client_id=row[0],
            name=row[1],
            api_key_hash=row[2],
            rate_limit_rpm=int(row[3]),
            status=row[4],
        )

    def rotate_agent_key_hash(
        self,
        *,
        agent_client_id: str,
        api_key_hash: str,
    ) -> None:
        row = self._conn.execute(
            "UPDATE ops.agent_client SET api_key_hash=%s "
            "WHERE agent_client_id=%s AND status='active' RETURNING agent_client_id",
            (api_key_hash, agent_client_id),
        ).fetchone()
        if row is None:
            raise NotFoundErr("활성 agent client를 찾지 못했습니다.")

    def disable_agent_client(
        self,
        *,
        agent_client_id: str,
        disabled_at: datetime | None = None,
    ) -> None:
        row = self._conn.execute(
            "UPDATE ops.agent_client SET status='disabled',"
            "disabled_at=coalesce(disabled_at,%s,now()) "
            "WHERE agent_client_id=%s RETURNING agent_client_id",
            (disabled_at, agent_client_id),
        ).fetchone()
        if row is None:
            raise NotFoundErr("agent client를 찾지 못했습니다.")

    def list_agent_clients(self) -> tuple[InsuranceAgentClientSnapshot, ...]:
        rows = self._conn.execute(
            "SELECT agent_client_id,name,api_key_hash,rate_limit_rpm,status "
            "FROM ops.agent_client ORDER BY agent_client_id"
        ).fetchall()
        return tuple(
            InsuranceAgentClientSnapshot(
                agent_client_id=row[0],
                name=row[1],
                api_key_hash=row[2],
                rate_limit_rpm=int(row[3]),
                status=row[4],
            )
            for row in rows
        )

    def sync_agent_client_mirror(
        self,
        *,
        agent_client_id: str,
        name: str,
        api_key_hash: str,
        rate_limit_rpm: int,
        status: str,
    ) -> InsuranceAgentClientSnapshot:
        if status not in {"active", "disabled"}:
            raise ValidationErr("agent client status는 active 또는 disabled여야 합니다.")
        if rate_limit_rpm <= 0:
            raise ValidationErr("agent client rate limit은 0보다 커야 합니다.")
        row = self._conn.execute(
            """
            INSERT INTO ops.agent_client(
                agent_client_id,name,api_key_hash,rate_limit_rpm,status,disabled_at
            ) VALUES (%s,%s,%s,%s,%s,
                      CASE WHEN %s='disabled' THEN now() ELSE NULL END)
            ON CONFLICT (agent_client_id) DO UPDATE SET
                name=EXCLUDED.name,
                api_key_hash=EXCLUDED.api_key_hash,
                rate_limit_rpm=EXCLUDED.rate_limit_rpm,
                status=EXCLUDED.status,
                disabled_at=CASE
                    WHEN EXCLUDED.status='disabled'
                        THEN coalesce(ops.agent_client.disabled_at, now())
                    ELSE NULL
                END
            RETURNING agent_client_id,name,api_key_hash,rate_limit_rpm,status
            """,
            (
                agent_client_id,
                name,
                api_key_hash,
                rate_limit_rpm,
                status,
                status,
            ),
        ).fetchone()
        return InsuranceAgentClientSnapshot(
            agent_client_id=row[0],
            name=row[1],
            api_key_hash=row[2],
            rate_limit_rpm=int(row[3]),
            status=row[4],
        )


class PgInsuranceRepository:
    """연결 하나를 업무 transaction 하나로 노출하는 repository 진입점."""

    def __init__(self, dsn: str):
        self._dsn = (dsn or "").strip()
        if not self._dsn:
            raise InfraError("INSURANCE_PG_DSN이 설정되지 않았습니다.")

    @classmethod
    def from_settings(cls) -> "PgInsuranceRepository":
        from app.core.config import get_settings

        return cls(get_settings().INSURANCE_PG_DSN)

    def _connect(self):
        import psycopg

        try:
            conn = connection(self._dsn)
            conn.execute("SET statement_timeout = '10s'")
            conn.execute("SET lock_timeout = '3s'")
            return conn
        except psycopg.Error as exc:
            raise _postgres_error(exc) from exc
        except Exception as exc:  # noqa: BLE001
            raise InfraError(f"보험 PostgreSQL에 연결할 수 없습니다: {exc}") from exc

    def readiness(self) -> dict[str, object]:
        """runtime DSN에서 원장 테이블과 migration ledger 상태를 확인한다."""

        required = (
            "app.subject",
            "app.coverage_review",
            "app.assessment",
            "app.claim",
            "app.outcome",
            "app.evidence",
            "ops.agent_client",
            "ops.consent",
            "ops.interaction_log",
            "ops.audit_log",
            "public.schema_migration",
        )
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT name,to_regclass(name) "
                    "FROM unnest(%s::text[]) AS t(name)",
                    (list(required),),
                ).fetchall()
                latest = conn.execute(
                    "SELECT max(filename) FROM public.schema_migration "
                    "WHERE filename ~ '^[0-9]{3}_.*\\.sql$'"
                ).fetchone()[0]
                database = conn.execute("SELECT current_database()").fetchone()[0]
            missing = [row[0] for row in rows if row[1] is None]
            return {
                "backend": "postgres",
                "database": database,
                #: ★★**마지막 마이그레이션 이름을 박지 않는다** (2026-08-26).
                #:
                #:   `latest == "016_runtime_ownership_and_grants.sql"` 로 굳어 있었다.
                #:   그래서 017·018·019 를 더하자 **`ready=false`** 가 됐다 —
                #:   스키마가 더 최신인데 「준비 안 됨」이라고 답한 것이다.
                #:   마이그레이션을 더할 때마다 이 줄과 시험을 같이 고쳐야 했고,
                #:   안 고치면 **운영이 「준비 안 됨」으로 막힌다.**
                #:
                #:   재는 것은 「필요한 테이블이 다 있나」다. 그것은 `missing` 이 이미 답한다.
                #:   ledger 는 **「적용 기록이 있는가」**만 본다 — 몇 번까지인지는
                #:   여기서 판정할 일이 아니다(적용기가 checksum 으로 이미 지킨다).
                "ready": not missing and bool(latest),
                "missing_tables": missing,
                "migration_latest": latest,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "backend": "postgres",
                "database": "insurance_real",
                "ready": False,
                "reason": str(exc)[:200],
            }

    def _transaction(self, conn) -> InsuranceTransactionPort:
        return PgInsuranceTransaction(conn)

    def get_precheck_by_request(
        self,
        *,
        channel: str,
        request_key_hash: str,
        agent_client_id: str | None = None,
    ) -> InsuranceStoredPrecheck | None:
        conn = self._connect()
        try:
            return PgInsuranceTransaction(conn).get_precheck_by_request(
                channel=channel,
                request_key_hash=request_key_hash,
                agent_client_id=agent_client_id,
            )
        except AppError:
            raise
        except Exception as exc:
            if getattr(exc, "sqlstate", None):
                raise _postgres_error(exc) from exc
            raise
        finally:
            conn.rollback()
            conn.close()

    @staticmethod
    def _exactly_one(rows, *, missing: str, ambiguous: str):
        if not rows:
            raise InfraError(missing)
        if len(rows) != 1:
            raise InfraError(ambiguous)
        return rows[0]

    def resolve_policy_reference(
        self, *, document_sha256: str
    ) -> InsurancePolicyReference:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT pv.id,pv.product_id FROM core.policy_version pv "
                "JOIN core.confirmed_policy_document d "
                "ON d.id=pv.confirmed_document_id WHERE d.sha256=%s "
                "AND pv.product_id IS NOT NULL ORDER BY pv.id",
                (document_sha256,),
            ).fetchall()
            row = self._exactly_one(
                rows,
                missing="문서 SHA에 대응하는 policy version을 찾지 못했습니다.",
                ambiguous="문서 SHA에 둘 이상의 policy version이 연결돼 있습니다.",
            )
            return InsurancePolicyReference(
                policy_version_id=str(row[0]), product_id=str(row[1])
            )
        except AppError:
            raise
        except Exception as exc:  # psycopg 오류는 transaction과 같은 규칙으로 변환한다.
            if getattr(exc, "sqlstate", None):
                raise _postgres_error(exc) from exc
            raise
        finally:
            conn.rollback()
            conn.close()

    def resolve_kcd_reference(
        self, *, code: str, incident_on: date
    ) -> InsuranceKcdReference:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT kc.id,kv.label,kc.code FROM core.kcd_code kc "
                "JOIN core.kcd_version kv ON kv.id=kc.kcd_version_id "
                "WHERE upper(kc.code)=upper(%s) "
                "AND (kv.effective_from IS NULL OR kv.effective_from<=%s) "
                "AND (kv.effective_to IS NULL OR kv.effective_to>=%s) "
                "ORDER BY kv.effective_from NULLS LAST,kv.id",
                (code, incident_on, incident_on),
            ).fetchall()
            row = self._exactly_one(
                rows,
                missing=f"사고일에 적용되는 KCD code를 찾지 못했습니다 ({code}).",
                ambiguous=f"사고일에 적용되는 KCD code가 둘 이상입니다 ({code}).",
            )
            return InsuranceKcdReference(
                kcd_code_id=str(row[0]), version_label=row[1], code=row[2]
            )
        except AppError:
            raise
        except Exception as exc:
            if getattr(exc, "sqlstate", None):
                raise _postgres_error(exc) from exc
            raise
        finally:
            conn.rollback()
            conn.close()

    def resolve_clause_reference(
        self,
        *,
        policy_version_id: str,
        document_sha256: str,
        source_kind: str,
        ordinal: int,
        quote: str,
        content_hash: str = "",
    ) -> InsuranceClauseReference:
        if source_kind not in {"clause", "annex"} or ordinal < 0 or not quote:
            raise ValidationErr("occurrence source_kind/ordinal/quote가 유효하지 않습니다.")
        if not content_hash.strip():
            #: ★★**내용 해시 없이는 조회하지 않는다** (2026-08-27).
            #:   자리(`ordinal`)만 맞춰 조회하면 그 자리가 밀렸을 때 **다른 조항**이 나온다.
            #:   실측: 색인 순번과 core 순번의 자리 일치가 62.51% 뿐이었다.
            raise ValidationErr(
                "인용 occurrence 에 content_hash 가 없습니다 — 자리만으로는 "
                "그 자리가 밀렸는지 확인할 수 없습니다."
            )
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT pc.id,pc.policy_version_id,pc.source_kind,pc.ordinal,"
                "pc.content_hash,pc.kind,pc.locator "
                "FROM core.policy_clause pc "
                "JOIN core.confirmed_policy_document d "
                "ON d.id=pc.confirmed_document_id "
                "JOIN core.document_extraction de "
                "ON de.id=pc.document_extraction_id "
                "JOIN core.clause_content cc ON cc.content_hash=pc.content_hash "
                #: ★★`pc.ordinal` 은 **산출물이 매긴 순번**이고, 넘어온 값도
                #:   이제 산출물 순번(`source_ordinal`)이다 — 같은 체계다(2026-08-27).
                #:   ★거기에 `content_hash` 를 **함께** 본다. 자리가 밀렸으면 여기서 걸린다.
                #:     한 문서 안에 같은 내용이 두 번 실리는 자리가 2,789개 있어
                #:     해시 단독으로는 모호하다 — 자리와 내용을 둘 다 봐야 유일해진다.
                #:   ★`position(quote in body)>0` 는 그대로 둔다. 세 번째 방어선이다.
                #:     다만 짧고 흔한 인용문이면 약하다(코덱스 지적) — 유일성은 위 둘이 낸다.
                "WHERE pc.policy_version_id=%s AND d.sha256=%s "
                "AND pc.source_kind=%s AND pc.ordinal=%s AND pc.content_hash=%s "
                "AND pc.citeable=true AND de.approval='accepted' "
                "AND position(%s in cc.body)>0 ORDER BY pc.id",
                (
                    policy_version_id,
                    document_sha256,
                    source_kind,
                    ordinal,
                    content_hash,
                    quote,
                ),
            ).fetchall()
            row = self._exactly_one(
                rows,
                missing="accepted extraction에서 인용 occurrence를 찾지 못했습니다.",
                ambiguous="accepted extraction의 인용 occurrence가 둘 이상입니다.",
            )
            return InsuranceClauseReference(
                policy_clause_id=str(row[0]),
                policy_version_id=str(row[1]),
                source_kind=row[2],
                ordinal=int(row[3]),
                content_hash=row[4],
                kind=row[5],
                locator=dict(row[6]),
            )
        except AppError:
            raise
        except Exception as exc:
            if getattr(exc, "sqlstate", None):
                raise _postgres_error(exc) from exc
            raise
        finally:
            conn.rollback()
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[InsuranceTransactionPort]:
        import psycopg

        conn = self._connect()
        try:
            yield self._transaction(conn)
            conn.commit()
        except AppError:
            conn.rollback()
            raise
        except psycopg.Error as exc:
            conn.rollback()
            raise _postgres_error(exc) from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class PgInsuranceAdminRepository(PgInsuranceRepository):
    """runtime DSN과 분리된 owner-member 관리자 DSN 진입점."""

    @classmethod
    def from_settings(cls) -> "PgInsuranceAdminRepository":
        from app.core.config import get_settings

        return cls(get_settings().INSURANCE_ADMIN_PG_DSN)

    def _transaction(self, conn) -> PgInsuranceAdminTransaction:
        return PgInsuranceAdminTransaction(conn)


__all__ = [
    "PgInsuranceAdminRepository",
    "PgInsuranceAdminTransaction",
    "PgInsuranceRepository",
    "PgInsuranceTransaction",
]
