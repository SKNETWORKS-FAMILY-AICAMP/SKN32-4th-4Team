from __future__ import annotations

import pytest

from scripts.review import reaudit_qa_pilot_part5 as reaudit


@pytest.fixture(scope="module")
def audited_rows() -> list[dict]:
    if reaudit.DEFAULT_INPUT.exists():
        return reaudit.build(reaudit.DEFAULT_INPUT)
    completed = (
        reaudit.ROOT
        / "docs"
        / "review"
        / "qa_pilot_completed_20260827"
        / "qa_pilot_review_part5_completed.jsonl"
    )
    if not completed.exists():
        pytest.skip("QA 원자료와 완성본이 없는 공개 배포 저장소입니다")
    return reaudit._read_jsonl(completed)


def test_latest_part5_is_complete_and_has_valid_dates(
    audited_rows: list[dict],
) -> None:
    assert len(audited_rows) == 60
    assert len({item["item_id"] for item in audited_rows}) == 60
    assert all(
        item["audit_checks"]["incident_after_enrollment"]
        for item in audited_rows
        if item["axis"] == "A"
    )


def test_reaudit_uses_item_specific_checks(audited_rows: list[dict]) -> None:
    rows = audited_rows

    assert {key: sum(row["decision"] == key for row in rows) for key in "AENRS"} == {
        "A": 13,
        "E": 47,
        "N": 0,
        "R": 0,
        "S": 0,
    }
    assert all(row["audit_checks"] for row in rows)
    assert len({str(sorted(row["audit_checks"].items())) for row in rows}) > 30
    assert all(row["edited_answer"].strip() for row in rows if row["decision"] == "E")


def test_b_axis_matches_plan_service_and_amount_before_editing(
    audited_rows: list[dict],
) -> None:
    rows = [row for row in audited_rows if row["axis"] == "B"]

    assert len(rows) == 12
    assert all(row["audit_checks"]["plan_matches"] for row in rows)
    assert all(row["audit_checks"]["service_matches"] for row in rows)
    assert all(row["audit_checks"]["draft_amount_matches"] for row in rows)
    assert all(row["decision"] == "E" for row in rows)
    assert all(" 입니다" not in row["edited_answer"] for row in rows)
    assert all("의20%" not in row["edited_answer"] for row in rows)
    assert all("보상대상" not in row["edited_answer"] for row in rows)
    assert all("보장대상" not in row["edited_answer"] for row in rows)
    assert all("주)" not in row["edited_answer"] for row in rows)
    assert all("을곱한" not in row["edited_answer"] for row in rows)
    assert all("%,보" not in row["edited_answer"] for row in rows)
    for row in rows:
        source = row["audit_checks"]["fact_amount"]
        edited = row["edited_answer"].removesuffix("입니다.")
        assert reaudit._clean_amount(source) == edited


def test_c_axis_removes_internal_field_names(audited_rows: list[dict]) -> None:
    rows = [row for row in audited_rows if row["axis"] == "C"]

    assert len(rows) == 12
    assert all(row["audit_checks"]["evidence_state_matches_kind"] for row in rows)
    assert all(row["decision"] == "E" for row in rows)
    for row in rows:
        assert "parse_status" not in row["edited_answer"]
        assert "citation_eligible" not in row["edited_answer"]


def test_a_axis_direct_evidence_matches_the_engine_reason(
    audited_rows: list[dict],
) -> None:
    rows = [row for row in audited_rows if row["axis"] == "A"]
    direct = [row for row in rows if row["stratum"].endswith(("excluded_by_clause", "exception_applies"))]

    assert len(direct) == 13
    assert all(row["audit_checks"]["support_ok"] for row in direct)
    assert all(row["decision"] == "A" for row in direct)
