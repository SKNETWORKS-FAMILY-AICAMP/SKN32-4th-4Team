"""Runtime observability persistence dependency."""

from __future__ import annotations

from collections.abc import Iterator

from app.core.config import get_settings


def get_ops_store() -> Iterator[object]:
    """Yield SQLite or PostgreSQL runtime storage according to configuration."""
    settings = get_settings()
    if settings.OPS_PERSISTENCE == "postgres":
        from db.postgres.ops_repository import PgOpsStore

        yield PgOpsStore.from_settings()
        return
    if not settings.SQLITE_LEGACY_ENABLED:
        raise RuntimeError("SQLite legacy persistence is disabled")

    # PostgreSQL 전용 프로세스에서는 legacy 모듈과 엔진을 구성하지 않는다.
    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
