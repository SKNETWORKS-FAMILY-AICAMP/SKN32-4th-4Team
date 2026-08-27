from __future__ import annotations

from scripts.review import complete_part5_reask_23 as complete


def test_complete_reask_23_has_no_empty_decisions() -> None:
    rows = complete.complete(complete.DEFAULT_INPUT, complete.DEFAULT_REAUDIT)

    assert len(rows) == 23
    assert len({row["item_id"] for row in rows}) == 23
    assert {key: sum(row["decision"] == key for row in rows) for key in "AENRS"} == {
        "A": 13,
        "E": 10,
        "N": 0,
        "R": 0,
        "S": 0,
    }
    assert all(row["note"].strip() for row in rows)
    assert all(row["edited_answer"].strip() for row in rows if row["decision"] == "E")


def test_a_decisions_explain_the_citation_meaning() -> None:
    rows = complete.complete(complete.DEFAULT_INPUT, complete.DEFAULT_REAUDIT)
    a_rows = [row for row in rows if row["stratum"].startswith("A:")]

    assert len(a_rows) == 13
    assert all(row["decision"] == "A" for row in a_rows)
    assert all(
        "면책 범위" in row["note"] or "보상하지 않는 항목" in row["note"]
        for row in a_rows
    )


def test_edits_do_not_expose_internal_fields() -> None:
    rows = complete.complete(complete.DEFAULT_INPUT, complete.DEFAULT_REAUDIT)
    forbidden = ("parse_status", "citation_eligible", "reason_code", "verdict")

    for row in rows:
        if row["decision"] == "E":
            assert not any(word in row["edited_answer"] for word in forbidden)
