"""`/api/admin/clause-search` — **꺼진 채로 켜진 척하지 않는다.**

★여기서 고정하는 것
  · 고객 앱(8080)에는 이 경로가 **아예 없다**(404) — 라우터 자체가 안 실린다
  · 플래그가 꺼졌는데 `rerank=true` 면 **409** — 조용히 무시하고 200 을 주지 않는다
  · 리랭킹이 실패하면 **503** — 벡터 순서로 되돌려 200 을 주지 않는다
  · 응답에 판정처럼 읽히는 필드가 없다 — 검색은 근거 후보일 뿐이다

모델도 DB 도 띄우지 않는다. 어댑터를 가짜로 바꿔 **경로의 계약만** 본다.
"""

from __future__ import annotations

import contextlib

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@contextlib.contextmanager
def _admin_app(monkeypatch, **settings):
    """관리자 인증을 통과시킨 앱. 필요한 설정만 갈아끼운다."""
    from app.auth.roles import require_admin
    from app.core.config import get_settings

    base = get_settings()

    class _S:
        def __getattr__(self, name):
            if name in settings:
                return settings[name]
            return getattr(base, name)

    monkeypatch.setattr("app.core.config.get_settings", lambda: _S())
    app = create_app("admin")
    app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "ADMIN"}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_고객앱에는_경로가_없다():
    """★관리자 API 는 고객 프로세스에 **실리지도 않는다**(무인증 노출 표면 축소)."""
    r = TestClient(create_app("customer")).post(
        "/api/admin/clause-search", json={"query": "치과치료"})
    assert r.status_code == 404


def test_플래그가_꺼졌는데_재정렬을_요청하면_409(monkeypatch):
    """★조용히 무시하면 부르는 쪽은 재정렬된 결과를 받았다고 믿는다."""
    with _admin_app(monkeypatch, INSURANCE_CLAUSE_RERANK_ENABLED=False) as c:
        r = c.post("/api/admin/clause-search",
                   json={"query": "치과치료 보철료", "scope_sha256s": ["a" * 64], "rerank": True})
    assert r.status_code == 409
    assert "INSURANCE_CLAUSE_RERANK_ENABLED" in r.json()["detail"]


def test_범위를_안_주면_400(monkeypatch):
    """전역 검색은 `allow_global` 없이는 열리지 않는다."""
    with _admin_app(monkeypatch) as c:
        r = c.post("/api/admin/clause-search", json={"query": "치과치료"})
    assert r.status_code == 400
    assert "allow_global" in r.json()["detail"]


def test_질의가_너무_길면_422(monkeypatch):
    with _admin_app(monkeypatch) as c:
        r = c.post("/api/admin/clause-search",
                   json={"query": "가" * 513, "scope_sha256s": ["a" * 64]})
    assert r.status_code == 422


def test_리랭킹이_실패하면_503이고_되돌리지_않는다(monkeypatch):
    """★200 + 벡터 순서를 주면 실패가 감춰진다."""
    from app.core.usecases import clause_search

    def _boom(**_kw):
        raise clause_search.RerankUnavailable("RuntimeError: constant scores")

    monkeypatch.setattr(clause_search, "search", _boom)
    with _admin_app(monkeypatch, INSURANCE_CLAUSE_RERANK_ENABLED=True) as c:
        r = c.post("/api/admin/clause-search",
                   json={"query": "치과치료", "scope_sha256s": ["a" * 64], "rerank": True})
    assert r.status_code == 503
    assert "되돌리지 않습니다" in r.json()["detail"]


def test_결과에_판정처럼_읽히는_필드가_없다(monkeypatch):
    """검색은 근거 후보다. 보장 여부는 /v1/prechecks 가 정한다."""
    from app.core.usecases import clause_search

    monkeypatch.setattr(clause_search, "search", lambda **_kw: clause_search.ClauseSearchResult(
        hits=[], reranked=False, provenance={"index_generation": "s6"}, dropped_incomplete=0))
    with _admin_app(monkeypatch) as c:
        r = c.post("/api/admin/clause-search",
                   json={"query": "치과치료", "scope_sha256s": ["a" * 64]})
    assert r.status_code == 200
    body = r.json()
    assert not {"verdict", "covered", "abstained", "reason_code"} & set(body)
    assert body["reranked"] is False
    assert "판정이 아닙니다" in body["_주의"]


@pytest.mark.parametrize("field", ["index_generation"])
def test_어느_색인으로_찾았는지_남긴다(monkeypatch, field):
    """provenance 가 없으면 결과를 재현할 수 없다."""
    from app.core.usecases import clause_search

    monkeypatch.setattr(clause_search, "search", lambda **_kw: clause_search.ClauseSearchResult(
        hits=[], reranked=False,
        provenance={"index_generation": "s6", "query_embed_profile": "arctic@abc"},
        dropped_incomplete=3))
    with _admin_app(monkeypatch) as c:
        r = c.post("/api/admin/clause-search",
                   json={"query": "치과치료", "scope_sha256s": ["a" * 64]})
    body = r.json()
    assert field in body["provenance"]
    #: 본문이 없어 뺀 조각 수를 감추지 않는다 — 0 이 아니면 적재가 반쪽이라는 신호다.
    assert body["dropped_incomplete"] == 3


# ── .env 설정이 실제로 흐르는가 ────────────────────────────────────────

def test_설정한_채점본문이_응답에_드러난다(monkeypatch):
    """★어느 본문으로 순위를 매겼는지 응답이 말해야 한다. 이 값이 결과를 5%p 가른다."""
    from app.core.usecases import clause_search

    monkeypatch.setattr(clause_search, "search", lambda **_kw: clause_search.ClauseSearchResult(
        hits=[], reranked=False, provenance={}, dropped_incomplete=0))
    with _admin_app(monkeypatch, CLAUSE_RERANK_SCORE_BODY="full_clause",
                    CLAUSE_RERANK_MAX_LENGTH=1536) as c:
        body = c.post("/api/admin/clause-search",
                      json={"query": "치과치료", "scope_sha256s": ["a" * 64]}).json()
    assert body["settings"]["score_body"] == "full_clause"
    assert body["settings"]["max_length"] == 1536
    assert body["settings"]["rerank_enabled"] is False


def test_설정값이_유스케이스까지_전달된다(monkeypatch):
    """라우터가 읽고 흘려보내지 않으면 .env 를 고쳐도 아무 일도 안 일어난다."""
    from app.core.usecases import clause_search

    got: dict = {}

    def _spy(**kw):
        got.update(kw)
        return clause_search.ClauseSearchResult(hits=[], reranked=False,
                                                provenance={}, dropped_incomplete=0)

    monkeypatch.setattr(clause_search, "search", _spy)
    with _admin_app(monkeypatch, CLAUSE_RERANK_SCORE_BODY="chunk",
                    CLAUSE_RERANK_SCORE_CHARS=777,
                    CLAUSE_RERANK_MAX_CANDIDATES=25) as c:
        c.post("/api/admin/clause-search",
               json={"query": "치과치료", "scope_sha256s": ["a" * 64]})
    assert got["score_body"] == "chunk"
    assert got["score_chars"] == 777
    assert got["max_candidates"] == 25


def test_기본_설정이_실측_우세값이다():
    """★.env 를 안 건드려도 켜면 **더 잘 맞는 쪽**으로 돈다."""
    from app.core.config import Settings

    s = Settings()
    assert s.CLAUSE_RERANK_SCORE_BODY == "chunk"      # +5.04%p (2026-08-05 실측)
    assert s.CLAUSE_RERANK_MAX_LENGTH == 1536         # constant scores 실패 제거
    assert s.INSURANCE_CLAUSE_RERANK_ENABLED is False  # 켜는 것은 사람이 정한다


# ── 코덱스 독립 검증에서 나온 결함들(2026-08-05) ────────────────────────

def test_리랭커를_준비하지_못하면_503이다(monkeypatch):
    """★생성이 try 밖에 있어 500 으로 나갔다. 리랭커를 못 쓰는 것은 인프라 문제다."""
    import app.adapters.reranker as rr

    class _Boom:
        def __init__(self, *a, **k): raise RuntimeError("no CUDA")

    monkeypatch.setattr(rr, "CrossEncoderReranker", _Boom)
    with _admin_app(monkeypatch, INSURANCE_CLAUSE_RERANK_ENABLED=True) as c:
        r = c.post("/api/admin/clause-search",
                   json={"query": "치과치료", "scope_sha256s": ["a" * 64], "rerank": True})
    assert r.status_code == 503
    assert "리랭커를 준비하지 못했습니다" in r.json()["detail"]


def test_동시성_설정이_실제로_쓰인다(monkeypatch):
    """★`Semaphore(1)` 이 박혀 있어 CLAUSE_RERANK_CONCURRENCY 가 죽은 손잡이였다."""
    import app.routers.admin as ad

    monkeypatch.setattr(ad, "_RERANK_GATE", None)
    with _admin_app(monkeypatch, CLAUSE_RERANK_CONCURRENCY=3):
        assert ad._rerank_gate()._value == 3
    monkeypatch.setattr(ad, "_RERANK_GATE", None)


def test_채점_못한_후보_수를_응답이_말한다(monkeypatch):
    """빼고 세되, 뺐다는 사실을 감추지 않는다."""
    from app.core.usecases import clause_search

    monkeypatch.setattr(clause_search, "search", lambda **_kw: clause_search.ClauseSearchResult(
        hits=[], reranked=True, provenance={}, dropped_incomplete=1, dropped_unscorable=2))
    with _admin_app(monkeypatch) as c:
        body = c.post("/api/admin/clause-search",
                      json={"query": "치과치료", "scope_sha256s": ["a" * 64]}).json()
    assert body["dropped_incomplete"] == 1
    assert body["dropped_unscorable"] == 2
