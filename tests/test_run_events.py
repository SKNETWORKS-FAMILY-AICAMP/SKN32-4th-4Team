"""TEST-OBS-002 — run_events 기록 + trace 상관."""

from __future__ import annotations

from db.sqlite_legacy.connection import SessionLocal
from db.sqlite_legacy.models import RunEvent
from app.obs.events import record_event
from app.obs.trace import set_trace_id


def test_record_event_persists_with_trace_id():
    set_trace_id("trace-evt-1")
    db = SessionLocal()
    try:
        ev = record_event(db, "rag_query", {"top_k": 3, "source_count": 2})
        assert ev.id is not None
        assert ev.trace_id == "trace-evt-1"
        assert ev.kind == "rag_query"
        row = db.query(RunEvent).filter(RunEvent.id == ev.id).first()
        assert row is not None and row.trace_id == "trace-evt-1"
        assert '"top_k": 3' in row.detail
    finally:
        db.close()
