"""지식갭 큐가 **보험 경로에** 연결돼 있는가.

★왜 이 파일이 필요한가 — 큐가 영원히 비어 있었다

    계획서 §2-1 은 "근거 인용 + abstention → 지식갭 큐"를 재사용 자산으로 꼽았다.
    그런데 실제로 `record_knowledge_gap` 을 부르는 곳은 **커머스 RAG(`/api/rag/qa`)
    하나뿐**이었고, 그 경로는 고객 포트에 실리지도 않고 어떤 화면도 부르지 않는다.
    결과: 관리자 대시보드의 "지식갭" 패널이 **0건으로 고정**돼 있었다(2026-08-04 발견).

    "기능이 있다"와 "그 기능에 데이터가 닿는다"는 다르다. 후자를 시험한다.

★넣지 않는 것도 시험한다

    `documents_not_confirmed`(확정 작업이 밀림)를 큐에 넣으면 같은 문장으로 넘쳐
    진짜 보강 대상이 묻힌다. **안 넣는 것**이 계약이다.
"""

from __future__ import annotations

import pytest

from app.core.domain.precheck_result import PrecheckOutcome


def _gaps(client):
    from app.db.database import SessionLocal
    from app.db.models import KnowledgeGap

    db = SessionLocal()
    try:
        return [g.question for g in db.query(KnowledgeGap).all()]
    finally:
        db.close()


def _outcome(**kw) -> PrecheckOutcome:
    base = dict(
        verdict="needs_expert", abstained=True, reason_code="no_evidence", message="",
        applied_policy=None, per_code=(), citations=(), candidates=(),
        rule_engine_version="rules-test", extractor="test", trace_id="t", warnings=(),
    )
    base.update(kw)
    return PrecheckOutcome(**base)


@pytest.fixture
def _graph_returns(monkeypatch):
    """판정 결과를 원하는 값으로 고정한다."""
    from app.routers import precheck as router

    def _set(outcome):
        class _G:
            def invoke(self, _b):
                return outcome, {}

        monkeypatch.setattr(router, "_graph", lambda: _G())

    return _set


_REQ = {"insurer": "테스트화재", "enrolled_on": "20200101", "kcd_codes": ["S72.0"]}


def test_근거없어_기권하면_지식갭이_쌓인다(client, _graph_returns):
    _graph_returns(_outcome(reason_code="no_evidence"))
    before = len(_gaps(client))

    r = client.post("/v1/prechecks", json=_REQ)
    assert r.status_code == 200
    assert r.json()["reason_code"] == "no_evidence"

    after = _gaps(client)
    assert len(after) == before + 1, "근거 없음 기권이 지식갭에 안 쌓였다."
    assert "테스트화재" in after[-1] and "S72.0" in after[-1]


def test_문서미확정_기권은_큐에_넣지_않는다(client, _graph_returns):
    """★확정 작업이 밀린 것은 '지식이 없는 것'이 아니다. 넣으면 큐가 넘친다."""
    _graph_returns(_outcome(reason_code="documents_not_confirmed"))
    before = len(_gaps(client))

    client.post("/v1/prechecks", json=_REQ)

    assert len(_gaps(client)) == before, (
        "documents_not_confirmed 가 큐에 들어갔다 — 같은 문장으로 넘쳐 진짜 보강 대상이 묻힌다."
    )


def test_판정에_성공하면_큐에_넣지_않는다(client, _graph_returns):
    _graph_returns(_outcome(verdict="unlikely", abstained=False,
                            reason_code="excluded_by_clause"))
    before = len(_gaps(client))

    client.post("/v1/prechecks", json=_REQ)

    assert len(_gaps(client)) == before


def test_용어를_못_찾으면_지식갭이_쌓인다(client, monkeypatch):
    """용어집 보강 대상 — 이 큐의 본래 용도다."""
    from app.routers import chat as chat_router

    class _Empty:
        def find(self, term, *, insurer=None, limit=20):
            return []

        def meta(self):
            return {"built_from": "test"}

    monkeypatch.setattr(chat_router, "_source", lambda: _Empty())
    before = len(_gaps(client))

    #: ★용어 추출이 되는 말을 쓴다 — 추출 자체가 실패하면 422 라 다른 시험이 된다.
    r = client.post("/v1/chat", json={"message": "통원 뜻"})
    assert r.status_code == 200
    assert r.json()["found"] is False

    after = _gaps(client)
    assert len(after) == before + 1
    assert after[-1].startswith("[용어]")


def test_지식갭_적재_실패가_응답을_깨뜨리지_않는다(client, _graph_returns, monkeypatch):
    """★관측이 제품을 죽이면 안 된다 — 판정은 이미 끝나 있다."""
    from app.obs import knowledge_gaps

    def _boom(*_a, **_k):
        raise RuntimeError("DB 없음")

    #: ★적재 **내부**를 터뜨린다. `record_gap_safe` 가 그것을 삼켜야 한다.
    monkeypatch.setattr(knowledge_gaps, "record_knowledge_gap", _boom)
    _graph_returns(_outcome(reason_code="no_evidence"))

    r = client.post("/v1/prechecks", json=_REQ)
    assert r.status_code == 200, "지식갭 적재 실패가 판정 응답을 깨뜨렸다."
    assert r.json()["reason_code"] == "no_evidence"
