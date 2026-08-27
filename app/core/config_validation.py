"""설정값을 사용하는 검증·정책 함수.

`config.py`는 환경변수와 기본값을 담는 `Settings` 정의만 유지한다.
이 모듈은 설정 객체를 받아 비밀키·프로바이더·배포 정책을 검증한다.
실행 서버의 bind 정책도 설정값 자체가 아니라 런타임 정책이므로 이곳에 둔다.
"""

from __future__ import annotations

import ipaddress

from app.core.config import Settings
from app.core.errors import ConfigError


def require_secret_key(settings: Settings) -> str:
    if not (settings.SECRET_KEY and settings.SECRET_KEY.strip()):
        raise ConfigError("SECRET_KEY가 설정되지 않았습니다. .env에 SECRET_KEY를 넣으세요.")
    return settings.SECRET_KEY


def require_agent_hash_secret(settings: Settings) -> str:
    value = (settings.AGENT_HASH_SECRET or "").strip()
    if len(value) < 32:
        raise ConfigError(
            "AGENT_HASH_SECRET가 없거나 너무 짧습니다. "
            "외부 에이전트 API에는 32자 이상의 별도 난수를 설정하세요."
        )
    return value


def require_insurance_idempotency_secret(settings: Settings) -> str:
    value = (settings.INSURANCE_IDEMPOTENCY_SECRET or "").strip()
    if len(value) < 32:
        raise ConfigError(
            "INSURANCE_IDEMPOTENCY_SECRET이 없거나 너무 짧습니다. "
            "precheck 원문 키를 저장하지 않도록 32자 이상의 별도 secret을 설정하세요."
        )
    return value


def has_openai_key(settings: Settings) -> bool:
    return bool(settings.OPENAI_API_KEY and settings.OPENAI_API_KEY.strip())


def has_google_key(settings: Settings) -> bool:
    return bool(settings.GOOGLE_API_KEY and settings.GOOGLE_API_KEY.strip())


def settings_readiness(settings: Settings) -> dict[str, bool]:
    """외부 연결 없이 환경 설정만 확인한다."""

    return {
        "local": settings.LLM_PROVIDER == "local" and bool(settings.LOCAL_BASE_URL),
        "openai": has_openai_key(settings),
        "gemini": has_google_key(settings),
        "db": bool(settings.DATABASE_URL),
    }


def validate_production_persistence(settings: Settings) -> None:
    """운영 환경이 SQLite로 조용히 기동하지 않도록 검증한다."""

    if settings.APP_ENV != "production":
        return

    persistence = {
        "AUTH_PERSISTENCE": settings.AUTH_PERSISTENCE,
        "OPS_PERSISTENCE": settings.OPS_PERSISTENCE,
        "PRECHECK_PERSISTENCE": settings.PRECHECK_PERSISTENCE,
        "OUTCOME_PERSISTENCE": settings.OUTCOME_PERSISTENCE,
        "DEMO_STORE_BACKEND": settings.DEMO_STORE_BACKEND,
        "VERIFIED_COHORT_STORE": settings.VERIFIED_COHORT_STORE,
    }
    bad = [name for name, value in persistence.items() if value != "postgres"]
    if settings.CLAUSE_STORE != "pg":
        bad.append("CLAUSE_STORE=pg")
    if settings.SQLITE_LEGACY_ENABLED:
        bad.append("SQLITE_LEGACY_ENABLED=false")
    if settings.DATABASE_URL.lower().startswith("sqlite"):
        bad.append("DATABASE_URL=PostgreSQL")
    #: ★코덱스 3차 리뷰 지적 — 선택자가 전부 postgres여도 실제 접속정보가 비어
    #:   있으면 여기를 그냥 통과했다. 그러면 기동은 성공하고 인증·저장 요청마다
    #:   나중에서야 실패한다("배포는 됐는데 아무것도 안 되는" 상태). 셀렉터와
    #:   함께 검사해야 기동 시점에 막힌다.
    if not settings.INSURANCE_PG_DSN.strip():
        bad.append("INSURANCE_PG_DSN")
    if not settings.INSURANCE_ADMIN_PG_DSN.strip():
        bad.append("INSURANCE_ADMIN_PG_DSN")
    if len((settings.INSURANCE_IDEMPOTENCY_SECRET or "").strip()) < 32:
        bad.append("INSURANCE_IDEMPOTENCY_SECRET(32자 이상)")
    if bad:
        raise ConfigError(
            "APP_ENV=production requires PostgreSQL cutover settings: "
            + ", ".join(bad)
        )


def validate_agent_bind(settings: Settings) -> tuple[str, int]:
    """원격 에이전트 서버 bind를 명시적으로 승인했는지 검사한다."""

    host = settings.AGENT_BIND_HOST.strip()
    if not host:
        raise ConfigError("AGENT_BIND_HOST가 비어 있습니다.")
    if not (1 <= settings.AGENT_PORT <= 65535):
        raise ConfigError("AGENT_PORT는 1~65535 범위여야 합니다.")

    loopback = host.lower() == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(host).is_loopback
    except ValueError:
        # DNS 이름은 실행 시 원격 주소를 가리킬 수 있으므로 원격으로 취급한다.
        pass
    if not loopback and not settings.ALLOW_REMOTE_AGENT_BIND:
        raise ConfigError(
            "외부 에이전트 서버의 비-loopback bind가 차단됐습니다. "
            "TLS 종료·방화벽을 준비한 뒤 ALLOW_REMOTE_AGENT_BIND=true를 명시하세요."
        )
    return host, settings.AGENT_PORT


__all__ = [
    "has_google_key",
    "has_openai_key",
    "require_agent_hash_secret",
    "require_insurance_idempotency_secret",
    "require_secret_key",
    "settings_readiness",
    "validate_agent_bind",
    "validate_production_persistence",
]
