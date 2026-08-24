import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _rows(path: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (ROOT / path).read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def test_s7_fact_review_is_complete_and_quarantines_fix_patterns():
    labels = _rows("data/eval/s7_fact_signature_labels_20260804.jsonl")
    assert len(labels) == 29
    assert len({row["signature_id"] for row in labels}) == 29
    assert sum(row["label"] == "approve" for row in labels) == 24
    assert sum(row["label"] == "fix" for row in labels) == 5
    assert sum(row["facts"] for row in labels if row["label"] == "approve") == 850
    assert sum(row["facts"] for row in labels if row["label"] == "fix") == 216


def test_materialized_s7_1_facts_are_approved_and_have_citation_locators():
    base = ROOT / "data/work/s7_1_approved_facts"
    manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    facts = _rows("data/work/s7_1_approved_facts/approved_facts.jsonl")
    chunks = _rows("data/work/s7_1_approved_facts/chunks.jsonl")
    occurrences = _rows("data/work/s7_1_approved_facts/occurrences.jsonl")
    assert manifest["counts"] == {
        "reviewed_patterns": 29,
        "approved_patterns": 24,
        "quarantined_patterns": 5,
        "approved_facts": 850,
        "approved_contents": 75,
        "occurrences": 850,
    }
    assert len(facts) == len(occurrences) == 850
    assert len(chunks) == 75
    assert all(row["approval"] == "human_pattern_approved" for row in facts)
    assert all(row["serving_eligible"] and row["citation_eligible"] for row in facts)
    assert all(row["sha12"] and row["page_from"] and row["table_bbox"] and row["image_sha256"]
               for row in occurrences)


def test_human_table_review_resolves_check_and_expands_true_labels():
    reviews = _rows("data/eval/human_table_labels_20260804.jsonl")
    merged = _rows("data/eval/table_labels_s7_human_20260804.jsonl")
    data = [row for row in merged if not row.get("_meta")]
    assert len(reviews) == 68
    assert all(row["label"] in {"prose", "broken"} for row in reviews)
    assert len(data) == 120
    assert sum(row["label"] == "true" for row in data) == 37
    assert sum(row["label"] == "false" for row in data) == 83
    assert not any(row["label"] == "check" for row in data)


def test_s7_1_candidates_reach_reranker_input():
    chunks = _rows("data/work/s7_1_approved_facts/chunks.jsonl")
    approved_hashes = {row["content_hash"] for row in chunks}
    payload = json.loads((ROOT / "data/eval/s7_1_arctic_ko_top20_rerank.json").read_text(encoding="utf-8"))
    pairs = [
        (record["query_id"], candidate["content_hash"])
        for record in payload["records"]
        for candidate in record["candidates"]
        if candidate["content_hash"] in approved_hashes
    ]
    assert payload["candidate_facts_included"] is True
    assert payload["scope"]["supplemental_occurrences"] == 850
    assert len(pairs) == 23
    assert len({query_id for query_id, _ in pairs}) == 6


def test_accepted_release_points_to_verified_s7_1_supplement():
    accepted = json.loads((ROOT / "config/accepted_extraction.json").read_text(encoding="utf-8"))
    supplement_path = ROOT / accepted["supplemental_facts"]
    supplement = json.loads(supplement_path.read_text(encoding="utf-8"))
    assert accepted["release_id"] == "r2026-08-04-clause-s7.1-arctic-ko-ocr-approved"
    assert supplement["release_state"] == "accepted"
    assert supplement["serving_eligible"] is True
    assert supplement["approval"]["approved_facts"] == 850
    assert supplement["approval"]["quarantined_facts"] == 216
    assert supplement["database_verification"] == {
        "loaded_chunks": 75,
        "loaded_occurrences": 850,
        "loaded_documents": 179,
        "rank1_same_vector_probe": True,
        "api_ready": True,
    }
