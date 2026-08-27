"""코호트 저장소 조립 어댑터.

실제 트랙은 현행 검증 파일만 보고, 합성 트랙은 명시된 demo backend만 본다.
두 출처를 합치거나 PostgreSQL 실패를 파일로 폴백하지 않는다.
"""

from __future__ import annotations

from app.core.domain.insurance import DataSource


def fetch(*, kcd_code, product_id: str, age_band: str | None, data_source: DataSource):
    if data_source is DataSource.VERIFIED_REAL:
        from app.core.config import get_settings

        if get_settings().VERIFIED_COHORT_STORE == "postgres":
            from db.postgres.pg_insurance_cohort_stats import fetch as pg_fetch

            return pg_fetch(
                kcd_code=kcd_code,
                product_id=product_id,
                age_band=age_band,
                data_source=data_source,
            )

    if data_source is DataSource.SYNTHETIC:
        from app.adapters.demo_submission_store import backend_name

        if backend_name() == "postgres":
            from db.postgres.pg_demo_submission_store import fetch_cohort

            return fetch_cohort(
                kcd_code=kcd_code, product_id=product_id, age_band=age_band
            )

    from app.adapters.file_cohort_stats import fetch as file_fetch

    return file_fetch(
        kcd_code=kcd_code,
        product_id=product_id,
        age_band=age_band,
        data_source=data_source,
    )


__all__ = ["fetch"]
