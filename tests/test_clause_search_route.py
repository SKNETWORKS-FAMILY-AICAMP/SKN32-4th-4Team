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


@contextlib.contextmanager
def _fake_worker(**stats):
    """★전역 워커에 **가짜를 심는다.**

    이걸 안 하면 라우터가 진짜 `CrossEncoderReranker` 를 만들고,
    시험이 **4B 무게추를 실제로 내려받는다**(2026-08-25에 실제로 그랬다).
    시험은 모델을 건드리면 안 된다.
    """
    from app.adapters import rerank_worker as rw

    class _W:
        def rerank(self, query, evidence, top_n=None, timeout=None):
            if "raise" in stats:
                raise stats["raise"]
            return evidence[:top_n] if top_n else evidence

        def stats(self):
            return {"loaded": True, "alive": True, **stats.get("stats", {})}

        def stop(self, timeout=None):
            return None

    rw.reset_worker()
    rw._WORKER = _W()
    try:
        yield rw._WORKER
    finally:
        rw.reset_worker()


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
    #: ★★`_fake_worker()` 가 **반드시** 있어야 한다(2026-08-26).
    #:
    #:   빠져 있었더니 라우터가 **진짜 `RerankWorker`** 를 만들었고, 그 데몬 스레드가
    #:   `Qwen3-Reranker-4B`(7.5GB)를 백그라운드로 내려받았다. 이 시험 자체는
    #:   워커가 아직 안 떠서 503 을 받고 **통과한다** — 그래서 아무도 눈치채지 못했다.
    #:   적재는 계속 돌다가 한참 뒤 다른 시험 도중 `access violation` 으로
    #:   **pytest 프로세스 전체를 죽였다**(전량 실행이 54~67% 에서 segfault · 재현 3/3).
    #:
    #:   ★그래서 증상과 원인이 멀리 떨어져 있었다 — 죽는 자리는 `test_rbac_admin` 인데
    #:   시작한 자리는 여기다. 같은 파일의 다른 시험 셋은 이미 `_fake_worker()` 를
    #:   쓰고 있었는데 이 시험만 빠져 있었다.
    with _fake_worker(), _admin_app(
            monkeypatch, INSURANCE_CLAUSE_RERANK_ENABLED=True) as c:
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
    """리랭커를 못 쓰는 것은 인프라 문제다 — 500(서버 버그)이 아니라 503.

    ★워커 설계로 바뀌면서 실패가 드러나는 자리가 옮겨졌다. 무게추 적재는
      워커 스레드에서 일어나므로 `get_worker()` 는 곧바로 돌아오고,
      **첫 일감에서** `RerankUnavailable` 이 올라온다.
    """
    from app.adapters import rerank_worker as rw
    from app.core.usecases import clause_search

    #: ★유스케이스도 가짜로 바꾼다. 안 그러면 **진짜 검색 경로**가 돌면서
    #:   질의 임베더가 arctic 무게추를 내려받는다 — 시험은 모델을 건드리면 안 된다.
    def _unavailable(**_kw):
        raise clause_search.RerankUnavailable("no CUDA") from rw.RerankUnavailable("no CUDA")

    monkeypatch.setattr(clause_search, "search", _unavailable)
    with _fake_worker(), _admin_app(
            monkeypatch, INSURANCE_CLAUSE_RERANK_ENABLED=True) as c:
        r = c.post("/api/admin/clause-search",
                   json={"query": "치과치료", "scope_sha256s": ["a" * 64], "rerank": True})
    assert r.status_code == 503
    assert "no CUDA" in r.json()["detail"]



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


# ── 전용 워커 · 시한 (2026-08-25) ───────────────────────────────────────

def test_시한을_넘기면_504다(monkeypatch):
    """★시한 초과와 장애는 다른 일이다. 둘 다 503 이면 원인을 못 가린다."""
    from app.adapters import rerank_worker as rw
    from app.core.usecases import clause_search

    def _timeout(**_kw):
        raise clause_search.RerankUnavailable("TimeoutError") from rw.RerankTimeout("느리다")

    monkeypatch.setattr(clause_search, "search", _timeout)
    with _fake_worker(), _admin_app(monkeypatch, INSURANCE_CLAUSE_RERANK_ENABLED=True,
                                    CLAUSE_RERANK_TIMEOUT_SECONDS=30.0) as c:
        r = c.post("/api/admin/clause-search",
                   json={"query": "치과치료", "scope_sha256s": ["a" * 64], "rerank": True})
    assert r.status_code == 504
    assert "시한" in r.json()["detail"]
    #: ★「추론이 계속 돈다」는 사실을 응답이 말해야 한다. 안 말하면 재시도가 몰린다.
    assert "끊을 수 없어" in r.json()["detail"]


def test_워커가_바쁘면_503이다(monkeypatch):
    from app.adapters import rerank_worker as rw
    from app.core.usecases import clause_search

    def _busy(**_kw):
        raise clause_search.RerankUnavailable("busy") from rw.RerankBusy("앞선 요청이 계산 중")

    monkeypatch.setattr(clause_search, "search", _busy)
    with _fake_worker(), _admin_app(monkeypatch, INSURANCE_CLAUSE_RERANK_ENABLED=True) as c:
        r = c.post("/api/admin/clause-search",
                   json={"query": "치과치료", "scope_sha256s": ["a" * 64], "rerank": True})
    assert r.status_code == 503
    assert "바쁩니다" in r.json()["detail"]


def test_시한이_응답_설정에_드러난다(monkeypatch):
    from app.core.usecases import clause_search

    monkeypatch.setattr(clause_search, "search", lambda **_kw: clause_search.ClauseSearchResult(
        hits=[], reranked=False, provenance={}, dropped_incomplete=0))
    with _admin_app(monkeypatch, CLAUSE_RERANK_TIMEOUT_SECONDS=12.5) as c:
        body = c.post("/api/admin/clause-search",
                      json={"query": "치과치료", "scope_sha256s": ["a" * 64]}).json()
    assert body["settings"]["timeout_seconds"] == 12.5


def test_준비상태가_워커를_만들지_않는다(monkeypatch):
    """★준비 상태를 물었을 뿐인데 4B 무게추가 올라가면 곤란하다."""
    from app.adapters import rerank_worker as rw
    from app.obs import readiness

    rw.reset_worker()
    monkeypatch.setattr("app.core.config.get_settings",
                        lambda: type("S", (), {
                            "INSURANCE_CLAUSE_RERANK_ENABLED": True,
                            "CLAUSE_RERANK_TIMEOUT_SECONDS": 30.0,
                            "CLAUSE_RERANK_MAX_CANDIDATES": 40,
                            "CLAUSE_RERANK_SCORE_BODY": "chunk"})())
    state = readiness._clause_rerank_state()
    assert state["enabled"] is True
    assert state["worker"] is None, "조회가 워커를 띄우면 안 된다"
    assert "아직 뜨지 않았습니다" in state["note"]
    assert rw.peek_worker() is None
