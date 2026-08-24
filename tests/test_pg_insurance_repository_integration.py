"""생성형 PostgreSQL DB에서 실제 보험 repository 사슬을 검증한다."""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.adapters.pg_insurance_repository import (
    PgInsuranceAdminRepository,
    PgInsuranceRepository,
)
from app.adapters.pg_agent_access import PgAgentAccess
from app.core.errors import ConflictErr, ValidationErr
from app.core.domain.insurance import Verdict
from app.core.domain.precheck_result import (
    AppliedPolicyInfo,
    CitationRef,
    EvidenceTier,
    PrecheckOutcome,
    ReasonCode,
)
from app.core.usecases.persist_precheck import PersistPrecheckCommand, persist
from app.core.usecases.persist_outcome import PersistOutcomeCommand
from app.application.agent_facade import AgentFacade
from app.schemas.agent import AgentObservationRequest
from app.core.usecases.sync_agent_clients import SyncAgentClients
from app.core.domain.agent_access import generate_api_key, hash_api_key
from scripts.db import apply as migration_apply

_DB_PREFIX = "insurance_repo_verify_"
_ROLE_PREFIX = "insurance_repo_runtime_"
_ADMIN_ROLE_PREFIX = "insurance_repo_admin_"


_AGENT_ADMIN_DSN = (
    "host=127.0.0.1 port=5433 user=insurance_agent_admin dbname=insurance_agent"
)
_AGENT_SUPER_DSN = "host=127.0.0.1 port=5433 user=postgres dbname=insurance_agent"


def _apply(target_dsn: str) -> None:
    old_argv = sys.argv[:]
    try:
        sys.argv = ["apply", "--dsn", target_dsn, "--track", "core"]
        assert migration_apply.main() == 0
    finally:
        sys.argv = old_argv


@pytest.fixture
def insurance_repository():
    configured = os.environ.get(
        "INSURANCE_TEST_ADMIN_DSN",
        "postgresql://postgres@127.0.0.1:5433/postgres",
    )
    try:
        base = conninfo_to_dict(configured)
        admin_dsn = make_conninfo(**{**base, "dbname": "postgres"})
        with psycopg.connect(admin_dsn) as conn:
            if not conn.execute(
                "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
            ).fetchone()[0]:
                pytest.skip("임시 DB/role 생성용 PostgreSQL superuser가 아닙니다")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"로컬 PostgreSQL에 연결할 수 없습니다: {str(exc)[:120]}")

    database = _DB_PREFIX + uuid.uuid4().hex[:12]
    role = _ROLE_PREFIX + uuid.uuid4().hex[:12]
    admin_role = _ADMIN_ROLE_PREFIX + uuid.uuid4().hex[:12]
    password = uuid.uuid4().hex + uuid.uuid4().hex
    admin_password = uuid.uuid4().hex + uuid.uuid4().hex
    database_created = False
    role_created = False
    admin_role_created = False
    target_admin_dsn = make_conninfo(**{**base, "dbname": database})
    runtime_dsn = make_conninfo(
        **{**base, "dbname": database, "user": role, "password": password}
    )
    owner_dsn = make_conninfo(
        **{
            **base,
            "dbname": database,
            "user": admin_role,
            "password": admin_password,
        }
    )
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            database_created = True

        _apply(target_admin_dsn)
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("CREATE ROLE {} LOGIN INHERIT PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            role_created = True
            conn.execute(
                sql.SQL("GRANT insurance_app TO {}").format(sql.Identifier(role))
            )
            conn.execute(
                sql.SQL("CREATE ROLE {} LOGIN INHERIT PASSWORD {}").format(
                    sql.Identifier(admin_role), sql.Literal(admin_password)
                )
            )
            admin_role_created = True
            conn.execute(
                sql.SQL("GRANT insurance_owner TO {}").format(
                    sql.Identifier(admin_role)
                )
            )

        with psycopg.connect(target_admin_dsn) as conn:
            admin_id = conn.execute(
                "INSERT INTO ops.admin_user(login,role) "
                "VALUES ('repo-reviewer','reviewer') RETURNING id"
            ).fetchone()[0]
            insurer_id = conn.execute(
                "INSERT INTO core.insurer(slug,legal_name,display_name,kind) "
                "VALUES ('repo-test','Repo Test Insurance','Repo Test','general') "
                "RETURNING id"
            ).fetchone()[0]
            document_id = conn.execute(
                "INSERT INTO core.confirmed_policy_document("
                "sha256,source_url,fetched_at,insurer_id,identified_by,identified_at) "
                "VALUES (%s,'https://example.invalid/repo.pdf',now(),%s,%s,now()) "
                "RETURNING id",
                ("1" * 64, insurer_id, admin_id),
            ).fetchone()[0]
            extraction_id = conn.execute(
                "INSERT INTO core.document_extraction("
                "confirmed_document_id,extractor,schema_version,parse_status,approval,"
                "extracted_at) VALUES (%s,'repo-test',1,'ok','accepted',now()) RETURNING id",
                (document_id,),
            ).fetchone()[0]
            product_id = conn.execute(
                "INSERT INTO core.product(insurer_id,product_code,name) "
                "VALUES (%s,'REPO-T1','Repository Test Product') RETURNING id",
                (insurer_id,),
            ).fetchone()[0]
            version_id = conn.execute(
                "INSERT INTO core.policy_version("
                "confirmed_document_id,product_id,version_label,date_confidence,generation) "
                "VALUES (%s,%s,'v1','exact',1) RETURNING id",
                (document_id, product_id),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO core.clause_content("
                "content_hash,hash_version,title,body,char_length) VALUES "
                "(%s,'v1','repository ground','covered body',12),"
                "(%s,'v1','repository annex','annex body',10)",
                ("2" * 64, "6" * 64),
            )
            clause_id = conn.execute(
                "INSERT INTO core.policy_clause("
                "confirmed_document_id,document_extraction_id,policy_version_id,"
                "source_kind,ordinal,content_hash,citeable,locator) "
                "VALUES (%s,%s,%s,'clause',0,%s,true,'{}') "
                "RETURNING id",
                (document_id, extraction_id, version_id, "2" * 64),
            ).fetchone()[0]
            annex_clause_id = conn.execute(
                "INSERT INTO core.policy_clause("
                "confirmed_document_id,document_extraction_id,policy_version_id,"
                "source_kind,ordinal,content_hash,citeable,locator) "
                "VALUES (%s,%s,%s,'annex',0,%s,true,'{}') RETURNING id",
                (document_id, extraction_id, version_id, "6" * 64),
            ).fetchone()[0]
            kcd_version_id = conn.execute(
                "INSERT INTO core.kcd_version(label) VALUES ('KCD-repo') RETURNING id"
            ).fetchone()[0]
            kcd_code_id = conn.execute(
                "INSERT INTO core.kcd_code(kcd_version_id,code,name_ko) "
                "VALUES (%s,'S72.0','repo test') RETURNING id",
                (kcd_version_id,),
            ).fetchone()[0]
            conn.commit()

        yield {
            "repository": PgInsuranceRepository(runtime_dsn),
            "admin_repository": PgInsuranceAdminRepository(owner_dsn),
            "admin_dsn": target_admin_dsn,
            "owner_dsn": owner_dsn,
            "runtime_dsn": runtime_dsn,
            "runtime_role": role,
            "admin_id": str(admin_id),
            "product_id": str(product_id),
            "version_id": str(version_id),
            "clause_id": str(clause_id),
            "annex_clause_id": str(annex_clause_id),
            "kcd_code_id": str(kcd_code_id),
        }
    finally:
        if database_created:
            assert database.startswith(_DB_PREFIX)
            with psycopg.connect(admin_dsn, autocommit=True) as conn:
                conn.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=%s AND pid<>pg_backend_pid()",
                    (database,),
                )
                conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))
        if role_created:
            assert role.startswith(_ROLE_PREFIX)
            with psycopg.connect(admin_dsn, autocommit=True) as conn:
                conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        if admin_role_created:
            assert admin_role.startswith(_ADMIN_ROLE_PREFIX)
            with psycopg.connect(admin_dsn, autocommit=True) as conn:
                conn.execute(
                    sql.SQL("DROP ROLE {}").format(sql.Identifier(admin_role))
                )


@pytest.mark.pg
def test_subject부터_verified_evidence까지_한_transaction으로_추적된다(
    insurance_repository,
):
    fx = insurance_repository
    repository = fx["repository"]
    policy = repository.resolve_policy_reference(document_sha256="1" * 64)
    kcd = repository.resolve_kcd_reference(code="S72.0", incident_on=date(2025, 1, 1))
    clause = repository.resolve_clause_reference(
        policy_version_id=policy.policy_version_id,
        document_sha256="1" * 64,
        source_kind="clause",
        ordinal=0,
        quote="covered body",
    )
    annex = repository.resolve_clause_reference(
        policy_version_id=policy.policy_version_id,
        document_sha256="1" * 64,
        source_kind="annex",
        ordinal=0,
        quote="annex body",
    )
    assert policy.product_id == fx["product_id"]
    assert kcd.kcd_code_id == fx["kcd_code_id"]
    assert clause.policy_clause_id == fx["clause_id"]
    assert annex.policy_clause_id == fx["annex_clause_id"]

    with repository.transaction() as tx:
        subject_id = tx.create_subject(age_band="30s")
        holding_id = tx.create_policy_holding(
            subject_id=subject_id,
            product_id=policy.product_id,
            policy_version_id=policy.policy_version_id,
            enrolled_on=date(2024, 1, 1),
        )
        review_id = tx.create_coverage_review(
            subject_id=subject_id,
            policy_holding_id=holding_id,
            incident_on=date(2025, 1, 1),
            channel="api",
        )
        diagnosis_id = tx.add_diagnosis(
            review_id=review_id,
            kcd_code_id=kcd.kcd_code_id,
            raw_kcd_code="S72.0",
        )
        assessment_id = tx.create_assessment(
            review_id=review_id,
            policy_version_id=policy.policy_version_id,
            verdict="likely_covered",
            rule_engine_version="repo-test-v1",
        )
        tx.add_assessment_citation(
            assessment_id=assessment_id,
            policy_clause_id=clause.policy_clause_id,
            policy_version_id=clause.policy_version_id,
            role="ground",
            content_hash=clause.content_hash,
            quote="covered body",
            locator=clause.locator,
        )
        claim_id = tx.create_claim(
            review_id=review_id,
            assessment_id=assessment_id,
            claimed_on=date(2025, 1, 2),
            claimed_amount=Decimal("1000"),
        )
        outcome_id = tx.create_outcome(
            claim_id=claim_id,
            decision="approved",
            decided_on=date(2025, 2, 1),
            paid_amount=Decimal("800"),
        )
        evidence_id = tx.create_evidence(
            outcome_id=outcome_id,
            doc_type="decision_notice",
            sha256_hash="3" * 64,
            stored_ref="object://repo-test",
        )
        consistency = tx.record_evidence_consistency(
            evidence_id=evidence_id,
            consistent=True,
            details={"amount_matches": True},
        )
        verification_id = tx.record_evidence_verification(
            evidence_id=evidence_id,
            result="verified",
            verification_method="admin_review",
            verified_by=fx["admin_id"],
        )
        snapshot = tx.get_review(review_id)

    assert consistency["status"] == "consistent"
    assert verification_id
    assert snapshot.diagnosis_ids == (diagnosis_id,)
    assert snapshot.assessment_ids == (assessment_id,)
    with psycopg.connect(fx["admin_dsn"]) as conn:
        assert conn.execute(
            "SELECT n FROM app.cohort_stats WHERE kcd_code_id=%s",
            (fx["kcd_code_id"],),
        ).fetchone()[0] == 1


@pytest.mark.pg
def test_repository_transaction_rollback과_fk_오류_매핑(insurance_repository):
    fx = insurance_repository
    repository = fx["repository"]
    rollback_subject = ""
    with pytest.raises(RuntimeError, match="rollback probe"):
        with repository.transaction() as tx:
            rollback_subject = tx.create_subject(age_band="rollback")
            raise RuntimeError("rollback probe")

    with psycopg.connect(fx["admin_dsn"]) as conn:
        assert conn.execute(
            "SELECT count(*) FROM app.subject WHERE id=%s", (rollback_subject,)
        ).fetchone()[0] == 0

    with pytest.raises(ValidationErr, match="무결성"):
        with repository.transaction() as tx:
            subject_id = tx.create_subject(age_band="invalid-fk")
            tx.create_policy_holding(
                subject_id=subject_id,
                product_id=str(uuid.uuid4()),
                policy_version_id=fx["version_id"],
                enrolled_on=date(2024, 1, 1),
            )


@pytest.mark.pg
def test_ops_agent_consent_interaction_audit_권한과_멱등성(insurance_repository):
    fx = insurance_repository
    admin_repository = fx["admin_repository"]
    repository = fx["repository"]
    agent_id = "repo-agent-001"
    with admin_repository.transaction() as tx:
        registered = tx.register_agent_client(
            agent_client_id=agent_id,
            name="Repository Agent",
            api_key_hash="4" * 64,
            rate_limit_rpm=30,
        )
        duplicate_registration = tx.register_agent_client(
            agent_client_id=agent_id,
            name="Repository Agent",
            api_key_hash="4" * 64,
            rate_limit_rpm=30,
        )
    assert registered == duplicate_registration

    with repository.transaction() as tx:
        assert tx.get_agent_client(agent_id).status == "active"
        subject_id = tx.create_subject(age_band="40s")
        consent1 = tx.grant_consent(
            subject_id=subject_id,
            purpose="cohort-analysis",
            policy_version_id=fx["version_id"],
        )
        consent2 = tx.grant_consent(
            subject_id=subject_id,
            purpose="cohort-analysis",
            policy_version_id=fx["version_id"],
        )
        tx.record_agent_auth_attempt(
            log_id="repo-auth-001",
            agent_client_id=agent_id,
            result="success",
        )
        interaction1 = tx.record_interaction(
            channel="agent-api",
            agent_client_id=agent_id,
            source_event_id="repo-event-001",
            actor_kind="agent",
            question_masked="질병코드 *** 문의",
            answer="확인이 필요합니다.",
            abstained=True,
        )
        interaction2 = tx.record_interaction(
            channel="agent-api",
            agent_client_id=agent_id,
            source_event_id="repo-event-001",
            actor_kind="agent",
            question_masked="질병코드 *** 문의",
            answer="확인이 필요합니다.",
            abstained=True,
        )
        anonymous1 = tx.record_interaction(
            channel="web",
            source_event_id="repo-anonymous-001",
            actor_kind="human",
            abstained=False,
        )
        anonymous2 = tx.record_interaction(
            channel="web",
            source_event_id="repo-anonymous-001",
            actor_kind="human",
            abstained=False,
        )
        audit_id = tx.record_audit(
            actor_id=fx["admin_id"],
            actor_type="admin",
            action="consent.grant",
            target_table="ops.consent",
            target_id=consent1,
            after={"purpose": "cohort-analysis"},
        )
        revoked1 = tx.revoke_consent(consent_id=consent1)
        revoked2 = tx.revoke_consent(consent_id=consent1)

    assert consent1 == consent2 == revoked1 == revoked2
    assert interaction1.interaction_id == interaction2.interaction_id
    assert interaction2.duplicate is True
    assert anonymous1.interaction_id == anonymous2.interaction_id
    assert anonymous2.duplicate is True
    assert audit_id > 0

    with pytest.raises(ConflictErr, match="다른 interaction payload"):
        with repository.transaction() as tx:
            tx.record_interaction(
                channel="agent-api",
                agent_client_id=agent_id,
                source_event_id="repo-event-001",
                actor_kind="agent",
                question_masked="different",
                answer="different",
                abstained=False,
            )

    with admin_repository.transaction() as tx:
        tx.rotate_agent_key_hash(agent_client_id=agent_id, api_key_hash="5" * 64)
        tx.disable_agent_client(agent_client_id=agent_id)
        assert tx.get_agent_client(agent_id).status == "disabled"

    with psycopg.connect(fx["admin_dsn"]) as conn:
        assert conn.execute(
            "SELECT has_schema_privilege(%s,'core','CREATE') OR "
            "has_schema_privilege(%s,'app','CREATE') OR "
            "has_schema_privilege(%s,'ops','CREATE')",
            (fx["runtime_role"], fx["runtime_role"], fx["runtime_role"]),
        ).fetchone()[0] is False
        assert conn.execute(
            "SELECT has_table_privilege(%s,'ops.consent','INSERT') OR "
            "has_table_privilege(%s,'ops.audit_log','UPDATE')",
            (fx["runtime_role"], fx["runtime_role"]),
        ).fetchone()[0] is False


def _persist_command(*, key: str, outcome: PrecheckOutcome, payload=None):
    request = payload or {
        "insurer": "Repo Test",
        "enrolled_on": "20240101",
        "incident_on": "20250101",
        "kcd_codes": ["S72.0"],
    }
    response = {
        "verdict": outcome.verdict.value,
        "abstained": outcome.abstained,
        "trace_id": outcome.trace_id,
    }
    return PersistPrecheckCommand(
        outcome=outcome,
        enrolled_on=date(2024, 1, 1),
        incident_on=date(2025, 1, 1),
        kcd_codes=("S72.0",),
        channel="public-api",
        idempotency_key=key,
        idempotency_secret="repo-test-secret-32-bytes-minimum-value",
        request_snapshot=request,
        response_snapshot=response,
    )


@pytest.mark.pg
def test_precheck_persistence는_uuid를_해소하고_원응답을_멱등재생한다(
    insurance_repository,
):
    fx = insurance_repository
    outcome = PrecheckOutcome(
        verdict=Verdict.LIKELY_COVERED,
        applied_policy=AppliedPolicyInfo(
            insurer="Repo Test",
            product_name="Repository Test Product",
            sale_start="20240101",
            sha256="1" * 64,
        ),
        citations=[
            CitationRef(
                clause_id="repo-clause",
                qualified_no="1",
                quote="covered body",
                occurrence_id=f"repo-release:{'1' * 64}:clause:0",
                tier=EvidenceTier.POLICY_CLAUSE,
            )
        ],
        rule_engine_version="repo-test-v1",
        trace_id="repo-trace-001",
    )
    command = _persist_command(key="repo-key-0001", outcome=outcome)
    first = persist(command, repository=fx["repository"])
    replay = persist(command, repository=fx["repository"])
    assert first.duplicate is False
    assert replay.duplicate is True
    assert replay.review_id == first.review_id
    assert replay.response_snapshot == first.response_snapshot

    with psycopg.connect(fx["admin_dsn"]) as conn:
        row = conn.execute(
            "SELECT count(*),count(DISTINCT cr.id),count(DISTINCT a.id),"
            "count(DISTINCT ac.policy_clause_id) "
            "FROM app.coverage_review cr "
            "JOIN app.assessment a ON a.case_id=cr.id "
            "JOIN app.assessment_clause_citation ac ON ac.assessment_id=a.id "
            "WHERE cr.id=%s",
            (first.review_id,),
        ).fetchone()
    assert row == (1, 1, 1, 1)

    changed = _persist_command(
        key="repo-key-0001",
        outcome=outcome,
        payload={**command.request_snapshot, "incident_on": "20250102"},
    )
    with pytest.raises(ConflictErr, match="다른 payload"):
        persist(changed, repository=fx["repository"])


@pytest.mark.pg
def test_policy를_못_정한_기권도_null_fk로_추적한다(insurance_repository):
    fx = insurance_repository
    outcome = PrecheckOutcome(
        verdict=Verdict.NEEDS_EXPERT,
        abstained=True,
        reason_code=ReasonCode.DOCUMENTS_NOT_CONFIRMED,
        message="확정된 약관이 없습니다.",
        rule_engine_version="repo-test-v1",
        trace_id="repo-trace-abstain",
    )
    saved = persist(
        _persist_command(key="repo-key-abstain", outcome=outcome),
        repository=fx["repository"],
    )
    with psycopg.connect(fx["admin_dsn"]) as conn:
        row = conn.execute(
            "SELECT cr.policy_holding_id,a.policy_version_id,a.abstained,"
            "a.abstain_reason,d.raw_kcd_code,d.kcd_code_id IS NOT NULL "
            "FROM app.coverage_review cr "
            "JOIN app.assessment a ON a.case_id=cr.id "
            "JOIN app.case_diagnosis d ON d.case_id=cr.id WHERE cr.id=%s",
            (saved.review_id,),
        ).fetchone()
    assert row == (None, None, True, "documents_not_confirmed", "S72.0", True)


@pytest.mark.pg
def test_public_precheck_api는_postgres에_원자저장하고_멱등재생한다(
    insurance_repository,
    monkeypatch,
):
    fx = insurance_repository
    outcome = PrecheckOutcome(
        verdict=Verdict.LIKELY_COVERED,
        applied_policy=AppliedPolicyInfo(
            insurer="Repo Test",
            product_name="Repository Test Product",
            sale_start="20240101",
            sale_end="20251231",
            sha256="1" * 64,
        ),
        citations=[
            CitationRef(
                clause_id="repo-clause",
                qualified_no="1",
                quote="covered body",
                occurrence_id=f"repo-release:{'1' * 64}:clause:0",
                tier=EvidenceTier.POLICY_CLAUSE,
            )
        ],
        rule_engine_version="repo-api-v1",
        trace_id="repo-api-trace-001",
    )

    class StaticGraph:
        def invoke(self, _input):
            return outcome, {"status": "done"}

    from app.core.config import get_settings
    from app.main import create_app
    from app.obs import agent_stream
    from app.routers import precheck as precheck_router

    monkeypatch.setenv("PRECHECK_PERSISTENCE", "postgres")
    monkeypatch.setenv("INSURANCE_PG_DSN", fx["runtime_dsn"])
    monkeypatch.setenv(
        "INSURANCE_IDEMPOTENCY_SECRET",
        "repo-api-idempotency-secret-32-bytes-minimum-value",
    )
    get_settings.cache_clear()
    monkeypatch.setattr(precheck_router, "_GRAPH", StaticGraph())
    monkeypatch.setattr(
        precheck_router,
        "_confirmation_stats",
        lambda: {"confirmed": 1, "collected": 1},
    )
    monkeypatch.setattr(agent_stream, "publish", lambda *_args, **_kwargs: None)

    request_body = {
        "insurer": "Repo Test",
        "product_name": "Repository Test Product",
        "enrolled_on": "20240101",
        "incident_on": "20250101",
        "kcd_codes": ["S72.0"],
    }
    headers = {"Idempotency-Key": "repo-api-key-0001"}
    try:
        with TestClient(create_app()) as client:
            first = client.post("/v1/prechecks", json=request_body, headers=headers)
            replay = client.post("/v1/prechecks", json=request_body, headers=headers)
            conflict = client.post(
                "/v1/prechecks",
                json={**request_body, "incident_on": "20250102"},
                headers=headers,
            )
    finally:
        get_settings.cache_clear()

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "conflict"
    with psycopg.connect(fx["admin_dsn"]) as conn:
        assert conn.execute("SELECT count(*) FROM app.coverage_review").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM app.assessment").fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM app.assessment_clause_citation"
        ).fetchone()[0] == 1


@pytest.mark.pg
def test_observation검수cohort가_postgres_api로_끝까지_연결된다(
    insurance_repository,
    monkeypatch,
):
    fx = insurance_repository
    outcome = PrecheckOutcome(
        verdict=Verdict.LIKELY_COVERED,
        applied_policy=AppliedPolicyInfo(
            insurer="Repo Test",
            product_name="Repository Test Product",
            sale_start="20240101",
            sale_end="20251231",
            sha256="1" * 64,
        ),
        citations=[
            CitationRef(
                clause_id="repo-clause",
                qualified_no="1",
                quote="covered body",
                occurrence_id=f"repo-release:{'1' * 64}:clause:0",
                tier=EvidenceTier.POLICY_CLAUSE,
            )
        ],
        rule_engine_version="repo-api-v1",
        trace_id="repo-s4-trace-001",
    )

    class StaticGraph:
        def invoke(self, _input):
            return outcome, {"status": "done"}

    from app.auth.roles import require_admin
    from app.core.config import get_settings
    from app.main import create_app
    from app.obs import agent_stream
    from app.routers import precheck as precheck_router

    monkeypatch.setenv("PRECHECK_PERSISTENCE", "postgres")
    monkeypatch.setenv("OUTCOME_PERSISTENCE", "postgres")
    monkeypatch.setenv("VERIFIED_COHORT_STORE", "postgres")
    monkeypatch.setenv("INSURANCE_PG_DSN", fx["runtime_dsn"])
    # The fixture's owner-member login, not the cluster superuser, is the application admin DSN.
    monkeypatch.setenv("INSURANCE_ADMIN_PG_DSN", fx["owner_dsn"])
    monkeypatch.setenv(
        "INSURANCE_IDEMPOTENCY_SECRET",
        "repo-s4-idempotency-secret-32-bytes-minimum-value",
    )
    get_settings.cache_clear()
    monkeypatch.setattr(precheck_router, "_GRAPH", StaticGraph())
    monkeypatch.setattr(
        precheck_router,
        "_confirmation_stats",
        lambda: {"confirmed": 1, "collected": 1},
    )
    monkeypatch.setattr(agent_stream, "publish", lambda *_args, **_kwargs: None)

    app = create_app()
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
        username="repo-reviewer"
    )
    precheck_body = {
        "insurer": "Repo Test",
        "product_name": "Repository Test Product",
        "enrolled_on": "20240101",
        "incident_on": "20250101",
        "kcd_codes": ["S72.0"],
    }
    observation_body = {
        "client_ref": "public-test-client",
        "insurer": "Repo Test",
        "enrolled_on": "20240101",
        "kcd_codes": ["S72.0"],
        "outcome": "paid",
        "outcome_reason": "approved after document review",
        "precheck_trace_id": "repo-s4-trace-001",
        "precheck_idempotency_key": "repo-s4-precheck-key",
        "claimed_on": "20250102",
        "decided_on": "20250201",
        "claimed_amount": "1000",
        "paid_amount": "800",
        "evidence_doc_type": "decision_notice",
        "evidence_sha256": "3" * 64,
        "evidence_stored_ref": "object://repo-s4/decision-notice",
    }
    try:
        with TestClient(app) as client:
            precheck_response = client.post(
                "/v1/prechecks",
                json=precheck_body,
                headers={"Idempotency-Key": "repo-s4-precheck-key"},
            )
            unbound = client.post(
                "/v1/observations",
                json={
                    **observation_body,
                    "precheck_idempotency_key": "wrong-precheck-key",
                },
                headers={"Idempotency-Key": "repo-s4-unbound-key"},
            )
            first = client.post(
                "/v1/observations",
                json=observation_body,
                headers={"Idempotency-Key": "repo-s4-outcome-key"},
            )
            replay = client.post(
                "/v1/observations",
                json=observation_body,
                headers={"Idempotency-Key": "repo-s4-outcome-key"},
            )
            outcome_conflict = client.post(
                "/v1/observations",
                json={**observation_body, "paid_amount": "700"},
                headers={"Idempotency-Key": "repo-s4-outcome-key"},
            )
            before = client.get("/v1/cohorts?code=S72.0")
            verified = client.post(
                "/api/admin/verifications",
                json={
                    "submission_id": first.json()["submission_id"],
                    "basis": "decision notice hash and amount reviewed",
                },
            )
            after = client.get("/v1/cohorts?code=S72.0")
            verify_replay = client.post(
                "/api/admin/verifications",
                json={
                    "submission_id": first.json()["submission_id"],
                    "basis": "decision notice hash and amount reviewed",
                },
            )
            verify_conflict = client.post(
                "/api/admin/verifications",
                json={
                    "submission_id": first.json()["submission_id"],
                    "basis": "different review basis",
                },
            )
    finally:
        get_settings.cache_clear()

    assert precheck_response.status_code == 200
    assert unbound.status_code == 404
    assert first.status_code == replay.status_code == 202
    assert first.json()["stored"] is True
    assert replay.json()["duplicate"] is True
    assert replay.json()["submission_id"] == first.json()["submission_id"]
    assert outcome_conflict.status_code == 409
    assert before.status_code == 200 and before.json()["n"] == 0
    assert verified.status_code == verify_replay.status_code == 201
    assert verified.json()["event"]["verification_method"] == "admin_attested"
    assert verify_replay.json()["event"]["duplicate"] is True
    assert verify_conflict.status_code == 409
    assert after.status_code == 200
    assert after.json()["n"] == 1
    assert after.json()["approved_n"] == 1
    assert after.json()["by_verification"] == {"admin_attested": 1}

    with psycopg.connect(fx["admin_dsn"]) as conn:
        assert conn.execute("SELECT count(*) FROM app.claim").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM app.outcome").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM app.evidence").fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM app.evidence_verification"
        ).fetchone()[0] == 1


@pytest.mark.pg
def test_agent_client_mirror_uses_source_hash_and_syncs_runtime_fk(insurance_repository):
    fx = insurance_repository
    source = PgAgentAccess(_AGENT_ADMIN_DSN)
    client_id = f"mirror-{uuid.uuid4().hex[:12]}"
    raw_key = generate_api_key(client_id)
    rotated_key = generate_api_key(client_id)
    source.create_client(
        client_id=client_id,
        display_name="Mirror integration",
        raw_key=raw_key,
        scopes={"precheck:read"},
        rate_limit_per_minute=17,
    )
    try:
        sync = SyncAgentClients(source, fx["admin_repository"])
        dry = sync.run()
        assert client_id in dry.missing_in_real

        applied = sync.run(apply=True)
        assert client_id not in applied.missing_in_real
        with fx["repository"].transaction() as tx:
            mirrored = tx.get_agent_client(client_id)
        assert mirrored.api_key_hash == hash_api_key(raw_key)
        assert mirrored.rate_limit_rpm == 17

        source.rotate_client_key(client_id=client_id, raw_key=rotated_key)
        assert client_id in sync.run().differing
        sync.run(apply=True)
        with fx["repository"].transaction() as tx:
            assert tx.get_agent_client(client_id).api_key_hash == hash_api_key(rotated_key)

        source.disable_client(client_id=client_id)
        sync.run(apply=True)
        with fx["repository"].transaction() as tx:
            assert tx.get_agent_client(client_id).status == "disabled"
    finally:
        with psycopg.connect(_AGENT_SUPER_DSN) as conn:
            conn.execute("DELETE FROM ops.agent_client WHERE client_id=%s", (client_id,))


@pytest.mark.pg
def test_registered_subject_hash_reuses_subject_and_grants_consent(insurance_repository):
    fx = insurance_repository
    subject_hash = "a" * 64
    with fx["repository"].transaction() as tx:
        first_subject = tx.create_subject(subject_ref_hash=subject_hash)
        first_consent = tx.grant_consent(
            subject_id=first_subject,
            purpose="insurance.precheck",
        )

    with fx["repository"].transaction() as tx:
        second_subject = tx.create_subject(subject_ref_hash=subject_hash)
        second_consent = tx.grant_consent(
            subject_id=second_subject,
            purpose="insurance.precheck",
        )

    assert second_subject == first_subject
    assert second_consent == first_consent
    with psycopg.connect(fx["admin_dsn"]) as conn:
        assert conn.execute(
            "SELECT count(*) FROM app.subject WHERE subject_ref_hash=%s",
            (subject_hash,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM ops.consent WHERE subject_id=%s AND purpose=%s",
            (first_subject, "insurance.precheck"),
        ).fetchone()[0] == 1


@pytest.mark.pg
def test_registered_agent_precheck_outcome_and_ops_logs_are_postgres_atomic(
    insurance_repository, monkeypatch
):
    fx = insurance_repository
    agent_id = f"e2e-agent-{uuid.uuid4().hex[:10]}"
    subject_hash = "c" * 64
    secret = "registered-agent-e2e-secret-32-bytes-min"
    with fx["admin_repository"].transaction() as tx:
        tx.register_agent_client(
            agent_client_id=agent_id,
            name="Registered E2E",
            api_key_hash="d" * 64,
            rate_limit_rpm=60,
        )

    outcome = PrecheckOutcome(
        verdict=Verdict.NEEDS_EXPERT,
        abstained=True,
        reason_code=ReasonCode.NO_EVIDENCE,
        rule_engine_version="registered-e2e",
        trace_id="registered-e2e-trace",
    )
    precheck = persist(
        PersistPrecheckCommand(
            outcome=outcome,
            enrolled_on=date(2024, 1, 1),
            incident_on=date(2025, 1, 1),
            kcd_codes=(),
            channel="registered-agent",
            agent_client_id=agent_id,
            subject_ref_hash=subject_hash,
            consent_purpose="insurance.precheck",
            idempotency_key="registered-precheck-1",
            idempotency_secret=secret,
            request_snapshot={"insurer": "Repo Test", "kcd_codes": []},
            response_snapshot={
                "verdict": outcome.verdict.value,
                "abstained": True,
                "trace_id": outcome.trace_id,
            },
        ),
        repository=fx["repository"],
    )

    monkeypatch.setenv("INSURANCE_PG_DSN", fx["runtime_dsn"])
    monkeypatch.setenv("INSURANCE_IDEMPOTENCY_SECRET", secret)
    monkeypatch.setenv("OUTCOME_PERSISTENCE", "postgres")
    monkeypatch.setenv("AGENT_REAL_LEDGER_ENABLED", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        receipt = AgentFacade().submit_observation(
            AgentObservationRequest(
                insurer="Repo Test",
                enrolled_on="20240101",
                kcd_codes=["S72.0"],
                product_id="",
                outcome="paid",
                outcome_reason="e2e paid",
                precheck_trace_id="registered-e2e-trace",
                precheck_idempotency_key="registered-precheck-1",
                claimed_on="20250102",
                decided_on="20250103",
                claimed_amount=100,
                paid_amount=100,
                evidence_doc_type="receipt",
                evidence_sha256="e" * 64,
                evidence_stored_ref="object://registered-e2e/receipt",
            ),
            client_id=agent_id,
            idempotency_key="registered-outcome-1",
        )
    finally:
        get_settings.cache_clear()

    assert receipt.stored is True
    assert receipt.duplicate is False
    with psycopg.connect(fx["admin_dsn"]) as conn:
        assert conn.execute(
            "SELECT count(*) FROM app.coverage_review WHERE id=%s",
            (precheck.review_id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM app.claim WHERE submission_id=%s",
            (receipt.submission_id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM app.evidence WHERE submission_id=%s",
            (receipt.submission_id,),
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT count(*) FROM ops.interaction_log WHERE agent_client_id=%s",
            (agent_id,),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT count(*) FROM ops.audit_log "
            "WHERE actor_type='agent' AND after->>'agent_client_id'=%s",
            (agent_id,),
        ).fetchone()[0] == 2


@pytest.mark.pg
def test_insurance_postgres_readiness_reports_migration_011(insurance_repository):
    result = insurance_repository["repository"].readiness()
    assert result["ready"] is True
    assert result["migration_latest"] == "016_runtime_ownership_and_grants.sql"
    assert result["missing_tables"] == []
