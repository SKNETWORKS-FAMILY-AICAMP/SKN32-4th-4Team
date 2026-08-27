"""조항 리랭커 런타임 오버라이드.

★이 파일이 지키는 명제

    1. 오버라이드가 없으면 `.env`(`settings.INSURANCE_CLAUSE_RERANK_ENABLED`)를 그대로 쓴다.
    2. 오버라이드는 파일에 남아 다른 프로세스도 같은 값을 본다.
    3. 오버라이드가 깨져 있으면 기본값으로 때우지 않고 **실패한다**.
    4. `/api/admin/clause-search`가 오버라이드를 우선 쓴다(판정 경로는 이 스위치와 무관).
"""

from __future__ import annotations

import json

import pytest

from app.core.domain import clause_rerank_override as cro
from app.core.errors import InfraError, ValidationErr


@pytest.fixture
def override_file(tmp_path, monkeypatch):
    f = tmp_path / "clause_rerank_override.json"
    monkeypatch.setattr(cro, "_OVERRIDE_FILE", f)
    return f


def test_오버라이드가_없으면_None이고_파일을_만들지_않는다(override_file):
    assert cro.current() is None
    assert not override_file.exists()


def test_오버라이드를_켜면_current가_True를_반환한다(override_file):
    cro.set_override(True, actor="tester")
    assert cro.current() is True


def test_오버라이드를_끄면_current가_False를_반환한다(override_file):
    cro.set_override(False, actor="tester")
    assert cro.current() is False


def test_오버라이드는_파일에_남아_다른_프로세스도_같은_값을_본다(override_file):
    cro.set_override(True, actor="tester")
    saved = json.loads(override_file.read_text(encoding="utf-8"))
    assert saved["enabled"] is True
    assert saved["changed_by"] == "tester"
    assert saved["changed_at"]


def test_None으로_되돌리면_오버라이드가_지워진다(override_file):
    cro.set_override(True, actor="tester")
    assert cro.current() is True
    cro.set_override(None, actor="tester")
    assert cro.current() is None
    assert not override_file.exists()


def test_바꾼_사람_없이는_바꿀_수_없다(override_file):
    with pytest.raises(ValidationErr, match="비워"):
        cro.set_override(True, actor="  ")
    with pytest.raises(ValidationErr, match="비워"):
        cro.set_override(None, actor="")


def test_설정이_깨졌으면_기본값으로_때우지_않고_실패한다(override_file):
    override_file.parent.mkdir(parents=True, exist_ok=True)
    override_file.write_text("{ not json", encoding="utf-8")
    with pytest.raises(InfraError, match="조항 리랭커"):
        cro.current()

    override_file.write_text('{"enabled": "yes"}', encoding="utf-8")
    with pytest.raises(InfraError, match="올바르지 않습니다"):
        cro.current()


# ── /api/admin/clause-search가 오버라이드를 우선 쓰는가 ──────────────────
# `tests/test_clause_search_route.py`의 `_admin_app`·`_fake_worker` 패턴을 그대로 쓴다.

import contextlib

from fastapi.testclient import TestClient


class _FakeStore:
    """`record_event`가 진짜 DB를 찾지 않도록 — SQLAlchemy 세션 흉내만 낸다."""

    def add(self, _event):
        return None

    def commit(self):
        return None

    def refresh(self, _event):
        return None


@contextlib.contextmanager
def _admin_app(monkeypatch, **settings):
    from app.auth.roles import require_admin
    from app.core.config import get_settings
    from app.main import create_app
    from app.obs.store import get_ops_store

    base = get_settings()

    class _S:
        def __getattr__(self, name):
            if name in settings:
                return settings[name]
            return getattr(base, name)

    monkeypatch.setattr("app.core.config.get_settings", lambda: _S())
    app = create_app("admin")
    app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "ADMIN"}
    app.dependency_overrides[get_ops_store] = lambda: _FakeStore()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@contextlib.contextmanager
def _fake_worker(**stats):
    from app.adapters import rerank_worker as rw

    class _W:
        def rerank(self, query, evidence, top_n=None, timeout=None):
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


def test_오버라이드로_켜면_env가_꺼져있어도_409가_안난다(monkeypatch):
    from app.core.usecases import clause_search

    monkeypatch.setattr(cro, "current", lambda: True)
    @contextlib.contextmanager
    def _conn():
        yield object()

    #: 이 시험은 오버라이드 우선순위만 본다. 개발 PC의 실제 PostgreSQL이나
    #: 임베딩 모델이 준비됐는지에 따라 503이 나면 스위치 계약을 시험할 수 없다.
    monkeypatch.setattr("db.postgres.pgvector_index.get_conn", _conn)
    monkeypatch.setattr("app.adapters.clause_query_embedder.build", lambda: object())
    monkeypatch.setattr("app.composition.build_clause_search_deps", lambda: {})
    monkeypatch.setattr(clause_search, "search", lambda **_kw: clause_search.ClauseSearchResult(
        hits=[], reranked=True, provenance={}, dropped_incomplete=0))

    with _fake_worker(), _admin_app(monkeypatch, INSURANCE_CLAUSE_RERANK_ENABLED=False) as c:
        r = c.post("/api/admin/clause-search",
                   json={"query": "치과치료", "scope_sha256s": ["a" * 64], "rerank": True})
    assert r.status_code == 200
    assert r.json()["settings"]["rerank_enabled"] is True


def test_오버라이드로_끄면_env가_켜져있어도_409다(monkeypatch):
    monkeypatch.setattr(cro, "current", lambda: False)

    with _admin_app(monkeypatch, INSURANCE_CLAUSE_RERANK_ENABLED=True) as c:
        r = c.post("/api/admin/clause-search",
                   json={"query": "치과치료", "scope_sha256s": ["a" * 64], "rerank": True})
    assert r.status_code == 409


def test_오버라이드가_없으면_env를_그대로_쓴다(monkeypatch):
    monkeypatch.setattr(cro, "current", lambda: None)

    with _admin_app(monkeypatch, INSURANCE_CLAUSE_RERANK_ENABLED=False) as c:
        r = c.post("/api/admin/clause-search",
                   json={"query": "치과치료", "scope_sha256s": ["a" * 64], "rerank": True})
    assert r.status_code == 409
    assert "INSURANCE_CLAUSE_RERANK_ENABLED" in r.json()["detail"]


# ── GET/PUT /api/admin/clause-rerank ──────────────────────────────────────

def test_GET_clause_rerank이_오버라이드_없으면_기본값을_effective로_낸다(monkeypatch):
    monkeypatch.setattr(cro, "current", lambda: None)
    with _admin_app(monkeypatch, INSURANCE_CLAUSE_RERANK_ENABLED=False) as c:
        r = c.get("/api/admin/clause-rerank")
    assert r.status_code == 200
    assert r.json() == {"override": None, "default": False, "effective": False}


def test_PUT_clause_rerank이_켜면_반영되고_current가_바뀐다(monkeypatch, override_file):
    with _admin_app(monkeypatch, INSURANCE_CLAUSE_RERANK_ENABLED=False) as c:
        r = c.put("/api/admin/clause-rerank", json={"enabled": True})
    assert r.status_code == 200
    assert r.json() == {"override": True, "default": False, "effective": True}
    assert cro.current() is True


def test_PUT_clause_rerank이_None이면_오버라이드를_지운다(monkeypatch, override_file):
    cro.set_override(True, actor="tester")
    with _admin_app(monkeypatch, INSURANCE_CLAUSE_RERANK_ENABLED=False) as c:
        r = c.put("/api/admin/clause-rerank", json={"enabled": None})
    assert r.status_code == 200
    assert r.json() == {"override": None, "default": False, "effective": False}
    assert cro.current() is None
