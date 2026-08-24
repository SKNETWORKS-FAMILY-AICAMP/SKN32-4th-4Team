import json

import pytest

from scripts.index.s7_arctic_embed import EmbedError, prepare


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_prepare_uses_only_exact_eligible_content_set(tmp_path):
    clauses = tmp_path / "clauses.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    _write_jsonl(
        clauses,
        [
            {"content_hash": "a" * 64, "text": "a", "has_eligible": True},
            {"content_hash": "b" * 64, "text": "b", "has_eligible": False},
        ],
    )
    _write_jsonl(
        chunks,
        [
            {"content_hash": "a" * 64, "seq": 0, "n_chunks": 2, "text": "a0"},
            {"content_hash": "a" * 64, "seq": 1, "n_chunks": 2, "text": "a1"},
        ],
    )

    result = prepare(clauses=clauses, chunks=chunks, output_dir=tmp_path / "out", shards=2)

    assert result["eligible_contents"] == 1
    assert result["chunks"] == 2
    assert result["shard_counts"] == [1, 1]
    assert result["candidate_facts_included"] is False


def test_prepare_rejects_content_set_drift(tmp_path):
    clauses = tmp_path / "clauses.jsonl"
    chunks = tmp_path / "chunks.jsonl"
    _write_jsonl(clauses, [{"content_hash": "a" * 64, "text": "a", "has_eligible": True}])
    _write_jsonl(
        chunks,
        [{"content_hash": "b" * 64, "seq": 0, "n_chunks": 1, "text": "b"}],
    )

    with pytest.raises(EmbedError, match="eligible/chunk content hash sets differ"):
        prepare(clauses=clauses, chunks=chunks, output_dir=tmp_path / "out", shards=1)
