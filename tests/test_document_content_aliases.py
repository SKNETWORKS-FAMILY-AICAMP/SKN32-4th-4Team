from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from scripts.index import build_clause_index
from scripts.index.load_precomputed import _drop_alias_only_vectors
from app.adapters.document_content_aliases import (
    ContentAliasError,
    ensure_canonicals_present,
    load,
    prune_occurrences,
)


ALIAS = "2660c88b05cc714dbf9e44cd7de3f1cc0d228a61a7c5d469118a7b6a9d5a99c2"
CANONICAL = "36787f5570ba48b55f6c369add07772d0b43bd4883ac74932da245aa8778d04b"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _doc(sha256: str, text: str) -> dict:
    return {
        "parse_status": "ok",
        "source": {"sha256": sha256, "insurer": "DB손해보험"},
        "clauses": [
            {
                "content_hash": sha256[:16],
                "text": text,
                "qualified_no": "보통약관/제1조",
                "citation_eligible": True,
                "chunk_type": "clause",
                "statute": False,
                "locator": {"page_from": 1, "page_to": 1},
            }
        ],
        "annexes": [],
    }


def test_repository_alias_ledger_keeps_only_confirmed_canonical():
    aliases = load()

    assert aliases == {ALIAS: CANONICAL}


def test_alias_ledger_rejects_unconfirmed_canonical(tmp_path):
    aliases = tmp_path / "aliases.jsonl"
    confirmed = tmp_path / "confirmed.jsonl"
    _write_jsonl(
        aliases,
        [
            {
                "alias_sha256": ALIAS,
                "canonical_sha256": CANONICAL,
                "relation": "same_legal_content_reprint",
                "status": "confirmed",
            }
        ],
    )
    _write_jsonl(confirmed, [])

    with pytest.raises(ContentAliasError, match="대표본이 확정 문서 원장에 없습니다"):
        load(aliases, confirmed_path=confirmed)


def test_alias_generation_requires_canonical_when_alias_is_present():
    with pytest.raises(ContentAliasError, match="대표본 산출물이 없습니다"):
        ensure_canonicals_present(
            {ALIAS: CANONICAL},
            {ALIAS},
            context="test generation",
        )


def test_clause_collector_excludes_alias_and_keeps_canonical(monkeypatch):
    docs = [
        (Path("alias.clauses.json"), _doc(ALIAS, "별칭 본문")),
        (Path("canonical.clauses.json"), _doc(CANONICAL, "대표 본문")),
    ]
    monkeypatch.setattr(build_clause_index, "_iter_docs", lambda limit, tag: docs)

    texts, occurrences, _demotions, report = build_clause_index._collect(
        None,
        False,
        "s7_hybrid-table-v1",
        aliases={ALIAS: CANONICAL},
    )

    assert set(texts) == {CANONICAL[:16]}
    assert {row[1] for row in occurrences} == {CANONICAL}
    assert "문서 content_alias 1" in report


def test_clause_collector_limit_counts_non_alias_documents(monkeypatch):
    docs = [
        (Path("alias.clauses.json"), _doc(ALIAS, "별칭 본문")),
        (Path("canonical.clauses.json"), _doc(CANONICAL, "대표 본문")),
    ]
    monkeypatch.setattr(build_clause_index, "_iter_docs", lambda limit, tag: docs)

    _, occurrences, _, _ = build_clause_index._collect(
        1,
        False,
        "s7_hybrid-table-v1",
        aliases={ALIAS: CANONICAL},
    )

    assert {row[1] for row in occurrences} == {CANONICAL}


def test_clause_collector_fails_if_alias_exists_without_canonical(monkeypatch):
    docs = [(Path("alias.clauses.json"), _doc(ALIAS, "별칭 본문"))]
    monkeypatch.setattr(build_clause_index, "_iter_docs", lambda limit, tag: docs)

    with pytest.raises(RuntimeError, match="대표본 산출물이 없습니다"):
        build_clause_index._collect(
            None,
            False,
            "s7_hybrid-table-v1",
            aliases={ALIAS: CANONICAL},
        )


class _Cursor:
    rowcount = 7

    def __init__(self):
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, sql, params):
        self.executed = (sql, params)


class _Conn:
    def __init__(self):
        self.cur = _Cursor()
        self.commits = 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


def test_prune_occurrences_is_scoped_to_alias_and_generation():
    conn = _Conn()

    removed = prune_occurrences(
        conn,
        {ALIAS: CANONICAL},
        generation="s7_hybrid-table-v1",
    )

    assert removed == 7
    assert conn.cur.executed is not None
    sql, params = conn.cur.executed
    assert "sha256 = ANY(%s)" in sql
    assert "index_generation = %s" in sql
    assert params == ([ALIAS], "s7_hybrid-table-v1")
    assert conn.commits == 1


def test_precomputed_loader_drops_every_chunk_of_alias_only_content():
    hashes = ["shared", "alias-only", "alias-only"]
    seqs = [0, 0, 1]
    nchunks = [1, 2, 2]
    texts = ["대표본에도 있음", "별칭 조각 1", "별칭 조각 2"]
    vecs = np.asarray([[1.0], [2.0], [3.0]], dtype=np.float32)

    got = _drop_alias_only_vectors(
        hashes, seqs, nchunks, texts, vecs, {"alias-only"}
    )

    assert got[0] == ["shared"]
    assert got[1] == [0]
    assert got[2] == [1]
    assert got[3] == ["대표본에도 있음"]
    assert got[4].tolist() == [[1.0]]
