"""D6 regression and mutation fixtures for unsafe table attachment.

These fixtures model the two failure shapes that must never become citable
evidence: prose split over adjacent cells and a two-column pairing that
repeats the same right-hand cell (a common false table/rowspan connection).
The production checks exercise the real attachment gate.  The test-only
mutants deliberately remove one safety condition at a time so the same
assertion oracle proves that each historical failure shape is detected.
"""

from collections.abc import Callable

import pytest

from scripts.extract.table_signals import attachment_verdict, prose_shape


Verdict = Callable[[dict], tuple[bool, list[str]]]


def _sentence_split_table() -> dict:
    return {
        "method": "선",
        "is_table": True,
        "records": [
            {"no": 1, "cols": {"1": "회사는 보험금을 지급하지", "2": "않습니다."}},
            {"no": 2, "cols": {"1": "다만 다음의 경우에는", "2": "지급합니다."}},
        ],
    }


def _repeated_right_cell_table() -> dict:
    return {
        "method": "2열짝짓기",
        "is_table": True,
        "signals": {"T2_dup_cells": 1.0},
        "records": [
            {"no": 1, "cols": {"1": "① 응급환자", "2": "보험금 지급 대상"}},
            {"no": 2, "cols": {"1": "② 이송", "2": "보험금 지급 대상"}},
            {"no": 3, "cols": {"1": "③ 기타", "2": "보험금 지급 대상"}},
        ],
    }


def _page_gate_rejected_table() -> dict:
    return {
        "method": "선",
        "is_table": False,
        "reject_why": ["T8 괘선 뻗음 부족"],
        "records": [
            {"no": 1, "cols": {"1": "보장 내용", "2": "20%"}},
        ],
    }


def _assert_rejected(
    verdict: Verdict,
    table: dict,
    reason_fragment: str,
) -> None:
    ok, why = verdict(table)

    assert ok is False, "unsafe table was attachable"
    assert any(reason_fragment in reason for reason in why), "rejection reason missing"


def _mutant_without_prose_veto(table: dict) -> tuple[bool, list[str]]:
    """Known-bad mutant: line tables bypass the independent T9 prose veto."""
    why: list[str] = []
    if table.get("method") != "선":
        why.append(f"미검증 방식 {table.get('method')!r}")
    if table.get("is_table") is False:
        why.extend(table.get("reject_why") or ["페이지 표 게이트 탈락"])
    return not why, why


def _mutant_without_method_gate(table: dict) -> tuple[bool, list[str]]:
    """Known-bad mutant: unverified two-column pairing is attachable."""
    why: list[str] = []
    if table.get("is_table") is False:
        why.extend(table.get("reject_why") or ["페이지 표 게이트 탈락"])
    prose = prose_shape(table.get("records") or [])
    if prose.get("is_prose"):
        why.extend(prose.get("prose_why") or ["T9 본문 모양"])
    return not why, why


def _mutant_discards_gate_reason(table: dict) -> tuple[bool, list[str]]:
    """Known-bad mutant: rejection survives, but its auditable reason is lost."""
    why: list[str] = []
    if table.get("method") != "선":
        why.append(f"미검증 방식 {table.get('method')!r}")
    if table.get("is_table") is False:
        why.append("페이지 표 게이트 탈락")
    prose = prose_shape(table.get("records") or [])
    if prose.get("is_prose"):
        why.extend(prose.get("prose_why") or ["T9 본문 모양"])
    return not why, why


def test_sentence_split_across_cells_is_rejected_before_attachment():
    _assert_rejected(attachment_verdict, _sentence_split_table(), "문장부호 비율")


def test_repeated_right_cell_pairing_is_never_attachable():
    _assert_rejected(attachment_verdict, _repeated_right_cell_table(), "미검증 방식")


def test_explicit_table_gate_rejection_is_preserved_with_d6_fixture():
    _assert_rejected(attachment_verdict, _page_gate_rejected_table(), "T8 괘선 뻗음 부족")


@pytest.mark.parametrize(
    ("mutant", "table", "reason_fragment", "expected_failure"),
    [
        pytest.param(
            _mutant_without_prose_veto,
            _sentence_split_table(),
            "문장부호 비율",
            "unsafe table was attachable",
            id="missing-prose-veto",
        ),
        pytest.param(
            _mutant_without_method_gate,
            _repeated_right_cell_table(),
            "미검증 방식",
            "unsafe table was attachable",
            id="missing-method-gate",
        ),
        pytest.param(
            _mutant_discards_gate_reason,
            _page_gate_rejected_table(),
            "T8 괘선 뻗음 부족",
            "rejection reason missing",
            id="discarded-rejection-reason",
        ),
    ],
)
def test_d6_oracle_kills_known_unsafe_mutants(
    mutant: Verdict,
    table: dict,
    reason_fragment: str,
    expected_failure: str,
) -> None:
    with pytest.raises(AssertionError, match=expected_failure):
        _assert_rejected(mutant, table, reason_fragment)
