"""등록 외부 에이전트 전용 서버(기본 127.0.0.1:8082)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))


def main() -> int:
    from app.core.config import get_settings
    from app.core.config_validation import require_agent_hash_secret, validate_agent_bind
    from app.core.errors import ConfigError

    settings = get_settings()
    try:
        if not settings.AGENT_API_ENABLED:
            raise ConfigError(
                "AGENT_API_ENABLED=false입니다. DB·키·TLS 경계를 준비한 뒤 명시적으로 켜세요."
            )
        require_agent_hash_secret(settings)
        host, port = validate_agent_bind(settings)
    except ConfigError as exc:
        print(f"[agent-server] 시작 거부: {exc}", file=sys.stderr)
        return 2

    import uvicorn

    uvicorn.run("app.agent_main:agent_app", host=host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
