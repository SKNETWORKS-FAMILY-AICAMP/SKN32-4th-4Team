# -*- coding: utf-8 -*-
"""`get_settings` 참조 누수 가드가 **실제로 무는지** 잰다.

★가드는 있다는 것만으로 안전장치가 아니다. 안 물면 「검사했다」는 착각만 남는다
  (실모델 적재 가드도 같은 이유로 `tests/test_real_model_guard.py` 를 따로 뒀다).

배경 — 2026-08-26 기본 CI 순서 의존 실패 2건:
    `tests/test_clause_search_route.py::_admin_app` 이
    `monkeypatch.setattr("app.core.config.get_settings", lambda: _S())` 를 거는 사이에
    `db.postgres.pgvector_index` 가 처음 임포트돼 **그 람다를 영구히** 물었다.
    monkeypatch 는 `app.core.config` 쪽 이름만 되돌린다.
    → `test_pgvector.py` · `test_pgvector_schema_pin.py` 가 전량 실행에서만 깨졌다.
"""

from __future__ import annotations

import sys
import types

import pytest

from tests.conftest import _leaked_settings_refs


def test_평소에는_아무것도_잡지_않는다():
    """정상 상태에서 오탐이 없어야 한다 — 오탐 나는 가드는 곧 꺼진다."""
    assert _leaked_settings_refs() == []


def test_남의_함수를_물고_있으면_잡는다():
    """바깥(시험)에서 들어온 함수가 붙어 있으면 **모듈 이름과 함께** 신고한다."""

    def _가짜설정():  # 이 함수의 __module__ 은 이 시험 파일이다
        raise AssertionError("불리면 안 된다")

    mod = types.ModuleType("app._가짜_누수_모듈")
    mod.get_settings = _가짜설정
    sys.modules["app._가짜_누수_모듈"] = mod
    try:
        leaked = _leaked_settings_refs()
        assert any("app._가짜_누수_모듈" in x for x in leaked), leaked
    finally:
        del sys.modules["app._가짜_누수_모듈"]

    assert _leaked_settings_refs() == []


def test_모듈이_자기_것을_정의한_경우는_통과시킨다():
    """`pgvector_index` 처럼 **지연 조회 래퍼**를 자기 안에 둔 것은 정상이다."""
    name = "app._자기것_정의_모듈"
    mod = types.ModuleType(name)

    def get_settings():
        raise AssertionError("불리면 안 된다")

    get_settings.__module__ = name          # 자기 모듈에서 정의된 것처럼
    mod.get_settings = get_settings
    sys.modules[name] = mod
    try:
        assert _leaked_settings_refs() == []
    finally:
        del sys.modules[name]


def test_pgvector_index_는_부를_때_찾는다():
    """실제로 고친 자리가 유지되는지 — import 시점에 다시 묶이면 회귀다."""
    from app.core import config as cfg
    from db.postgres import pgvector_index as m

    assert m.get_settings is not cfg.get_settings, (
        "pgvector_index 가 다시 import 시점에 묶였습니다 — 순서 의존 실패가 돌아옵니다")
    assert m.get_settings.__module__ == "db.postgres.pgvector_index"

    #: 지연 조회라서 **패치가 따라온다.** 그게 이 구조의 목적이다.
    sentinel = object()
    original = cfg.get_settings
    cfg.get_settings = lambda: sentinel
    try:
        assert m.get_settings() is sentinel
    finally:
        cfg.get_settings = original

    assert m.get_settings() is cfg.get_settings()


def test_가드가_실패_메시지에_고치는_법을_적는다():
    """메시지가 「무엇을 하라」까지 말해야 다음 사람이 안 헤맨다."""
    from tests.conftest import _settings_binding_guard

    gen = _settings_binding_guard.__wrapped__()
    next(gen)

    mod = types.ModuleType("app._가짜_누수_모듈2")
    mod.get_settings = test_가드가_실패_메시지에_고치는_법을_적는다
    sys.modules["app._가짜_누수_모듈2"] = mod
    try:
        with pytest.raises(RuntimeError) as e:
            next(gen)
    finally:
        del sys.modules["app._가짜_누수_모듈2"]
    msg = str(e.value)
    assert "app._가짜_누수_모듈2" in msg
    assert "config.get_settings()" in msg, "고치는 법이 없으면 가드가 절반만 일한다"
