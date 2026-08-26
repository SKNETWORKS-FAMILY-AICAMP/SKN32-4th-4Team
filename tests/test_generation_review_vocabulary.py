"""확정 원장이 쓰는 낱말을 **판정 게이트가 아는가.**

★왜 필요한가 — 실제로 깨져 있었다(2026-08-26 실측).

    게이트(`PolicyVersionRow.usable_for_judgment`)에 허용 값이
    `("reviewed", "partial")` 이라고 **손으로 적혀** 있었다.
    그런데 확정 원장(`config/confirmed_documents.jsonl`)은
    `verified` 54건 · `not_applicable` 3건을 쓰고 있었다.

    둘이 어긋나도 **아무도 소리 내지 않았다.** 게이트는 모르는 낱말을 만나면
    그냥 `False` 를 돌려주고, 그 문서는 판정 대상에서 조용히 사라진다 —
    확정까지 끝난 문서 **57건**이 그렇게 빠졌고(1,353 → 1,296),
    KB손해보험의 LIG 시절 약관 4건은 상품 검색에도 잡히지 않았다.

    "확정 절차를 다 밟았는데 화면에는 없다"는 것을 알아챌 방법이 없었다.
    이 테스트가 그 방법이다.

★이 테스트가 깨지면 무엇을 해야 하나

    새 낱말을 만들었다면 `app/core/ports/precheck.py` 의
    `GENERATION_REVIEW_OK` / `GENERATION_REVIEW_BLOCKED` 중
    **어느 쪽인지 정해서 넣는다.** 넣지 않으면 그 문서들은 판정에서 빠진다.
    빠뜨리는 것 자체는 안전한 쪽(fail-closed)이지만,
    **모르고 빠뜨리는 것**은 안전하지 않다.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.core.ports.precheck import (
    GENERATION_REVIEW_KNOWN,
    GENERATION_REVIEW_OK,
)

_LEDGER = pathlib.Path(__file__).resolve().parents[1] / "config" / "confirmed_documents.jsonl"


def _ledger_rows() -> list[dict]:
    if not _LEDGER.is_file():
        pytest.skip(f"확정 원장이 없습니다: {_LEDGER}")
    return [
        json.loads(line)
        for line in _LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_원장의_세대검토_낱말을_게이트가_전부_안다():
    rows = _ledger_rows()
    used = {(r.get("generation_review") or "") for r in rows}
    unknown = used - GENERATION_REVIEW_KNOWN
    assert not unknown, (
        f"확정 원장이 게이트가 모르는 낱말을 씁니다: {sorted(unknown)}. "
        "app/core/ports/precheck.py 의 GENERATION_REVIEW_OK / _BLOCKED 중 "
        "어디에 넣을지 정하세요 — 정하지 않으면 그 문서들은 판정에서 조용히 빠집니다."
    )


def test_확정된_문서가_세대검토_낱말_때문에_통째로_빠지지_않는다():
    """★한 낱말이 통째로 막히면 그 낱말을 쓰는 문서가 **전부** 사라진다.

    `verified` 가 그랬다 — 54건이 한 번에 빠졌다. 그래서 「어떤 낱말이
    확정 문서에 쓰이는데 허용 목록에 없다」를 따로 잡는다.
    """
    rows = _ledger_rows()
    confirmed = [r for r in rows if (r.get("identification") or "") == "confirmed"]
    if not confirmed:
        pytest.skip("확정 상태 문서가 없습니다.")

    blocked: dict[str, int] = {}
    for r in confirmed:
        word = r.get("generation_review") or ""
        if word not in GENERATION_REVIEW_OK:
            blocked[word] = blocked.get(word, 0) + 1

    assert not blocked, (
        f"확정까지 끝난 문서가 세대검토 낱말 때문에 판정에서 빠집니다: {blocked}. "
        "낱말이 '검토를 마쳤다'는 뜻이면 GENERATION_REVIEW_OK 에 넣으세요."
    )
