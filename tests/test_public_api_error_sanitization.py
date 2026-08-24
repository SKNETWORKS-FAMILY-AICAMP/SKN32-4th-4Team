"""공개 보험 API가 저장소 예외의 경로·접속 문자열을 노출하지 않는다."""

from fastapi.testclient import TestClient

from app.core.errors import InfraError
from app.main import create_app


_LEAKED = "postgresql://admin:secret@internal/db C:/private/runtime.jsonl"


def _assert_sanitized(response) -> None:
    assert response.status_code == 503
    assert "secret" not in response.text
    assert "private" not in response.text
    assert response.json()["detail"] == "서비스 의존 시스템을 사용할 수 없습니다."


def test_demo_observation_store_error_is_sanitized(monkeypatch):
    from app.adapters import demo_submission_store

    def _boom(*_args, **_kwargs):
        raise InfraError(_LEAKED)

    monkeypatch.setattr(demo_submission_store, "store", _boom)
    response = TestClient(create_app()).post(
        "/v1/demo/observations",
        json={"client_ref": "demo-agent", "outcome": "paid"},
    )
    _assert_sanitized(response)


def test_public_observation_store_error_is_sanitized(monkeypatch):
    from app.adapters import external_submission_store
    from app.core.config import get_settings

    def _boom(*_args, **_kwargs):
        raise InfraError(_LEAKED)

    monkeypatch.setenv("OUTCOME_PERSISTENCE", "file")
    get_settings.cache_clear()
    monkeypatch.setattr(external_submission_store, "store", _boom)
    try:
        response = TestClient(create_app()).post(
            "/v1/observations",
            json={
                "client_ref": "web-ui",
                "insurer": "테스트보험",
                "outcome": "paid",
            },
        )
        _assert_sanitized(response)
    finally:
        get_settings.cache_clear()
