# -*- coding: utf-8 -*-
"""Fresh PostgreSQL DB에서 core/app/ops migration과 핵심 불변식을 검증한다.

기존 DB는 사용하지 않는다. ``insurance_schema_verify_<uuid>`` DB를 생성하고
검증 후 즉시 삭제한다. 관리자 DSN은 ``--admin-dsn`` 또는 ``PG_DSN``으로 받는다.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from scripts.db import apply as migration_apply

_PREFIX = "insurance_schema_verify_"
_ROLE_PREFIX = "insurance_migrator_verify_"


def _expect_error(conn: psycopg.Connection, statement: str, params=()) -> str:
    conn.execute("SAVEPOINT expected_failure")
    try:
        conn.execute(statement, params)
    except psycopg.Error as exc:
        code = exc.sqlstate or "unknown"
        conn.execute("ROLLBACK TO SAVEPOINT expected_failure")
        conn.execute("RELEASE SAVEPOINT expected_failure")
        return code
    conn.execute("RELEASE SAVEPOINT expected_failure")
    raise AssertionError("무결성 위반 SQL이 성공했다")


def _apply_code(target_dsn: str) -> int:
    old_argv = sys.argv[:]
    old_dsn = os.environ.get("PG_DSN")
    try:
        os.environ["PG_DSN"] = target_dsn
        sys.argv = ["apply", "--track", "core"]
        return migration_apply.main()
    finally:
        sys.argv = old_argv
        if old_dsn is None:
            os.environ.pop("PG_DSN", None)
        else:
            os.environ["PG_DSN"] = old_dsn


def _apply(target_dsn: str) -> None:
    if _apply_code(target_dsn) != 0:
        raise RuntimeError("migration apply 실패")


def _verify_migration_safety(target_dsn: str) -> dict[str, object]:
    assert _apply_code(target_dsn) == 0

    migration = migration_apply.HERE / "005_integrity_and_privileges.sql"
    ledger_name = migration.name
    expected_checksum = migration_apply._sha(migration.read_text(encoding="utf-8"))
    with psycopg.connect(target_dsn) as conn:
        conn.execute(
            "UPDATE public.schema_migration SET checksum=%s WHERE filename=%s",
            ("0" * 64, ledger_name),
        )
        conn.commit()
    try:
        checksum_code = _apply_code(target_dsn)
        assert checksum_code == 1
    finally:
        with psycopg.connect(target_dsn) as conn:
            conn.execute(
                "UPDATE public.schema_migration SET checksum=%s WHERE filename=%s",
                (expected_checksum, ledger_name),
            )
            conn.commit()

    _migration_dir, lock_key = migration_apply.TRACKS["core"]
    with psycopg.connect(target_dsn) as lock_conn:
        lock_conn.execute("SELECT pg_advisory_lock(%s)", (lock_key,))
        lock_conn.commit()
        try:
            lock_code = _apply_code(target_dsn)
            assert lock_code == 1
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
            lock_conn.commit()

    probe_name = "verification/999_partial.sql"
    partial_sqlstate = ""
    with psycopg.connect(target_dsn) as conn:
        cur = conn.cursor()
        try:
            migration_apply._apply_one(
                cur,
                "CREATE TABLE public.migration_rollback_probe(id integer); SELECT 1/0;",
                probe_name,
                "f" * 64,
            )
            conn.commit()
        except psycopg.Error as exc:
            partial_sqlstate = exc.sqlstate or "unknown"
            conn.rollback()
        else:
            raise AssertionError("부분 적용 실패 probe가 성공했다")

        assert conn.execute(
            "SELECT to_regclass('public.migration_rollback_probe')"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT count(*) FROM public.schema_migration WHERE filename=%s",
            (probe_name,),
        ).fetchone()[0] == 0

    return {
        "reapply": "skip",
        "checksum_conflict": checksum_code,
        "advisory_lock": lock_code,
        "partial_rollback": partial_sqlstate,
    }


def _drop_database(admin_dsn: str, database: str, prefix: str) -> None:
    if not database.startswith(prefix):
        raise RuntimeError(f"임시 DB 이름 안전검사 실패: {database}")
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname=%s AND pid<>pg_backend_pid()",
            (database,),
        )
        conn.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))


def _verify_non_superuser_migration(
    admin_dsn: str, base_conninfo: dict[str, str]
) -> dict[str, object]:
    database = _PREFIX + "role_" + uuid.uuid4().hex[:12]
    role = _ROLE_PREFIX + uuid.uuid4().hex[:12]
    password = uuid.uuid4().hex + uuid.uuid4().hex
    role_created = False
    database_created = False
    admin_target_dsn = make_conninfo(**{**base_conninfo, "dbname": database})
    migrator_dsn = make_conninfo(
        **{
            **base_conninfo,
            "dbname": database,
            "user": role,
            "password": password,
        }
    )
    expected_migrations = len(list(migration_apply.TRACKS["core"][0].glob("*.sql")))

    try:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            assert conn.execute(
                "SELECT rolsuper FROM pg_roles WHERE rolname=current_user"
            ).fetchone()[0], "임시 non-superuser role 검증에는 superuser 관리자 DSN이 필요하다"
            conn.execute(
                sql.SQL("CREATE ROLE {} LOGIN INHERIT PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
            role_created = True
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
            database_created = True

        with psycopg.connect(admin_target_dsn, autocommit=True) as conn:
            conn.execute("CREATE EXTENSION vector")
            conn.execute("CREATE EXTENSION pg_trgm")
            conn.execute("CREATE EXTENSION pgcrypto")

        membership_rejected = False
        try:
            _apply(migrator_dsn)
        except RuntimeError as exc:
            membership_rejected = "insurance_owner" in str(exc)
        assert membership_rejected
        with psycopg.connect(migrator_dsn) as conn:
            assert conn.execute(
                "SELECT to_regclass('public.schema_migration')"
            ).fetchone()[0] is None
            assert conn.execute(
                "SELECT count(*) FROM pg_namespace "
                "WHERE nspname IN ('core','app','ops')"
            ).fetchone()[0] == 0

        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(
                sql.SQL("GRANT insurance_owner TO {}").format(sql.Identifier(role))
            )

        _apply(migrator_dsn)
        with psycopg.connect(migrator_dsn) as conn:
            identity = conn.execute(
                "SELECT current_user, "
                "(SELECT rolsuper FROM pg_roles WHERE rolname=current_user)"
            ).fetchone()
            assert identity == (role, False)
            assert conn.execute(
                "SELECT bool_and(applied_by=%s), count(*) "
                "FROM public.schema_migration WHERE filename ~ '^[0-9]{3}_'",
                (role,),
            ).fetchone() == (True, expected_migrations)
            assert conn.execute(
                "SELECT count(*) FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_roles r ON r.oid=c.relowner "
                "WHERE n.nspname IN ('app','core','ops') "
                "AND c.relkind IN ('r','v') AND r.rolname<>'insurance_owner'"
            ).fetchone()[0] == 0

        return {
            "membership_preflight": "rejected_before_ddl",
            "migration_user": "non-superuser",
            "applied": expected_migrations,
        }
    finally:
        if database_created:
            _drop_database(admin_dsn, database, _PREFIX)
        if role_created:
            if not role.startswith(_ROLE_PREFIX):
                raise RuntimeError(f"임시 role 이름 안전검사 실패: {role}")
            with psycopg.connect(admin_dsn, autocommit=True) as conn:
                conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def _seed_and_verify(conn: psycopg.Connection) -> dict[str, object]:
    admin = conn.execute(
        "INSERT INTO ops.admin_user(login, role) "
        "VALUES ('schema-test-admin','reviewer') RETURNING id"
    ).fetchone()[0]
    insurer = conn.execute(
        "INSERT INTO core.insurer(slug,legal_name,display_name,kind) "
        "VALUES ('schema-test','Schema Test Insurance','Schema Test','general') "
        "RETURNING id"
    ).fetchone()[0]
    document = conn.execute(
        "INSERT INTO core.confirmed_policy_document("
        "sha256,source_url,fetched_at,insurer_id,identified_by,identified_at) "
        "VALUES (%s,'https://example.invalid/policy.pdf',now(),%s,%s,now()) "
        "RETURNING id",
        ("a" * 64, insurer, admin),
    ).fetchone()[0]
    extraction = conn.execute(
        "INSERT INTO core.document_extraction("
        "confirmed_document_id,extractor,schema_version,parse_status,extracted_at) "
        "VALUES (%s,'schema-test',1,'ok',now()) RETURNING id",
        (document,),
    ).fetchone()[0]
    product = conn.execute(
        "INSERT INTO core.product(insurer_id,product_code,name) "
        "VALUES (%s,'SCHEMA-T1','Schema Test Product') RETURNING id",
        (insurer,),
    ).fetchone()[0]
    version1 = conn.execute(
        "INSERT INTO core.policy_version("
        "confirmed_document_id,product_id,version_label,date_confidence,generation) "
        "VALUES (%s,%s,'v1','exact',1) RETURNING id",
        (document, product),
    ).fetchone()[0]
    version2 = conn.execute(
        "INSERT INTO core.policy_version("
        "confirmed_document_id,product_id,version_label,date_confidence,generation) "
        "VALUES (%s,%s,'v2','exact',2) RETURNING id",
        (document, product),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO core.clause_content("
        "content_hash,hash_version,title,body,char_length) VALUES "
        "(%s,'v1','ground','body1',5),(%s,'v1','other','body2',5)",
        ("b" * 64, "c" * 64),
    )
    clause1 = conn.execute(
        "INSERT INTO core.policy_clause("
        "confirmed_document_id,document_extraction_id,policy_version_id,source_kind,"
        "ordinal,content_hash,citeable,locator) "
        "VALUES (%s,%s,%s,'clause',0,%s,true,'{}') "
        "RETURNING id",
        (document, extraction, version1, "b" * 64),
    ).fetchone()[0]
    clause2 = conn.execute(
        "INSERT INTO core.policy_clause("
        "confirmed_document_id,document_extraction_id,policy_version_id,source_kind,"
        "ordinal,content_hash,citeable,locator) "
        "VALUES (%s,%s,%s,'clause',1,%s,true,'{}') "
        "RETURNING id",
        (document, extraction, version2, "c" * 64),
    ).fetchone()[0]
    kcd_version = conn.execute(
        "INSERT INTO core.kcd_version(label) VALUES ('KCD-test') RETURNING id"
    ).fetchone()[0]
    kcd_code = conn.execute(
        "INSERT INTO core.kcd_code(kcd_version_id,code,name_ko) "
        "VALUES (%s,'S72.0','test') RETURNING id",
        (kcd_version,),
    ).fetchone()[0]

    subject = conn.execute(
        "INSERT INTO app.subject(age_band) VALUES ('30s') RETURNING id"
    ).fetchone()[0]
    conn.execute("SET ROLE insurance_app")
    consent1 = conn.execute(
        "SELECT ops.grant_consent(%s,'cohort-analysis',%s)",
        (subject, version1),
    ).fetchone()[0]
    consent2 = conn.execute(
        "SELECT ops.grant_consent(%s,'cohort-analysis',%s)",
        (subject, version1),
    ).fetchone()[0]
    direct_consent = _expect_error(
        conn,
        "INSERT INTO ops.consent(subject_id,purpose,granted_at) "
        "VALUES (%s,'direct-write',now())",
        (subject,),
    )
    conn.execute(
        "INSERT INTO ops.interaction_log("
        "channel,source_event_id,actor_kind,abstained) "
        "VALUES ('web','schema-anonymous-1','human',false)"
    )
    anonymous_duplicate = _expect_error(
        conn,
        "INSERT INTO ops.interaction_log("
        "channel,source_event_id,actor_kind,abstained) "
        "VALUES ('web','schema-anonymous-1','human',false)",
    )
    runtime_schema_create = conn.execute(
        "SELECT has_schema_privilege(current_user,'core','CREATE') OR "
        "has_schema_privilege(current_user,'app','CREATE') OR "
        "has_schema_privilege(current_user,'ops','CREATE')"
    ).fetchone()[0]
    revoked1 = conn.execute(
        "SELECT ops.revoke_consent(%s)", (consent1,)
    ).fetchone()[0]
    revoked2 = conn.execute(
        "SELECT ops.revoke_consent(%s)", (consent1,)
    ).fetchone()[0]
    conn.execute("RESET ROLE")
    assert consent1 == consent2 == revoked1 == revoked2
    assert runtime_schema_create is False

    holding = conn.execute(
        "INSERT INTO app.policy_holding("
        "subject_id,product_id,policy_version_id,enrolled_on) "
        "VALUES (%s,%s,%s,'2024-01-01') RETURNING id",
        (subject, product, version1),
    ).fetchone()[0]
    review1 = conn.execute(
        "INSERT INTO app.coverage_review("
        "subject_id,policy_holding_id,incident_on,channel) "
        "VALUES (%s,%s,'2025-01-01','api') RETURNING id",
        (subject, holding),
    ).fetchone()[0]
    review2 = conn.execute(
        "INSERT INTO app.coverage_review("
        "subject_id,policy_holding_id,incident_on,channel) "
        "VALUES (%s,%s,'2025-01-02','api') RETURNING id",
        (subject, holding),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO app.case_diagnosis(case_id,kcd_code_id,raw_kcd_code) "
        "VALUES (%s,%s,'S72.0')",
        (review1, kcd_code),
    )
    assessment1 = conn.execute(
        "INSERT INTO app.assessment("
        "case_id,policy_version_id,verdict,rule_engine_version) "
        "VALUES (%s,%s,'likely_covered','schema-test-v1') RETURNING id",
        (review1, version1),
    ).fetchone()[0]
    assessment2 = conn.execute(
        "INSERT INTO app.assessment("
        "case_id,policy_version_id,verdict,rule_engine_version) "
        "VALUES (%s,%s,'needs_expert','schema-test-v1') RETURNING id",
        (review2, version1),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO app.assessment_clause_citation("
        "assessment_id,policy_clause_id,citeable,role,content_hash,quote,locator,"
        "policy_version_id) VALUES (%s,%s,true,'ground',%s,'body1','{}',%s)",
        (assessment1, clause1, "b" * 64, version1),
    )

    cross_version = _expect_error(
        conn,
        "INSERT INTO app.assessment_clause_citation("
        "assessment_id,policy_clause_id,citeable,role,content_hash,quote,locator,"
        "policy_version_id) VALUES (%s,%s,true,'ground',%s,'body2','{}',%s)",
        (assessment1, clause2, "c" * 64, version2),
    )
    wrong_review = _expect_error(
        conn,
        "INSERT INTO app.claim(case_id,assessment_id,claimed_on,claimed_amount) "
        "VALUES (%s,%s,'2025-01-03',1000)",
        (review1, assessment2),
    )

    claim = conn.execute(
        "INSERT INTO app.claim(case_id,assessment_id,claimed_on,claimed_amount) "
        "VALUES (%s,%s,'2025-01-03',1000) RETURNING id",
        (review1, assessment1),
    ).fetchone()[0]
    outcome = conn.execute(
        "INSERT INTO app.outcome(claim_id,decision,paid_amount,decided_on) "
        "VALUES (%s,'approved',800,'2025-02-01') RETURNING id",
        (claim,),
    ).fetchone()[0]
    evidence = conn.execute(
        "INSERT INTO app.evidence(outcome_id,doc_type,sha256_hash,stored_ref) "
        "VALUES (%s,'decision_notice',%s,'object://schema-test') RETURNING id",
        (outcome, "d" * 64),
    ).fetchone()[0]

    baseline = conn.execute(
        "SELECT coalesce(sum(n),0) FROM app.cohort_stats"
    ).fetchone()[0]
    verify_too_early = _expect_error(
        conn,
        "SELECT app.record_evidence_verification("
        "%s,'verified','admin_review',%s,NULL)",
        (evidence, admin),
    )

    conn.execute("SET ROLE insurance_app")
    consistency = conn.execute(
        "SELECT app.record_evidence_consistency(%s,true,%s::jsonb)",
        (evidence, '{"amount_matches":true}'),
    ).fetchone()[0]
    conn.execute("RESET ROLE")
    after_consistency = conn.execute(
        "SELECT coalesce(sum(n),0) FROM app.cohort_stats"
    ).fetchone()[0]

    conn.execute("SET ROLE insurance_app")
    verification1 = conn.execute(
        "SELECT app.record_evidence_verification("
        "%s,'verified','admin_review',%s,'schema test')",
        (evidence, admin),
    ).fetchone()[0]
    verification2 = conn.execute(
        "SELECT app.record_evidence_verification("
        "%s,'verified','admin_review',%s,'schema test')",
        (evidence, admin),
    ).fetchone()[0]
    direct_mutation = _expect_error(
        conn,
        "UPDATE app.evidence_verification SET reason='tamper' WHERE id=%s",
        (verification1,),
    )
    conn.execute("RESET ROLE")

    after_verification = conn.execute(
        "SELECT coalesce(sum(n),0) FROM app.cohort_stats"
    ).fetchone()[0]
    assert (int(baseline), int(after_consistency), int(after_verification)) == (0, 0, 1)
    assert verification1 == verification2
    assert consistency["status"] == "consistent"

    noncompliant_owners = conn.execute(
        "SELECT n.nspname,c.relname,r.rolname FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_roles r ON r.oid=c.relowner "
        "WHERE n.nspname IN ('app','core','ops') "
        "AND c.relkind IN ('r','v') AND r.rolname<>'insurance_owner'"
    ).fetchall()
    assert noncompliant_owners == []
    assert conn.execute(
        "SELECT to_regclass('app.coverage_review') IS NOT NULL, "
        "to_regclass('app.\"case\"') IS NULL"
    ).fetchone() == (True, True)
    assert conn.execute(
        "SELECT has_table_privilege("
        "'insurance_app','app.evidence_verification','UPDATE')"
    ).fetchone()[0] is False

    return {
        "cross_version": cross_version,
        "wrong_review": wrong_review,
        "verify_too_early": verify_too_early,
        "direct_mutation": direct_mutation,
        "cohort": [int(baseline), int(after_consistency), int(after_verification)],
        "verification_idempotent": verification1 == verification2,
        "consent_idempotent": consent1 == consent2 == revoked1 == revoked2,
        "direct_consent": direct_consent,
        "anonymous_interaction_duplicate": anonymous_duplicate,
        "runtime_schema_create": runtime_schema_create,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-dsn", default=os.environ.get("PG_DSN", ""))
    args = parser.parse_args(argv)
    if not args.admin_dsn:
        parser.error("--admin-dsn 또는 PG_DSN이 필요합니다")

    base = conninfo_to_dict(args.admin_dsn)
    database = _PREFIX + uuid.uuid4().hex[:12]
    admin_dsn = make_conninfo(**{**base, "dbname": "postgres"})
    target_dsn = make_conninfo(**{**base, "dbname": database})
    created = False
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            if conn.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (database,)
            ).fetchone():
                raise RuntimeError(f"임시 DB가 이미 존재합니다: {database}")
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
            created = True

        _apply(target_dsn)
        migration_safety = _verify_migration_safety(target_dsn)
        with psycopg.connect(target_dsn) as conn:
            result = _seed_and_verify(conn)
            conn.commit()
        non_superuser = _verify_non_superuser_migration(admin_dsn, base)
        result["migration_safety"] = migration_safety
        result["non_superuser"] = non_superuser
        print(f"[verify-insurance-schema] PASS {result}")
        return 0
    finally:
        if created:
            _drop_database(admin_dsn, database, _PREFIX)
            print(f"[verify-insurance-schema] dropped temporary DB {database}")


if __name__ == "__main__":
    raise SystemExit(main())
