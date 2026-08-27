from __future__ import annotations

from scripts.review import merge_qa_pilot_part5_codex as merger


def test_merge_replaces_only_the_ten_repaired_reviews() -> None:
    base = merger._read_jsonl(merger.DEFAULT_BASE)
    merged = merger.merge(merger.DEFAULT_BASE, merger.DEFAULT_REPAIRS)
    before = {row["item_id"]: row for row in base}

    assert len(merged) == 60
    assert len({row["item_id"] for row in merged}) == 60
    assert {key: sum(row["decision"] == key for row in merged) for key in "AENRS"} == {
        "A": 37,
        "E": 23,
        "N": 0,
        "R": 0,
        "S": 0,
    }
    changed = [row for row in merged if row != before[row["item_id"]]]
    assert len(changed) == 10
    assert all(row["reviewer"] == "Codex 재생성 검수" for row in changed)
    assert all(row["decision"] in {"A", "E"} for row in changed)
    assert all(row["edited_answer"].strip() for row in merged if row["decision"] == "E")
