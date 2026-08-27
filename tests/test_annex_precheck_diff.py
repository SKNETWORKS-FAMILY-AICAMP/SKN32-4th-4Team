from __future__ import annotations

from scripts.eval.annex_precheck_diff import compare_document, safe_ref_reasons


def _clause(text: str, *, ordinal: int = 1, eligible: bool = True) -> dict:
    return {
        "ordinal": ordinal,
        "qualified_no": f"제{ordinal}조",
        "text": text,
        "citation_eligible": eligible,
        "statute": False,
    }


def _row(*, kind: str = "exclude", conditional: bool = False, quarantined: bool = False) -> dict:
    return {
        "sha12": "abc",
        "annex_ordinal": 3,
        "annex_label": "별표3",
        "clause_ordinal": 1,
        "clause_qualified_no": "제1조",
        "quarantined": quarantined,
        "ref": {"conditional": conditional, "context": "F04~F09 참조"},
        "mentions": [{"range": "F04~F09", "kind": kind, "context": "ctx"}],
    }


def _owner(status: str = "unique") -> dict:
    return {"owner_status": status, "owner_clause_ordinal": 1 if status == "unique" else None}


def test_safe_annex_exclusion_changes_decision() -> None:
    rows, summary, _ = compare_document(
        sha12="abc",
        insurer_dir="insurer",
        doc={"parse_status": "ok", "source": {}, "clauses": [_clause("코드 언급 없음")]},
        resolved_rows=[_row()],
        owner_by_annex={3: _owner()},
    )
    changed = {row["code"]: row for row in rows}
    assert changed["F04"]["baseline_status"] == "not_mentioned"
    assert changed["F04"]["safe_status"] == "excluded"
    assert summary["changed_codes_safe"] == 6


def test_clause_exception_keeps_precedence_over_annex_exclusion() -> None:
    rows, summary, _ = compare_document(
        sha12="abc",
        insurer_dir="insurer",
        doc={
            "parse_status": "ok",
            "source": {},
            "clauses": [_clause("보상하지 않습니다. 다만 F04~F09는 보상합니다.")],
        },
        resolved_rows=[_row()],
        owner_by_annex={3: _owner()},
    )
    assert not rows
    assert summary["changed_codes_safe"] == 0


def test_safe_gate_rejects_each_risky_reference_condition() -> None:
    usable = {1}
    assert safe_ref_reasons(_row(quarantined=True), owner=_owner(), usable_clause_ordinals=usable) == [
        "quarantined_document"
    ]
    assert safe_ref_reasons(_row(conditional=True), owner=_owner(), usable_clause_ordinals=usable) == [
        "conditional_reference"
    ]
    assert safe_ref_reasons(_row(), owner=_owner("ambiguous"), usable_clause_ordinals=usable) == [
        "ambiguous_owner"
    ]
    assert safe_ref_reasons(_row(), owner=_owner(), usable_clause_ordinals=set()) == [
        "origin_clause_not_usable"
    ]


def test_unsafe_ref_changes_diagnostic_mode_but_not_safe_mode() -> None:
    rows, summary, _ = compare_document(
        sha12="abc",
        insurer_dir="insurer",
        doc={"parse_status": "ok", "source": {}, "clauses": [_clause("코드 언급 없음")]},
        resolved_rows=[_row(conditional=True)],
        owner_by_annex={3: _owner()},
    )
    assert rows
    assert all(row["all_resolved_status"] == "excluded" for row in rows)
    assert all(row["safe_status"] == "not_mentioned" for row in rows)
    assert summary["changed_codes_safe"] == 0


def test_semantic_scope_blocks_other_subsection_ranges() -> None:
    row = _row()
    row["mentions"].append({"range": "S00~T98", "kind": "exclude", "context": "ctx"})
    rows, _, _ = compare_document(
        sha12="abc",
        insurer_dir="insurer",
        doc={"parse_status": "ok", "source": {}, "clauses": [_clause("코드 언급 없음")]},
        resolved_rows=[row],
        owner_by_annex={3: _owner()},
    )
    changed = {item["code"]: item for item in rows}
    assert changed["F04"]["safe_status"] == "excluded"
    assert changed["S00"]["structural_safe_status"] == "excluded"
    assert changed["S00"]["safe_status"] == "not_mentioned"
