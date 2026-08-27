"""조항 리랭커 켬/끔을 **런타임에 바꾸는** 스위치 — `.env`의 `INSURANCE_CLAUSE_RERANK_ENABLED`를 덮어쓴다.

★왜 스위치가 필요한가

    admin·customer는 **별도 프로세스**로 뜬다(`scripts/run_admin_server.py`·
    `scripts/run_customer_server.py`). 관리자 화면에서 리랭커를 켜도 그 값을
    프로세스 메모리에만 두면 다른 프로세스는 여전히 옛 값을 본다
    (`llm_provider_override.py`·`identification_mode.py`와 같은 문제).

    그래서 오버라이드를 **파일에 남긴다** — 두 프로세스가 같은 파일을 읽으면
    같은 값을 본다.

★오버라이드가 없을 때는 `.env` 기본값을 그대로 쓴다

    이 파일이 없거나 오버라이드가 `None`이면 `settings.INSURANCE_CLAUSE_RERANK_ENABLED`가
    그대로 쓰인다. 관리자가 "기본값으로" 되돌리면 오버라이드 파일 자체를 지운다 —
    잔여 상태를 남기지 않는다.

★대상은 `/api/admin/clause-search`(관리자 전용) 뿐이다 — 판정 경로(`/v1/prechecks`)에는
    켜지 않기로 팀이 이미 결정했다(2026-08-25 §4). 이 스위치는 그 결정을 바꾸지 않는다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.errors import InfraError, ValidationErr

_ROOT = Path(__file__).resolve().parents[3]
_OVERRIDE_FILE = _ROOT / "config" / "clause_rerank_override.json"


def current() -> bool | None:
    """현재 오버라이드된 켬/끔. 오버라이드가 없으면 `None`(파일을 만들지 않는다 —
    읽기는 부작용이 없어야 한다)."""
    if not _OVERRIDE_FILE.exists():
        return None
    try:
        raw = json.loads(_OVERRIDE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        #: ★깨진 설정을 조용히 기본값으로 때우지 않는다. 켜졌는지 꺼졌는지
        #:   모르는 채 응답하는 것이 가장 위험하다(llm_provider_override.py와 동일 원칙).
        raise InfraError(f"조항 리랭커 오버라이드 설정을 읽지 못했습니다: {e}") from e

    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise InfraError(f"조항 리랭커 오버라이드 값이 올바르지 않습니다(설정 오염): {enabled!r}")
    return enabled


def set_override(enabled: bool | None, *, actor: str) -> dict:
    """오버라이드를 바꾸고 **누가 언제 바꿨는지 남긴다.**

    `enabled=None`이면 오버라이드를 지운다(= `.env` 기본값으로 되돌림) — 파일 자체를
    삭제해 잔여 상태를 남기지 않는다.
    """
    if not (actor or "").strip():
        raise ValidationErr("리랭커 스위치를 바꾼 사람을 비워 둘 수 없습니다.")

    changed_at = datetime.now(timezone.utc).isoformat()

    if enabled is None:
        try:
            _OVERRIDE_FILE.unlink(missing_ok=True)
        except OSError as e:
            raise InfraError(f"조항 리랭커 오버라이드를 지우지 못했습니다: {e}") from e
        return {"enabled": None, "changed_at": changed_at, "changed_by": actor}

    state = {"enabled": enabled, "changed_at": changed_at, "changed_by": actor}
    try:
        _OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _OVERRIDE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        raise InfraError(f"조항 리랭커 오버라이드를 저장하지 못했습니다: {e}") from e
    return state


__all__ = ["current", "set_override"]
