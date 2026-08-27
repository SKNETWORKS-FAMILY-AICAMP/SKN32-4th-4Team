"""run_events 기록 헬퍼 (NFR-OBS-01, TEST-OBS-002).

Interface 계층(라우터)에서 호출한다. Application(AnswerQuestion)은 순수 유지 — 관측성 기록은
경계 밖에서 수행해 도메인이 DB/trace에 의존하지 않게 한다.
"""

from __future__ import annotations

import json

from app.auth.user_types import AuthStore
from app.obs.trace import get_trace_id
from db.postgres.auth_repository import PgAuthStore
from db.postgres.ops_repository import PgOpsStore


def record_event(
    db: AuthStore | PgOpsStore, kind: str, detail: dict | None = None
) -> object | None:
    """현재 trace_id로 이벤트를 append한다. detail은 요약 dict(원문·민감정보 금지)."""
    if isinstance(db, (PgAuthStore, PgOpsStore)):
        db.record_event(get_trace_id() or "no-trace", kind, detail)
        return None
    #: ★레거시 SQLite 분기 **안에서만** 적재한다 — 최상단에서 부르면
    #:   PostgreSQL 전용 배포에도 SQLAlchemy 가 딸려 온다(`app/auth/user_types.py`).
    from db.sqlite_legacy.models import RunEvent

    event = RunEvent(
        trace_id=get_trace_id() or "no-trace",
        kind=kind,
        detail=json.dumps(detail or {}, ensure_ascii=False),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
