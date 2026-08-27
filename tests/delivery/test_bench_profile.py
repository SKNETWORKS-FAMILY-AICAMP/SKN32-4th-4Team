"""벤치 프로필 보고 — "무슨 구성으로 쟀는지"를 기계가 찍는다.

계획: `docs/plans/2026-08-25_0955_전달계층_Django분리_계획.md` §6.4

★사람이 기억으로 측정 조건을 적으면 틀린다. 실제로 이 저장소에서 `CLAUSE_STORE` 가
  미설정인 채 파일 어댑터로 돌던 것을 아무도 몰랐다.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.delivery


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch):
    from app.core.config import get_settings
    import delivery.bench.profile as profile

    #: 단위 테스트가 개발기 자원이나 DB에 닿지 않도록 프로브 경계를 가짜로 고정한다.
    monkeypatch.setattr(profile, "_available_memory_gb", lambda: (8.0, None))
    monkeypatch.setattr(
        profile,
        "_postgres_connections",
        lambda dsn: ({"current": 1, "max_connections": 100}, None),
    )
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_측정조건을_전부_담는다(monkeypatch):
    monkeypatch.delenv("CLAUSE_STORE", raising=False)
    from delivery.bench.profile import describe

    d = describe()
    for key in (
        "delivery_mode",
        "target_base_url",
        "bench_endpoints_enabled",
        "stores",
        "insurance_pg_dsn_set",
        "memory",
        "postgres",
        "release",
        "blocking",
    ):
        assert key in d, key
    #: 저장소 7종이 한 자리에 모여야 리포트에 그대로 실을 수 있다.
    assert set(d["stores"]) == {
        "CLAUSE_STORE",
        "AUTH_PERSISTENCE",
        "OPS_PERSISTENCE",
        "PRECHECK_PERSISTENCE",
        "OUTCOME_PERSISTENCE",
        "VERIFIED_COHORT_STORE",
        "DEMO_STORE_BACKEND",
    }


def test_파일_저장소면_비교자료가_아니라고_말한다(monkeypatch):
    """★이게 이 모듈의 존재 이유다. 조용히 통과시키면 잘못된 수치가 리포트로 간다."""
    monkeypatch.delenv("CLAUSE_STORE", raising=False)
    from delivery.bench.profile import describe

    d = describe()
    assert d["stores"]["CLAUSE_STORE"] == "file"
    assert any("CLAUSE_STORE=file" in b for b in d["blocking"])


def test_DSN_값을_찍지_않는다(monkeypatch):
    """자격증명은 보고서에 담지 않는다. 설정 여부만 본다."""
    monkeypatch.setenv("INSURANCE_PG_DSN", "host=x port=5433 user=secret dbname=y")
    from delivery.bench.profile import describe

    d = describe()
    assert d["insurance_pg_dsn_set"] is True
    assert "secret" not in repr(d)


def test_DSN_이_비면_막는다(monkeypatch):
    monkeypatch.setenv("INSURANCE_PG_DSN", "")
    from delivery.bench.profile import describe

    assert any("INSURANCE_PG_DSN" in b for b in describe()["blocking"])


def test_설정_오류를_삼키지_않는다(monkeypatch):
    """오타가 있으면 `blocking` 에 사유가 남는다 — 조용히 file 로 보고하지 않는다."""
    monkeypatch.setenv("CLAUSE_STORE", "postgre")
    from delivery.bench.profile import describe

    d = describe()
    assert "해석 실패" in d["stores"]["CLAUSE_STORE"]
    assert d["blocking"]


def test_여유_메모리가_임계값_미만이면_막는다(monkeypatch):
    from delivery.bench.profile import MIN_AVAILABLE_MEMORY_GB, describe

    d = describe(memory_probe=lambda: (MIN_AVAILABLE_MEMORY_GB - 0.001, None))

    assert d["memory"]["available_gb"] == MIN_AVAILABLE_MEMORY_GB - 0.001
    assert any("여유 물리 메모리" in reason for reason in d["blocking"])


def test_메모리를_측정하지_못하면_None과_사유를_남긴다():
    from delivery.bench.profile import describe

    d = describe(memory_probe=lambda: (None, "지원하는 조회 수단 없음"))

    assert d["memory"]["available_gb"] is None
    assert d["memory"]["error"] == "지원하는 조회 수단 없음"
    assert any("확인하지 못했습니다" in reason for reason in d["blocking"])


def test_DSN이_있을_때만_PostgreSQL_연결을_조회한다(monkeypatch):
    from delivery.bench.profile import describe

    calls = []
    probe = lambda dsn: (calls.append(dsn) or {"current": 81, "max_connections": 100}, None)

    monkeypatch.setenv("INSURANCE_PG_DSN", "host=example user=secret")
    d = describe(postgres_probe=probe)
    assert len(calls) == 1
    assert d["postgres"]["connections"]["current"] == 81
    assert any("연결 사용률" in reason for reason in d["blocking"])
    assert "secret" not in repr(d)

    from app.core.config import get_settings

    get_settings.cache_clear()
    calls.clear()
    monkeypatch.setenv("INSURANCE_PG_DSN", "")
    describe(postgres_probe=probe)
    assert calls == []
