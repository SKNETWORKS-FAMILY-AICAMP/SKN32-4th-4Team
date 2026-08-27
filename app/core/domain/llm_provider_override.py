"""생성 LLM 프로바이더를 **런타임에 바꾸는** 스위치 — `.env`의 `LLM_PROVIDER`를 덮어쓴다.

★왜 스위치가 필요한가

    `app/core/config.py`의 `LLM_PROVIDER`는 프로세스 시작 시 `.env`에서 한 번 읽혀
    `Settings` 싱글턴에 고정된다. 그런데 admin·customer는 **별도 프로세스**로 뜬다
    (`scripts/run_admin_server.py`·`scripts/run_customer_server.py`) — 관리자 화면에서
    프로바이더를 바꿔도 그 값을 프로세스 메모리에만 두면 customer 프로세스는 여전히
    옛 값을 본다(`identification_mode.py`가 판정 모드에서 겪었던 것과 같은 문제).

    그래서 오버라이드를 **파일에 남긴다** — 두 프로세스가 같은 파일을 읽으면
    같은 값을 본다.

★오버라이드가 없을 때는 `.env` 기본값을 그대로 쓴다

    이 파일이 없거나 오버라이드가 `None`이면 `settings.LLM_PROVIDER`가 그대로 쓰인다.
    관리자가 "기본값으로" 되돌리면 오버라이드 파일 자체를 지운다 — 잔여 상태를
    남기지 않는다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.errors import InfraError, ValidationErr

_ROOT = Path(__file__).resolve().parents[3]
_OVERRIDE_FILE = _ROOT / "config" / "llm_provider_override.json"

#: `app/core/config.py`의 `LLM_PROVIDER: Literal["local", "openai", "gemini"]`와 동일 집합.
PROVIDERS = frozenset({"local", "openai", "gemini"})


def current() -> str | None:
    """현재 오버라이드된 프로바이더. 오버라이드가 없으면 `None`(파일을 만들지 않는다 —
    읽기는 부작용이 없어야 한다)."""
    if not _OVERRIDE_FILE.exists():
        return None
    try:
        raw = json.loads(_OVERRIDE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        #: ★깨진 설정을 조용히 기본값으로 때우지 않는다. 어느 프로바이더로 판정했는지
        #:   모르는 채 답하는 것이 가장 위험하다(identification_mode.py의 원칙과 동일).
        raise InfraError(f"LLM 프로바이더 오버라이드 설정을 읽지 못했습니다: {e}") from e

    provider = raw.get("provider")
    if provider not in PROVIDERS:
        raise InfraError(f"알 수 없는 LLM 프로바이더 오버라이드입니다(설정 오염): {provider!r}")
    return provider


def set_override(provider: str | None, *, actor: str) -> dict:
    """오버라이드를 바꾸고 **누가 언제 바꿨는지 남긴다.**

    `provider=None`이면 오버라이드를 지운다(= `.env` 기본값으로 되돌림) — 파일 자체를
    삭제해 잔여 상태를 남기지 않는다.
    """
    if not (actor or "").strip():
        raise ValidationErr("프로바이더를 바꾼 사람을 비워 둘 수 없습니다.")

    changed_at = datetime.now(timezone.utc).isoformat()

    if provider is None:
        try:
            _OVERRIDE_FILE.unlink(missing_ok=True)
        except OSError as e:
            raise InfraError(f"LLM 프로바이더 오버라이드를 지우지 못했습니다: {e}") from e
        return {"provider": None, "changed_at": changed_at, "changed_by": actor}

    if provider not in PROVIDERS:
        raise ValidationErr(f"LLM 프로바이더는 {sorted(PROVIDERS)} 중 하나여야 합니다: {provider!r}")

    state = {"provider": provider, "changed_at": changed_at, "changed_by": actor}
    try:
        _OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _OVERRIDE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        raise InfraError(f"LLM 프로바이더 오버라이드를 저장하지 못했습니다: {e}") from e
    return state


__all__ = ["PROVIDERS", "current", "set_override"]
