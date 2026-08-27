"""health 라우터 테스트 (LLM 호출 없이 readiness 확인)."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_ok():
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "provider" in body
    assert set(body["readiness"].keys()) == {"local", "openai", "gemini", "db"}
    assert body["configured"] == body["readiness"]
    assert body["llm_live_check"] == "/api/health/llm"


def test_llm_health_uses_live_probe(monkeypatch):
    from app.adapters import llm_probe

    expected = {
        "provider": "local",
        "model": "configured-model",
        "configured": True,
        "ready": True,
        "latency_ms": 1.2,
        "error": None,
    }
    monkeypatch.setattr(llm_probe, "probe_llm", lambda: expected)
    resp = TestClient(app).get("/api/health/llm")
    assert resp.status_code == 200
    assert resp.json() == expected


def test_llm_probe_does_not_expose_provider_exception(monkeypatch):
    import httpx

    from app.adapters.llm_probe import probe_llm
    from app.core.config import Settings

    leaked = "postgresql://admin:secret@internal/db C:/private/config.env"

    def _boom(*_args, **_kwargs):
        raise RuntimeError(leaked)

    monkeypatch.setattr(httpx, "get", _boom)
    settings = Settings(
        _env_file=None,
        LLM_PROVIDER="local",
        LOCAL_BASE_URL="http://127.0.0.1:9999/v1",
        LOCAL_MODEL="configured-model",
    )
    result = probe_llm(settings)
    assert result["ready"] is False
    assert result["error"] == "모델 상태 확인에 실패했습니다."
    assert leaked not in str(result)
