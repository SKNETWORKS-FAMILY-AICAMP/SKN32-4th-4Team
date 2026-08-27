"""Verified-real cohort adapter backed only by the insurance PostgreSQL view."""

from __future__ import annotations

from db.postgres.pg_insurance_repository import PgInsuranceRepository, _postgres_error
from app.core.domain.insurance import CohortStats, DataSource, KcdCode
from app.core.errors import AppError, ValidationErr
from app.core.usecases.cohort import DEFAULT_MIN_SAMPLE


def fetch(
    *,
    kcd_code: KcdCode,
    product_id: str,
    age_band: str | None,
    data_source: DataSource,
) -> CohortStats:
    if data_source is not DataSource.VERIFIED_REAL:
        raise ValidationErr("PostgreSQL 실제 코호트 adapter는 verified_real만 조회합니다.")

    repository = PgInsuranceRepository.from_settings()
    conn = repository._connect()
    try:
        rows = conn.execute(
            "SELECT cs.verification_method,sum(cs.n),sum(cs.approved_n),"
            "sum(cs.denied_n) FROM app.cohort_stats cs "
            "JOIN core.kcd_code kc ON kc.id=cs.kcd_code_id "
            "WHERE upper(kc.code)=upper(%s) "
            "AND (%s='' OR cs.product_id::text=%s) "
            "AND (%s::text IS NULL OR cs.age_band=%s::text) "
            "GROUP BY cs.verification_method ORDER BY cs.verification_method",
            (kcd_code.code, product_id, product_id, age_band, age_band),
        ).fetchall()
    except AppError:
        raise
    except Exception as exc:
        if getattr(exc, "sqlstate", None):
            raise _postgres_error(exc) from exc
        raise
    finally:
        conn.rollback()
        conn.close()

    grades = tuple((str(row[0]), int(row[1])) for row in rows)
    return CohortStats(
        n=sum(int(row[1]) for row in rows),
        approved_n=sum(int(row[2]) for row in rows),
        denied_n=sum(int(row[3]) for row in rows),
        data_source=DataSource.VERIFIED_REAL,
        min_sample=DEFAULT_MIN_SAMPLE,
        by_verification=grades,
    )


__all__ = ["fetch"]
