"""config 단위 테스트 (LLM 호출 없음)."""

import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.config_validation import (
    settings_readiness,
    validate_agent_bind,
    validate_production_persistence,
)
from app.core.errors import ConfigError


def test_config_module_contains_settings_only():
    import app.core.config as config

    assert hasattr(config, "Settings")
    assert hasattr(config, "get_settings")
    for name in (
        "require_secret_key",
        "require_agent_hash_secret",
        "require_insurance_idempotency_secret",
        "has_openai_key",
        "has_google_key",
        "validate_agent_bind",
        "validate_production_persistence",
    ):
        assert not hasattr(config, name)


def test_default_provider_is_local():
    s = Settings(_env_file=None)
    assert s.LLM_PROVIDER == "local"


def test_chat_cost_guard_settings_have_safe_defaults_and_reject_negative_values():
    settings = Settings(_env_file=None)
    assert settings.CHAT_RATE_LIMIT_PER_MINUTE == 20
    assert settings.CHAT_LLM_MAX_CALLS_PER_MINUTE == 60
    assert settings.CHAT_LLM_CACHE_TTL_SECONDS == 30
    assert settings.CHAT_TRUST_FORWARDED_FOR is False

    with pytest.raises(ValidationError):
        Settings(_env_file=None, CHAT_RATE_LIMIT_PER_MINUTE=-1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, CHAT_LLM_MAX_CALLS_PER_MINUTE=-1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, CHAT_LLM_CACHE_TTL_SECONDS=-1)


def test_readiness_keys():
    s = Settings(_env_file=None)
    r = settings_readiness(s)
    assert set(r.keys()) == {"local", "openai", "gemini", "db"}
    assert all(isinstance(v, bool) for v in r.values())


def test_local_ready_without_keys():
    s = Settings(_env_file=None)
    assert settings_readiness(s)["local"] is True
    assert settings_readiness(s)["openai"] is False
    assert settings_readiness(s)["gemini"] is False


def test_database_url_default_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    s = Settings(_env_file=None)
    assert s.DATABASE_URL.startswith("sqlite")
    assert s.DATABASE_URL.endswith("/data/db/insurance.sqlite3")
    assert s.DB_DIR == s.DATA_DIR / "db"


def test_production_rejects_sqlite_defaults():
    settings = Settings(_env_file=None, APP_ENV="production")

    with pytest.raises(ConfigError, match="PostgreSQL"):
        validate_production_persistence(settings)


def test_production_accepts_postgres_cutover():
    settings = Settings(
        _env_file=None,
        APP_ENV="production",
        DATABASE_URL="postgresql+psycopg://runtime@db.example/insurance_real",
        AUTH_PERSISTENCE="postgres",
        OPS_PERSISTENCE="postgres",
        PRECHECK_PERSISTENCE="postgres",
        OUTCOME_PERSISTENCE="postgres",
        DEMO_STORE_BACKEND="postgres",
        CLAUSE_STORE="pg",
        VERIFIED_COHORT_STORE="postgres",
        SQLITE_LEGACY_ENABLED=False,
        #: ★코덱스 리뷰 지적으로 이 검증에 추가됨 — 셀렉터가 postgres여도
        #:   실제 접속정보가 비어 있으면 통과했던 결함(app/core/config_validation.py).
        INSURANCE_PG_DSN="postgresql://runtime@db.example/insurance_real",
        INSURANCE_ADMIN_PG_DSN="postgresql://admin@db.example/insurance_real",
        INSURANCE_IDEMPOTENCY_SECRET="x" * 32,
    )

    validate_production_persistence(settings)


def test_disabled_sqlite_does_not_create_engine_or_file(tmp_path):
    db_path = tmp_path / "must-not-exist.sqlite3"
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "production",
            "DATABASE_URL": f"sqlite:///{db_path.as_posix()}",
            "SQLITE_LEGACY_ENABLED": "false",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from db.sqlite_legacy.connection import engine; assert engine is None",
        ],
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert not db_path.exists()


def test_openai_key_detection():
    s = Settings(_env_file=None, OPENAI_API_KEY="sk-test")
    from app.core.config_validation import has_openai_key

    assert has_openai_key(s) is True
    assert settings_readiness(s)["openai"] is True


def test_agent_bind_default_is_loopback_and_customer_url_is_independent():
    settings = Settings(_env_file=None, CUSTOMER_BASE_URL="http://example.invalid:9999")
    assert validate_agent_bind(settings) == ("127.0.0.1", 8082)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.10", "agent.example.test"])
def test_agent_remote_bind_requires_explicit_opt_in(host):
    settings = Settings(
        _env_file=None,
        AGENT_BIND_HOST=host,
        ALLOW_REMOTE_AGENT_BIND=False,
    )
    with pytest.raises(ConfigError):
        validate_agent_bind(settings)


def test_agent_remote_bind_can_be_explicitly_enabled():
    settings = Settings(
        _env_file=None,
        AGENT_BIND_HOST="0.0.0.0",
        ALLOW_REMOTE_AGENT_BIND=True,
    )
    assert validate_agent_bind(settings) == ("0.0.0.0", 8082)
