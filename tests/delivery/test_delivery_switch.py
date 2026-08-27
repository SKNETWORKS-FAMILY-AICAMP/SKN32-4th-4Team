"""전달 계층 전환 스위치와 벤치 경로 게이트.

계획: `docs/plans/2026-08-25_0955_전달계층_Django분리_계획.md` §7.1

★**이 파일이 지키는 계약** — `DELIVERY_MODE=direct` + `BENCH_ENDPOINTS_ENABLED=false` 면
  전환 이전의 FastAPI 단독 구조와 **동작이 같아야 한다.** 되돌릴 수 없는 전환은 하지 않는다.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.delivery


@pytest.fixture(autouse=True)
def _fresh_settings():
    """설정 싱글턴을 앞뒤로 비운다 — 이 파일이 환경을 바꾸므로 뒤 테스트를 오염시키지 않는다."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ------------------------------------------------------------------ 모드 전환


def test_기본은_direct_다(monkeypatch):
    """★기본값이 곧 '되돌린 상태'다."""
    monkeypatch.delenv("DELIVERY_MODE", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()
    assert get_settings().DELIVERY_MODE == "direct"


def test_direct_는_fastapi_주소를_가리킨다(monkeypatch):
    monkeypatch.setenv("DELIVERY_MODE", "direct")
    monkeypatch.setenv("CUSTOMER_BASE_URL", "http://127.0.0.1:8080")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from delivery.bench.profile import target_base_url

    assert target_base_url() == "http://127.0.0.1:8080"


def test_django_는_전달계층_주소를_가리킨다(monkeypatch):
    monkeypatch.setenv("DELIVERY_MODE", "django")
    monkeypatch.setenv("DELIVERY_DJANGO_BASE_URL", "http://127.0.0.1:8000/")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from delivery.bench.profile import target_base_url

    #: 끝의 `/` 를 떼어 부하 스크립트가 `//v1/...` 을 만들지 않게 한다.
    assert target_base_url() == "http://127.0.0.1:8000"


def test_알_수_없는_모드는_조용히_direct_로_떨어지지_않는다(monkeypatch):
    """★무폴백(`RULE.md` §3.2) — 오타를 기본값으로 흡수하면 어느 구성을 쟀는지 모르게 된다."""
    from pydantic import ValidationError

    monkeypatch.setenv("DELIVERY_MODE", "djngo")
    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings()


# ------------------------------------------------------------------ 벤치 경로 게이트


def _paths(app) -> set[str]:
    return {getattr(r, "path", "") for r in app.routes}


def test_기본에서는_벤치_경로가_아예_없다(monkeypatch):
    """★'막혀 있다'가 아니라 '없다'. 마운트 자체를 하지 않는다."""
    monkeypatch.delenv("BENCH_ENDPOINTS_ENABLED", raising=False)
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    assert not any(p.startswith("/_bench") for p in _paths(create_app("full")))


def test_켜면_두_변종이_생긴다(monkeypatch):
    """async/sync 두 개를 둔다 — 차이가 곧 스레드풀 왕복 비용이다."""
    monkeypatch.setenv("BENCH_ENDPOINTS_ENABLED", "true")
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    paths = _paths(create_app("full"))
    assert "/_bench/noop" in paths
    assert "/_bench/noop-sync" in paths


def test_켜진_벤치_경로는_고정_응답을_준다(monkeypatch):
    monkeypatch.setenv("BENCH_ENDPOINTS_ENABLED", "true")
    from fastapi.testclient import TestClient

    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    client = TestClient(create_app("full"))
    for path in ("/_bench/noop", "/_bench/noop-sync"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        #: ★`bench: true` 로 실제 API 응답과 구분한다.
        assert resp.json() == {"ok": True, "bench": True}, path


def test_벤치_경로는_openapi_에_나오지_않는다(monkeypatch):
    """운영 문서에 재기용 경로가 섞이면 쓰는 사람이 혼란스럽다."""
    monkeypatch.setenv("BENCH_ENDPOINTS_ENABLED", "true")
    from fastapi.testclient import TestClient

    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    schema = TestClient(create_app("full")).get("/openapi.json").json()
    assert not [p for p in schema["paths"] if p.startswith("/_bench")]
