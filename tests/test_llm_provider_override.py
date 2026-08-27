"""LLM 프로바이더 런타임 오버라이드.

★이 파일이 지키는 명제

    1. 오버라이드가 없으면 `.env`(`settings.LLM_PROVIDER`)를 그대로 쓴다.
    2. 오버라이드는 파일에 남아 다른 프로세스도 같은 값을 본다.
    3. 오버라이드가 깨져 있으면 기본값으로 때우지 않고 **실패한다**.
    4. `llm_clients.py`의 세 함수(get_chat_client·get_langchain_chat·get_active_model)가
       오버라이드를 우선 쓴다.
"""

from __future__ import annotations

import json

import pytest

from app.core.domain import llm_provider_override as lpo
from app.core.errors import InfraError, ValidationErr


@pytest.fixture
def override_file(tmp_path, monkeypatch):
    f = tmp_path / "llm_provider_override.json"
    monkeypatch.setattr(lpo, "_OVERRIDE_FILE", f)
    return f


def test_오버라이드가_없으면_None이고_파일을_만들지_않는다(override_file):
    assert lpo.current() is None
    assert not override_file.exists()


def test_오버라이드를_설정하면_current가_그값을_반환한다(override_file):
    lpo.set_override("openai", actor="tester")
    assert lpo.current() == "openai"


def test_오버라이드는_파일에_남아_다른_프로세스도_같은_값을_본다(override_file):
    lpo.set_override("gemini", actor="tester")
    saved = json.loads(override_file.read_text(encoding="utf-8"))
    assert saved["provider"] == "gemini"
    assert saved["changed_by"] == "tester"
    assert saved["changed_at"]


def test_None으로_되돌리면_오버라이드가_지워진다(override_file):
    lpo.set_override("local", actor="tester")
    assert lpo.current() == "local"
    lpo.set_override(None, actor="tester")
    assert lpo.current() is None
    assert not override_file.exists()


def test_알_수_없는_프로바이더는_거절한다(override_file):
    with pytest.raises(ValidationErr, match="LLM 프로바이더"):
        lpo.set_override("anthropic", actor="tester")


def test_바꾼_사람_없이는_바꿀_수_없다(override_file):
    with pytest.raises(ValidationErr, match="비워"):
        lpo.set_override("openai", actor="  ")
    with pytest.raises(ValidationErr, match="비워"):
        lpo.set_override(None, actor="")


def test_설정이_깨졌으면_기본값으로_때우지_않고_실패한다(override_file):
    override_file.parent.mkdir(parents=True, exist_ok=True)
    override_file.write_text("{ not json", encoding="utf-8")
    with pytest.raises(InfraError, match="LLM 프로바이더"):
        lpo.current()

    override_file.write_text('{"provider": "anthropic"}', encoding="utf-8")
    with pytest.raises(InfraError, match="알 수 없는"):
        lpo.current()


# ── llm_clients.py가 오버라이드를 우선 쓰는가 ────────────────────────────
def test_get_active_model이_오버라이드_없으면_settings를_쓴다(monkeypatch):
    from app.core import llm_clients
    from app.core.config import Settings

    monkeypatch.setattr(lpo, "current", lambda: None)
    settings = Settings(_env_file=None, LLM_PROVIDER="openai", OPENAI_MODEL="gpt-4.1-nano")
    assert llm_clients.get_active_model(settings) == "gpt-4.1-nano"


def test_get_active_model이_오버라이드가_있으면_그것을_쓴다(monkeypatch):
    from app.core import llm_clients
    from app.core.config import Settings

    monkeypatch.setattr(lpo, "current", lambda: "gemini")
    settings = Settings(
        _env_file=None,
        LLM_PROVIDER="openai", OPENAI_MODEL="gpt-4.1-nano", GEMINI_MODEL="gemini-2.5-flash",
    )
    assert llm_clients.get_active_model(settings) == "gemini-2.5-flash"
