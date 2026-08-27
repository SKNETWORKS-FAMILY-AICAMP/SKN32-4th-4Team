"""Auth persistence dependency with an explicit SQLite/PostgreSQL switch."""

from __future__ import annotations

from collections.abc import Iterator

from app.core.config import get_settings


def get_auth_store() -> Iterator[object]:
    """Yield the configured auth store without opening SQLite in PostgreSQL mode."""
    settings = get_settings()
    if settings.AUTH_PERSISTENCE == "postgres":
        from db.postgres.auth_repository import PgAuthStore

        yield PgAuthStore.from_settings()
        return
    if not settings.SQLITE_LEGACY_ENABLED:
        raise RuntimeError("SQLite legacy persistence is disabled")

    # PostgreSQL 전용 프로세스에서는 legacy 모듈과 엔진을 구성하지 않는다.
    from db.sqlite_legacy.connection import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
