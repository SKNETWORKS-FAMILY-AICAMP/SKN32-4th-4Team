"""문서 확정을 **어디까지 믿을 것인가** — 판정 게이트의 모드.

★왜 스위치가 필요한가

    확정 원장(`config/confirmed_documents.jsonl`)의 항목은 두 단계를 거친다.

        1. 기계 대조   `scripts.confirm.identify_documents` 가 매니페스트와 맞춰 본다
        2. 사람 승인   운영자가 "이 PDF 가 이 상품의 이 버전이 맞다"를 확인한다

    2026-08-04 실측: 10건 전부 **1단계까지만** 끝나 있었다
    (`confirmed_by: "...사람 최종승인 대기"`). 그런데 판정은 이미 그것들을 쓰고 있었다 —
    **사람 승인 여부가 아무것도 막지 않고 있었다.**

    그건 결함이라기보다 **선언되지 않은 선택**이다. 선택은 해도 되지만
    숨기면 안 된다. 그래서 모드로 꺼내 놓고, 응답에 **항상 실어 보낸다.**

★두 모드

    `human_signoff` (엄격)   사람 승인까지 끝난 문서만 판정에 쓴다.
                            지금은 이 모드에서 판정 가능 약관이 **0건**이다.
    `machine_match` (시연)   기계 대조까지만 끝난 문서도 쓴다.
                            시연은 되지만 "사람이 확인한 약관"이라고 말하면 안 된다.

★기본값을 `machine_match` 로 두는 이유 — 그리고 그 대가

    끄면 지금 도는 시연이 통째로 0건이 된다. 그래서 켜 둔다.
    대신 **대가를 치른다** — 이 모드가 켜져 있으면 지원범위·판정 응답 양쪽에
    경고 문구가 붙고, 화면 배지가 바뀐다. 조용히 켜져 있는 일은 없다.

★모드는 파일에 남는다

    프로세스 메모리에만 두면 "누가 언제 켰나"를 답할 수 없고, 고객 서버와
    관리 서버가 **다른 값을 보게** 된다(실제로 관측 스트림에서 겪은 문제다).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.errors import InfraError, ValidationErr

_ROOT = Path(__file__).resolve().parents[3]
_MODE_FILE = _ROOT / "config" / "precheck_mode.json"

#: 사람 승인까지 끝난 것만 쓴다.
HUMAN_SIGNOFF = "human_signoff"
#: 기계 대조까지만 끝난 것도 쓴다(시연).
MACHINE_MATCH = "machine_match"
MODES = frozenset({HUMAN_SIGNOFF, MACHINE_MATCH})

#: ★원장의 `confirmed_by` 에 이 말이 들어 있으면 **사람 승인 전**이다.
#:   문자열 매칭이라 약하다 — 원장 형식이 바뀌면 여기도 바뀌어야 한다.
#:   그래서 `is_pending_signoff()` 한 곳에만 둔다.
_PENDING_MARK = "대기"

_DEFAULT = MACHINE_MATCH


@dataclass(frozen=True)
class ModeState:
    mode: str
    changed_at: str = ""
    changed_by: str = ""

    @property
    def auto_approve(self) -> bool:
        """기계 대조만으로 판정에 쓰는가."""
        return self.mode == MACHINE_MATCH

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "auto_approve": self.auto_approve,
            "changed_at": self.changed_at,
            "changed_by": self.changed_by,
            #: ★화면·API 가 이 문구를 **그대로** 쓴다. 두 군데서 다르게 적히지 않게.
            "label": (
                "자동승인(기계 대조까지만 끝난 문서도 판정에 사용)"
                if self.auto_approve
                else "엄격(사람 최종승인까지 끝난 문서만 판정에 사용)"
            ),
            "warning": (
                "자동승인 모드입니다 — 이 판정에 쓰인 약관은 기계 대조로만 확정됐고 "
                "사람의 최종 승인을 거치지 않았습니다."
                if self.auto_approve
                else ""
            ),
        }


def is_pending_signoff(ledger_entry: dict) -> bool:
    """이 원장 항목이 **사람 승인 전**인가."""
    return _PENDING_MARK in (ledger_entry.get("confirmed_by") or "")


def current() -> ModeState:
    """현재 모드. 파일이 없으면 기본값(파일을 만들지 않는다 — 읽기는 부작용이 없어야 한다)."""
    if not _MODE_FILE.exists():
        return ModeState(mode=_DEFAULT)
    try:
        raw = json.loads(_MODE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        #: ★깨진 설정을 기본값으로 **때우지 않는다.** 어느 모드로 판정했는지
        #:   모르는 채 답하는 것이 이 도메인에서 가장 위험하다.
        raise InfraError(f"판정 모드 설정을 읽지 못했습니다: {e}") from e

    mode = raw.get("mode")
    if mode not in MODES:
        raise InfraError(f"알 수 없는 판정 모드입니다(설정 오염): {mode!r}")
    return ModeState(mode=mode, changed_at=raw.get("changed_at", ""),
                     changed_by=raw.get("changed_by", ""))


def set_mode(mode: str, *, actor: str) -> ModeState:
    """모드를 바꾸고 **누가 언제 바꿨는지 남긴다.**"""
    if mode not in MODES:
        raise ValidationErr(f"판정 모드는 {sorted(MODES)} 중 하나여야 합니다: {mode!r}")
    if not (actor or "").strip():
        raise ValidationErr("모드를 바꾼 사람을 비워 둘 수 없습니다.")

    state = ModeState(mode=mode,
                      changed_at=datetime.now(timezone.utc).isoformat(),
                      changed_by=actor)
    try:
        _MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MODE_FILE.write_text(
            json.dumps({"mode": state.mode, "changed_at": state.changed_at,
                        "changed_by": state.changed_by}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    except OSError as e:
        raise InfraError(f"판정 모드를 저장하지 못했습니다: {e}") from e
    return state


__all__ = [
    "HUMAN_SIGNOFF", "MACHINE_MATCH", "MODES", "ModeState",
    "current", "is_pending_signoff", "set_mode",
]
