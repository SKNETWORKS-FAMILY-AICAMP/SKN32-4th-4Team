from pathlib import Path

from app.adapters import file_clause_store
from scripts.index import build_clause_index


def _candidate_only_document():
    return {
        "parse_status": "ok",
        "source": {"sha256": "a" * 64, "insurer": "test"},
        "clauses": [],
        "annexes": [],
        "candidate_facts": [
            {
                "candidate_id": "sha256:" + "b" * 64,
                "approval": "candidate",
                "serving_eligible": False,
                "citation_eligible": False,
                "text": "must never be indexed or served",
            }
        ],
    }


def test_clause_index_collector_ignores_candidate_facts(monkeypatch):
    document = _candidate_only_document()
    monkeypatch.setattr(
        build_clause_index,
        "_iter_docs",
        lambda limit, tag: [(Path("candidate.clauses.json"), document)],
    )

    texts, occurrences, report = build_clause_index._collect(None, False, "s7_hybrid-table-v1")

    assert texts == {}
    assert occurrences == []
    assert "조항 등장 0" in report


def test_file_clause_store_ignores_candidate_facts(monkeypatch):
    monkeypatch.setattr(file_clause_store, "_load_doc", lambda sha256: _candidate_only_document())

    assert file_clause_store.load_clauses("a" * 64, usable_only=False) == []
