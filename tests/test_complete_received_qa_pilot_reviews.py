from __future__ import annotations

import collections

import pytest

from scripts.review import complete_received_qa_pilot_reviews as complete


@pytest.fixture(scope="module")
def outputs() -> dict[int, list[dict]]:
    required = [
        complete.DEFAULT_RECEIVED / f"qa_pilot_review_part{part}.jsonl"
        for part in (1, 2, 4)
    ] + [
        complete.DEFAULT_PART5,
        complete.ROOT / "data" / "eval" / "retrieval_probes.json",
        complete.ROOT
        / "data"
        / "work"
        / "s7_1_approved_facts"
        / "approved_facts.jsonl",
        complete.ROOT
        / "data"
        / "work"
        / "s7_1_approved_facts"
        / "chunks.jsonl",
        complete.ROOT / "data" / "finetune" / "qa_pilot" / "candidates.jsonl",
    ]
    if all(path.exists() for path in required):
        return complete.build(complete.DEFAULT_RECEIVED, complete.DEFAULT_PART5)

    # 원문·OCR 작업 데이터는 저장소 배포 대상이 아니다. 그 환경에서는 저장된
    # 완성본의 구조와 감사 결과를 회귀 검사한다.
    completed_dir = (
        complete.ROOT / "docs" / "review" / "qa_pilot_completed_20260827"
    )
    completed_paths = [
        completed_dir / f"qa_pilot_review_part{part}_completed.jsonl"
        for part in (1, 2, 4, 5)
    ]
    if not all(path.exists() for path in completed_paths):
        pytest.skip("QA 원자료와 완성본이 없는 공개 배포 저장소입니다")
    return {
        part: complete._read_jsonl(path)
        for part, path in zip((1, 2, 4, 5), completed_paths, strict=True)
    }


def test_received_parts_are_reconstructed_without_missing_items(
    outputs: dict[int, list[dict]],
) -> None:
    all_rows = [row for part in (1, 2, 4, 5) for row in outputs[part]]

    assert len(all_rows) == 240
    assert len({row["item_id"] for row in all_rows}) == 239
    counts = collections.Counter(row["item_id"] for row in all_rows)
    assert {key: value for key, value in counts.items() if value > 1} == {
        "B:823789501858": 2
    }
    assert all(row["decision"] in {"A", "E"} for row in all_rows)
    assert all(row["note"].strip() for row in all_rows)
    assert all(row["edited_answer"].strip() for row in all_rows if row["decision"] == "E")


@pytest.mark.parametrize("part", [1, 2, 4])
def test_old_parts_use_gold_ids_and_approved_facts(
    outputs: dict[int, list[dict]], part: int,
) -> None:
    rows = outputs[part]
    a_rows = [row for row in rows if row["axis"] == "A"]
    b_rows = [row for row in rows if row["axis"] == "B"]
    c_rows = [row for row in rows if row["axis"] == "C"]

    assert len(a_rows) == 36 and all(row["decision"] == "A" for row in a_rows)
    assert all(row["audit_checks"]["gold_eligible_match"] for row in a_rows)
    assert len(b_rows) == 12 and all(row["decision"] == "E" for row in b_rows)
    assert all(row["audit_checks"]["plan_matches"] for row in b_rows)
    assert all(row["audit_checks"]["service_matches"] for row in b_rows)
    assert all(row["audit_checks"]["draft_amount_matches"] for row in b_rows)
    assert len(c_rows) == 12 and all(row["decision"] == "E" for row in c_rows)
    assert all(row["audit_checks"]["evidence_state_matches_kind"] for row in c_rows)


def test_part_decision_distributions(outputs: dict[int, list[dict]]) -> None:
    for part in (1, 2, 4):
        rows = outputs[part]
        assert {key: sum(row["decision"] == key for row in rows) for key in "AENRS"} == {
            "A": 36,
            "E": 24,
            "N": 0,
            "R": 0,
            "S": 0,
        }
    rows = outputs[5]
    assert {key: sum(row["decision"] == key for row in rows) for key in "AENRS"} == {
        "A": 13,
        "E": 47,
        "N": 0,
        "R": 0,
        "S": 0,
    }


def test_customer_edits_do_not_expose_internal_fields(
    outputs: dict[int, list[dict]],
) -> None:
    forbidden = ("parse_status", "citation_eligible", "reason_code", "verdict")
    edits = [
        row["edited_answer"]
        for part in (1, 2, 4, 5)
        for row in outputs[part]
        if row["decision"] == "E"
    ]
    assert edits
    assert all(not any(word in answer for word in forbidden) for answer in edits)
