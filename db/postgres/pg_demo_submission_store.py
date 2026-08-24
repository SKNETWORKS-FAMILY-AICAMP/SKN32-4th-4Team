"""PostgreSQL 합성 제출·검증 저장소.

별도 ``insurance_demo`` DB의 ``demo`` 스키마만 사용한다. 연결이나 스키마가
준비되지 않았을 때 파일 저장소로 폴백하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from app.core.domain.insurance import CohortStats, DataSource, KcdCode
from app.core.domain.synthetic_validation import GateDecision
from app.core.errors import ConflictErr, InfraError, ValidationErr
from app.core.usecases.cohort import DEFAULT_MIN_SAMPLE


@dataclass(frozen=True)
class PgStoreResult:
    stored: bool
    submission_id: str
    duplicate: bool = False
    promoted: bool = False
    verification: str = "unverified"
    reason_codes: tuple[str, ...] = ()
    rule_version: str = ""
    path: str = "postgresql:demo.submission"


def _dsn() -> str:
    from app.core.config import get_settings

    return get_settings().DEMO_PG_DSN


def _connect():
    import psycopg

    try:
        conn = psycopg.connect(_dsn(), connect_timeout=5)
        conn.execute("SET statement_timeout = '10s'")
        conn.execute("SET lock_timeout = '3s'")
        return conn
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"합성 PostgreSQL에 연결할 수 없습니다: {e}") from e


def _canonical_hash(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def canonical_hash(payload: dict) -> str:
    """파일 이관 reconcile과 런타임이 같은 payload 지문 규칙을 쓴다."""
    return _canonical_hash(payload)


def _submission_id(client_ref: str, idempotency_key: str) -> str:
    return hashlib.sha256(f"{client_ref}:{idempotency_key}".encode()).hexdigest()[:32]


def readiness() -> dict:
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT to_regclass('demo.submission'), "
                "to_regclass('demo.verification_event')"
            ).fetchone()
        ready = bool(row and row[0] and row[1])
        return {"backend": "postgres", "ready": ready, "schema": "demo"}
    except InfraError as e:
        return {"backend": "postgres", "ready": False, "reason": str(e)[:200]}


def store(
    payload: dict,
    *,
    client_ref: str,
    idempotency_key: str,
    received_at: datetime,
    decision: GateDecision | None = None,
    submission_id_override: str | None = None,
) -> PgStoreResult:
    """제출과 선택적 자동 정합성 이벤트를 한 트랜잭션으로 기록한다."""
    from psycopg.types.json import Jsonb

    digest = _canonical_hash(payload)
    sid = submission_id_override or _submission_id(client_ref, idempotency_key)
    run_id = str(payload.get("simulation_run_id") or "manual")[:32]
    values = (
        sid,
        run_id,
        client_ref,
        idempotency_key,
        digest,
        str(payload.get("insurer") or ""),
        str(payload.get("enrolled_on") or ""),
        [str(x).upper() for x in (payload.get("kcd_codes") or [])],
        str(payload.get("product_id") or ""),
        payload.get("age_band"),
        str(payload.get("outcome") or ""),
        str(payload.get("outcome_reason") or ""),
        payload.get("precheck_trace_id"),
        received_at,
    )

    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO demo.submission (
                    submission_id, run_id, client_ref, idempotency_key, payload_hash,
                    insurer, enrolled_on, kcd_codes, product_id, age_band, outcome,
                    outcome_reason, precheck_trace_id, received_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (client_ref, idempotency_key) DO NOTHING
                RETURNING submission_id
                """,
                values,
            )
            inserted = cur.fetchone()
            if inserted is None:
                cur.execute(
                    """
                    SELECT submission_id, payload_hash
                    FROM demo.submission
                    WHERE client_ref=%s AND idempotency_key=%s
                    """,
                    (client_ref, idempotency_key),
                )
                existing = cur.fetchone()
                if existing is None:
                    raise InfraError("멱등 충돌 후 기존 합성 제출을 찾지 못했습니다.")
                if existing[1] != digest:
                    raise ConflictErr(
                        "같은 idempotency_key에 다른 합성 payload가 제출됐습니다."
                    )
                cur.execute(
                    """
                    SELECT verification_level, reason_codes, rule_version
                    FROM demo.verification_event
                    WHERE submission_id=%s AND decision='accepted'
                    ORDER BY verification_id DESC LIMIT 1
                    """,
                    (existing[0],),
                )
                verified = cur.fetchone()
                return PgStoreResult(
                    stored=False,
                    submission_id=existing[0],
                    duplicate=True,
                    promoted=bool(verified),
                    verification=verified[0] if verified else "unverified",
                    reason_codes=tuple(verified[1] or ()) if verified else (),
                    rule_version=verified[2] if verified else "",
                )

            if decision is not None:
                level = "synthetic_consistency"
                cur.execute(
                    """
                    INSERT INTO demo.verification_event (
                        submission_id, decision, method, verification_level,
                        rule_version, reason_codes, evidence, actor
                    ) VALUES (%s,%s,'simulated_consistency',%s,%s,%s,%s,'simulator')
                    """,
                    (
                        sid,
                        "accepted" if decision.accepted else "rejected",
                        level,
                        decision.rule_version,
                        list(decision.reason_codes),
                        Jsonb(decision.evidence),
                    ),
                )
            conn.commit()
            return PgStoreResult(
                stored=True,
                submission_id=sid,
                promoted=bool(decision and decision.accepted),
                verification=(
                    "synthetic_consistency"
                    if decision and decision.accepted
                    else "rejected" if decision else "unverified"
                ),
                reason_codes=decision.reason_codes if decision else (),
                rule_version=decision.rule_version if decision else "",
            )
    except (ConflictErr, InfraError):
        raise
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"합성 PostgreSQL 제출을 저장하지 못했습니다: {e}") from e


def pending(limit: int = 100) -> list[dict]:
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT s.submission_id, s.client_ref, s.insurer, s.enrolled_on,
                       s.kcd_codes, s.product_id, s.age_band, s.outcome,
                       s.outcome_reason, s.precheck_trace_id, s.received_at
                FROM demo.submission s
                WHERE NOT EXISTS (
                    SELECT 1 FROM demo.verification_event v
                    WHERE v.submission_id=s.submission_id AND v.decision='accepted'
                )
                ORDER BY s.received_at, s.submission_id
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "submission_id": r[0], "client_ref": r[1], "insurer": r[2],
                "enrolled_on": r[3], "kcd_codes": list(r[4]), "product_id": r[5],
                "age_band": r[6], "outcome": r[7], "outcome_reason": r[8],
                "precheck_trace_id": r[9], "received_at": r[10].isoformat(),
                "verification": "unverified", "data_source": "synthetic",
            }
            for r in rows
        ]
    except InfraError:
        raise
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"합성 PostgreSQL 검수 큐를 읽지 못했습니다: {e}") from e


def promote(submission_id: str, *, actor: str, at: datetime | None = None) -> dict:
    """관리자 합성 검토 이벤트. 진위 확인이나 실제 지급 승인이 아니다."""
    from psycopg.types.json import Jsonb

    sid = (submission_id or "").strip()
    if not sid:
        raise ValidationErr("submission_id가 비어 있습니다.")
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT submission_id FROM demo.submission WHERE submission_id=%s FOR UPDATE",
                (sid,),
            )
            if cur.fetchone() is None:
                raise ValidationErr(f"합성 제출을 찾을 수 없습니다: {sid}")
            cur.execute(
                "SELECT 1 FROM demo.verification_event "
                "WHERE submission_id=%s AND decision='accepted'",
                (sid,),
            )
            if cur.fetchone():
                raise ValidationErr(f"이미 승격된 합성 제출입니다: {sid}")
            cur.execute(
                """
                INSERT INTO demo.verification_event (
                    submission_id, decision, method, verification_level,
                    rule_version, reason_codes, evidence, actor, verified_at
                ) VALUES (%s,'accepted','admin_review','synthetic_admin_review',
                          'synthetic-admin-review-v1',%s,%s,%s,COALESCE(%s,now()))
                RETURNING verified_at
                """,
                (
                    sid,
                    ["admin_approved_synthetic"],
                    Jsonb({"truth_verified": False, "synthetic_only": True}),
                    actor,
                    at,
                ),
            )
            verified_at = cur.fetchone()[0]
            conn.commit()
        return {
            "submission_id": sid,
            "verification": "synthetic_admin_review",
            "verification_method": "admin_review",
            "verified_by": actor,
            "verified_at": verified_at.isoformat(),
            "data_source": "synthetic",
        }
    except ValidationErr:
        raise
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"합성 PostgreSQL 검증 이벤트를 기록하지 못했습니다: {e}") from e


def counts() -> dict:
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT count(*)::int,
                       count(*) FILTER (WHERE EXISTS (
                           SELECT 1 FROM demo.verification_event v
                           WHERE v.submission_id=s.submission_id AND v.decision='accepted'
                       ))::int
                FROM demo.submission s
                """
            ).fetchone()
        total, promoted = row
        return {"submitted": total, "promoted": promoted, "pending": total - promoted}
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"합성 PostgreSQL 집계를 읽지 못했습니다: {e}") from e


def fetch_cohort(
    *, kcd_code: KcdCode, product_id: str, age_band: str | None
) -> CohortStats:
    clauses = ["kcd_code = %s"]
    params: list[object] = [kcd_code.code]
    if product_id:
        clauses.append("product_id = %s")
        params.append(product_id)
    if age_band:
        clauses.append("age_band = %s")
        params.append(age_band)
    where = " AND ".join(clauses)
    try:
        with _connect() as conn:
            rows = conn.execute(
                f"""
                SELECT outcome, verification_level, count(*)::int
                FROM demo.accepted_cohort_event
                WHERE {where}
                GROUP BY outcome, verification_level
                """,
                params,
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"합성 PostgreSQL 코호트를 집계하지 못했습니다: {e}") from e
    n = sum(r[2] for r in rows)
    approved = sum(r[2] for r in rows if r[0] == "paid")
    denied = sum(r[2] for r in rows if r[0] == "denied")
    grades: dict[str, int] = {}
    for _, grade, count in rows:
        grades[grade] = grades.get(grade, 0) + count
    return CohortStats(
        n=n, approved_n=approved, denied_n=denied,
        data_source=DataSource.SYNTHETIC,
        min_sample=DEFAULT_MIN_SAMPLE,
        warnings=(), by_verification=tuple(sorted(grades.items())),
    )


def reset() -> dict:
    """별도 합성 DB의 두 테이블만 비운다. 실제 사례 경로를 알지 못한다."""
    try:
        with _connect() as conn:
            conn.execute(
                "TRUNCATE demo.verification_event, demo.submission RESTART IDENTITY"
            )
            conn.commit()
        return {
            "reset": True,
            "removed": ["postgresql:insurance_demo/demo.verification_event",
                        "postgresql:insurance_demo/demo.submission"],
            "data_source": "synthetic",
        }
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"합성 PostgreSQL 트랙을 초기화하지 못했습니다: {e}") from e


def import_legacy_batch(
    records: list[dict], accepted_events: dict[str, dict]
) -> dict:
    """기존 파일 제출·승격을 **한 트랜잭션**으로 멱등 이관한다."""
    from psycopg.types.json import Jsonb

    inserted = 0
    accepted = 0
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            for rec in records:
                sid = str(rec.get("submission_id") or "").strip()
                client = str(rec.get("client_ref") or "unknown")
                if not sid:
                    raise ValidationErr("legacy 합성 제출에 submission_id가 없습니다.")
                digest = _canonical_hash(rec)
                received = datetime.fromisoformat(str(rec["received_at"]))
                cur.execute(
                    """
                    INSERT INTO demo.submission (
                        submission_id, run_id, client_ref, idempotency_key, payload_hash,
                        insurer, enrolled_on, kcd_codes, product_id, age_band, outcome,
                        outcome_reason, precheck_trace_id, received_at
                    ) VALUES (%s,'file_import',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT DO NOTHING
                    RETURNING submission_id
                    """,
                    (
                        sid, client, f"legacy-{sid}", digest,
                        str(rec.get("insurer") or ""), str(rec.get("enrolled_on") or ""),
                        [str(x).upper() for x in (rec.get("kcd_codes") or [])],
                        str(rec.get("product_id") or ""), rec.get("age_band"),
                        str(rec.get("outcome") or ""), str(rec.get("outcome_reason") or ""),
                        rec.get("precheck_trace_id"), received,
                    ),
                )
                if cur.fetchone():
                    inserted += 1
                else:
                    cur.execute(
                        "SELECT payload_hash FROM demo.submission WHERE submission_id=%s",
                        (sid,),
                    )
                    existing = cur.fetchone()
                    if existing is None or existing[0] != digest:
                        raise ConflictErr(
                            f"legacy submission_id가 다른 payload와 충돌합니다: {sid}"
                        )

                ev = accepted_events.get(sid)
                if ev:
                    cur.execute(
                        """
                        INSERT INTO demo.verification_event (
                            submission_id, decision, method, verification_level,
                            rule_version, reason_codes, evidence, actor, verified_at
                        ) VALUES (%s,'accepted','legacy_import','legacy_synthetic',
                                  'legacy-file-import-v1',%s,%s,%s,%s)
                        ON CONFLICT (submission_id) WHERE decision='accepted' DO NOTHING
                        RETURNING verification_id
                        """,
                        (
                            sid,
                            ["legacy_file_accepted_event"],
                            Jsonb({
                                "source_verification": ev.get("verification", ""),
                                "source_method": ev.get("verification_method", ""),
                                "truth_verified": False,
                            }),
                            str(ev.get("verified_by") or "legacy-import"),
                            datetime.fromisoformat(str(ev["verified_at"])),
                        ),
                    )
                    if cur.fetchone():
                        accepted += 1
            conn.commit()
        return {"inserted": inserted, "accepted_inserted": accepted}
    except (ValidationErr, ConflictErr, InfraError):
        raise
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"legacy 합성 파일을 PostgreSQL로 이관하지 못했습니다: {e}") from e


def legacy_snapshot() -> dict:
    """REPEATABLE READ 스냅샷에서 legacy count/hash reconcile 자료를 읽는다."""
    try:
        with _connect() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            rows = conn.execute(
                """
                SELECT s.submission_id, s.payload_hash,
                       EXISTS (SELECT 1 FROM demo.verification_event v
                               WHERE v.submission_id=s.submission_id
                                 AND v.decision='accepted') AS accepted
                FROM demo.submission s
                WHERE s.run_id='file_import'
                ORDER BY s.submission_id
                """
            ).fetchall()
            conn.rollback()
        normalized = [[r[0], r[1], bool(r[2])] for r in rows]
        digest = hashlib.sha256(
            json.dumps(normalized, separators=(",", ":")).encode()
        ).hexdigest()
        return {
            "submitted": len(rows),
            "accepted": sum(1 for r in rows if r[2]),
            "snapshot_sha256": digest,
        }
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"legacy PostgreSQL 스냅샷을 읽지 못했습니다: {e}") from e


__all__ = [
    "PgStoreResult", "canonical_hash", "counts", "fetch_cohort",
    "import_legacy_batch", "legacy_snapshot", "pending", "promote",
    "readiness", "reset", "store",
]
