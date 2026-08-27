"""공개 REST·등록 에이전트 REST·MCP가 같은 챗봇 계약을 지키는지 검증한다.

실제 네트워크와 PostgreSQL은 사용하지 않는다. 세 채널만 실제 라우팅하고 용어 원장과
LLM provider는 결정론적 fake로 바꾼다.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app.application.agent_facade import AgentFacade, get_agent_facade
from app.auth.agent_client import get_agent_principal, get_agent_registry
from app.core.config import Settings
from app.core.domain.agent_access import AgentPrincipal, RateLimitDecision
from app.core.ports.glossary import TermPassage


class _Glossary:
    def __init__(self) -> None:
        self.rows = [
            self._passage(
                "통원",
                "통원 의료기관에 입원하지 않고 방문하여 치료받는 것",
            ),
            self._passage(
                "도수치료",
                "도수치료 치료자가 손을 이용하여 실시하는 치료행위",
            ),
        ]

    @staticmethod
    def _passage(term: str, text: str) -> TermPassage:
        return TermPassage(
            kind="clause",
            sha256=(term.encode("utf-8").hex() + "a" * 64)[:64],
            insurer="가보험",
            qualified_no="보통약관/2.",
            section="보통약관",
            title="용어의 정의",
            page_from=3,
            page_to=3,
            content_hash=(term.encode("utf-8").hex() + "b" * 16)[:16],
            text=text,
        )

    def find(self, term, *, insurer=None, limit=20):
        rows = [
            row
            for row in self.rows
            if term in row.text and (not insurer or row.insurer == insurer)
        ]
        return rows[:limit] if limit else rows

    def meta(self):
        return {"built_from": "channel-e2e", "release": "s7-test"}


class _FakeModel:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def complete(self, _prompt, **_kwargs):
        with self._lock:
            self.calls += 1
            call_no = self.calls
        return f"약관 원문을 쉽게 설명한 결과입니다. #{call_no}"


class _Registry:
    def __init__(self) -> None:
        self.audits = []

    def consume_rate_limit(self, *_args, **_kwargs):
        return RateLimitDecision(True, 0)

    def append_audit(self, record):
        self.audits.append(record)


def _principal() -> AgentPrincipal:
    return AgentPrincipal(
        client_id="channel-e2e-agent",
        display_name="Channel E2E Agent",
        scopes=frozenset({"terms:read"}),
        rate_limit_per_minute=60,
        key_fingerprint="a" * 16,
    )


def _mcp_json(result) -> dict:
    if isinstance(result, tuple):
        result = result[-1]
    if isinstance(result, list):
        raw = result[0].text
    else:
        raw = getattr(result, "text", str(result))
    return json.loads(raw)


@pytest.fixture()
def channel_env(monkeypatch):
    from app import agent_main, main
    from app.auth import agent_client
    from app.routers import chat as chat_router

    settings = Settings(
        _env_file=None,
        APP_ENV="development",
        LLM_CHAT_ENABLED=True,
        LLM_PROVIDER="local",
        LLM_REQUEST_TIMEOUT_SECONDS=5,
        CHAT_RATE_LIMIT_PER_MINUTE=100,
        CHAT_LLM_MAX_CALLS_PER_MINUTE=0,
        CHAT_LLM_CACHE_TTL_SECONDS=30,
        CHAT_TRUST_FORWARDED_FOR=False,
        AGENT_API_ENABLED=True,
        AGENT_HASH_SECRET="test-only-agent-hash-secret-32-characters",
    )
    glossary = _Glossary()
    model = _FakeModel()
    registry = _Registry()

    monkeypatch.setattr(chat_router, "get_settings", lambda: settings)
    monkeypatch.setattr(chat_router, "get_active_model", lambda: "fake-channel-model")
    monkeypatch.setattr(chat_router, "_source", lambda: glossary)
    monkeypatch.setattr(chat_router, "_model", lambda: model)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_main, "get_settings", lambda: settings)
    monkeypatch.setattr(agent_client, "get_settings", lambda: settings)
    chat_router._reset_guard_for_tests()

    public_app = main.create_app("customer")
    registered_app = agent_main.create_agent_app()
    registered_app.dependency_overrides[get_agent_principal] = _principal
    registered_app.dependency_overrides[get_agent_registry] = lambda: registry
    registered_app.dependency_overrides[get_agent_facade] = AgentFacade

    yield {
        "chat_router": chat_router,
        "settings": settings,
        "model": model,
        "public": public_app,
        "registered": registered_app,
        "registry": registry,
    }
    chat_router._reset_guard_for_tests()


def test_public_registered_agent_mcp_share_in_process_response_contract(channel_env):
    """동일 프로세스 조립에서 세 인터페이스가 같은 guard·응답 계약을 재사용한다.

    실제 배포의 customer·agent·MCP 프로세스 사이에 메모리 캐시가 공유된다는 뜻은 아니다.
    """
    from app.mcp.server import mcp

    payload = {"message": "통원 뜻"}
    public = TestClient(channel_env["public"]).post("/v1/chat", json=payload)
    registered = TestClient(channel_env["registered"]).post(
        "/v1/agent/terms/explain",
        headers={"X-Agent-Subject": "opaque-user-0001"},
        json=payload,
    )
    mcp_body = _mcp_json(asyncio.run(mcp.call_tool("explain_term", payload)))

    assert public.status_code == 200
    assert registered.status_code == 200, registered.text
    public_body = public.json()
    registered_body = registered.json()
    assert public_body["message"] == registered_body["message"] == mcp_body["message"]
    assert public_body["llm"] == {
        "used": True,
        "provider": "local",
        "model": "fake-channel-model",
        "source": "call",
    }
    for body in (registered_body, mcp_body):
        assert body["llm"] == {
            "used": True,
            "provider": "local",
            "model": "fake-channel-model",
            "source": "cache",
        }
    assert channel_env["model"].calls == 1
    assert len(channel_env["registry"].audits) == 1


def test_public_chat_openapi_operation_id_is_backward_compatible(channel_env):
    operation = channel_env["public"].openapi()["paths"]["/v1/chat"]["post"]
    assert operation["operationId"] == "chat_turn_v1_chat_post"


def test_registered_agent_preserves_global_llm_budget_429(channel_env, monkeypatch):
    from app.mcp.server import mcp

    settings = channel_env["settings"].model_copy(
        update={
            "CHAT_LLM_MAX_CALLS_PER_MINUTE": 1,
            "CHAT_LLM_CACHE_TTL_SECONDS": 0,
        }
    )
    monkeypatch.setattr(channel_env["chat_router"], "get_settings", lambda: settings)
    channel_env["chat_router"]._reset_guard_for_tests()

    client = TestClient(channel_env["registered"])
    headers = {"X-Agent-Subject": "opaque-user-0001"}
    first = client.post(
        "/v1/agent/terms/explain",
        headers=headers,
        json={"message": "통원 뜻"},
    )
    limited = client.post(
        "/v1/agent/terms/explain",
        headers=headers,
        json={"message": "도수치료 뜻"},
    )
    mcp_limited = _mcp_json(
        asyncio.run(mcp.call_tool("explain_term", {"message": "도수치료 뜻"}))
    )

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.headers["Retry-After"]
    assert limited.json()["error_code"] == "rate_limit_exceeded"
    assert mcp_limited["ok"] is False
    assert mcp_limited["http_status"] == 429
    assert channel_env["model"].calls == 1
