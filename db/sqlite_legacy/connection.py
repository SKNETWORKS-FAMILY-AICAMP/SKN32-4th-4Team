"""Legacy SQLite connection retained for local tests and recovery tooling."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.core.config import get_settings

_settings = get_settings()
# A disabled legacy store must not even create a SQLite engine: SQLAlchemy may
# create the file during engine initialization. Keep the symbols available for
# compatibility, but make accidental production use fail closed.
if not _settings.SQLITE_LEGACY_ENABLED:
    engine = None
    SessionLocal = sessionmaker(autoflush=False, autocommit=False)
else:
    engine = create_engine(
        _settings.DATABASE_URL,
        connect_args={
            "check_same_thread": False
        }
        if _settings.DATABASE_URL.startswith("sqlite")
        else {},
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db() -> Iterator[Session]:
    if engine is None:
        raise RuntimeError("SQLite legacy persistence is disabled")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
