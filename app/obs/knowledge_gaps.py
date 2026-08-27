"""지식보강 큐 적재 (Phase 9).

RAG가 근거를 못 찾아 abstention한 질문을 모아 "문서 보강 대상"으로 삼는다.
**저장 전에 PII를 마스킹**한다 — 큐의 목적은 질문의 주제이지 개인정보가 아니다.
"""

from __future__ import annotations

from app.auth.user_types import AuthStore

from app.obs.pii import mask_pii
from app.obs.trace import get_trace_id
from db.postgres.ops_repository import PgKnowledgeGap, PgOpsStore


def record_gap_safe(question: str) -> bool:
    """세션을 스스로 열어 큐에 적재한다. **어떤 경우에도 예외를 던지지 않는다.**

    ★★관측이 제품을 죽이면 안 된다 — 그런데 **호출부까지 감싸야** 그게 성립한다.

        처음엔 함수 안쪽만 `try` 로 감쌌다. 그러자 시험이 잡았다 —
        호출부의 `from ... import` 나 함수 자체가 던지면 **판정 응답이 500 이 됐다.**
        판정은 이미 끝나 있는데 큐에 적재하다 실패해서 답을 못 주는 것은 거꾸로다.
        그래서 "절대 던지지 않는 함수" 하나로 만들고 호출부는 그냥 부른다.

    ★그렇다고 조용히 삼키지는 않는다 — 실패는 `run_events` 에 남긴다.

    Returns:
        적재 성공 여부. 호출부는 보통 무시해도 된다.
    """
    from app.obs.store import get_ops_store

    db = None
    try:
        db = next(get_ops_store())
        record_knowledge_gap(db, question)
        return True
    except Exception as exc:  # noqa: BLE001
        try:
            from app.obs.events import record_event

            if db is not None:
                record_event(db, "knowledge_gap_write_failed", {"error": str(exc)[:120]})
        except Exception:  # noqa: BLE001
            #: 감사 기록마저 실패하면 더 할 수 있는 것이 없다. 여기서 멈춘다.
            pass
        return False
    finally:
        if db is not None:
            try:
                close = getattr(db, "close", None)
                if close is not None:
                    close()
            except Exception:  # noqa: BLE001
                pass


def record_knowledge_gap(
    db: Session | PgOpsStore, question: str
) -> object | PgKnowledgeGap:
    """abstention 질문을 마스킹해 큐에 적재한다."""
    if isinstance(db, PgOpsStore):
        return db.record_knowledge_gap(mask_pii(question), get_trace_id() or "no-trace")
    #: ★레거시 SQLite 분기 **안에서만** 적재한다 — 최상단에서 부르면
    #:   PostgreSQL 전용 배포에도 SQLAlchemy 가 딸려 온다(`app/auth/user_types.py`).
    from db.sqlite_legacy.models import KnowledgeGap

    gap = KnowledgeGap(
        question=mask_pii(question),  # 원문 그대로 저장하지 않는다
        trace_id=get_trace_id() or "no-trace",
    )
    db.add(gap)
    db.commit()
    db.refresh(gap)
    return gap
