from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapters import file_glossary_source
from scripts.export import build_dataset
from scripts.extract import build_glossary
from app.adapters import document_content_aliases
from scripts.index.load_s7_1_approved_facts import _reject_content_alias_occurrences


ALIAS = "2660c88b05cc714dbf9e44cd7de3f1cc0d228a61a7c5d469118a7b6a9d5a99c2"
CANONICAL = "36787f5570ba48b55f6c369add07772d0b43bd4883ac74932da245aa8778d04b"


def _doc(sha256: str, text: str) -> dict:
    return {
        "parse_status": "ok",
        "source": {"sha256": sha256, "insurer": "DB손해보험"},
        "clauses": [
            {
                "ordinal": 1,
                "qualified_no": "보통약관/제1조",
                "section": "보통약관",
                "title": "용어의 정의",
                "text": text,
                "content_hash": sha256[:16],
                "citation_eligible": True,
                "chunk_type": "clause",
                "statute": False,
                "locator": {"page_from": 1, "page_to": 1},
            }
        ],
        "annexes": [],
        "candidate_facts": [],
    }


def _write_doc(root: Path, tag: str, sha256: str, text: str) -> None:
    path = root / "dbins" / tag / f"{sha256[:12]}.clauses.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_doc(sha256, text), ensure_ascii=False), encoding="utf-8")


def test_glossary_builder_excludes_content_alias(tmp_path, monkeypatch):
    structured = tmp_path / "structured"
    output = tmp_path / "glossary" / "passages.jsonl"
    _write_doc(structured, "s5_test", ALIAS, "별칭 용어 본문")
    _write_doc(structured, "s5_test", CANONICAL, "대표 용어 본문")
    monkeypatch.setattr(build_glossary, "_STRUCT", structured)
    monkeypatch.setattr(build_glossary, "_OUT", output)
    monkeypatch.setattr(document_content_aliases, "load", lambda: {ALIAS: CANONICAL})

    assert build_glossary.main() == 0

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    meta = json.loads(output.with_name("meta.json").read_text(encoding="utf-8"))
    assert {row["sha256"] for row in rows} == {CANONICAL}
    assert meta["source_documents"] == 2
    assert meta["documents"] == 1
    assert meta["content_alias_documents_skipped"] == 1


def test_runtime_glossary_loader_filters_stale_alias_artifact(tmp_path, monkeypatch):
    passages = tmp_path / "passages.jsonl"
    meta = tmp_path / "meta.json"
    accepted = tmp_path / "accepted_extraction.json"
    passages.write_text(
        "\n".join(
            json.dumps(row, ensure_ascii=False)
            for row in (
                {"kind": "clause", "sha256": ALIAS, "text": "별칭 구절"},
                {"kind": "clause", "sha256": CANONICAL, "text": "대표 구절"},
            )
        )
        + "\n",
        encoding="utf-8",
    )
    meta.write_text("{}", encoding="utf-8")
    accepted.write_text('{"supplemental_facts":""}', encoding="utf-8")
    monkeypatch.setattr(file_glossary_source, "_PASSAGES", passages)
    monkeypatch.setattr(file_glossary_source, "_META", meta)
    monkeypatch.setattr(file_glossary_source, "_ACCEPTED_RELEASE", accepted)
    monkeypatch.setattr(
        file_glossary_source,
        "_s7_paths",
        lambda: tuple(tmp_path / name for name in ("facts", "chunks", "occurrences")),
    )
    monkeypatch.setattr(
        document_content_aliases,
        "load",
        lambda **_kwargs: {ALIAS: CANONICAL},
    )
    file_glossary_source._reset_for_tests()

    rows = file_glossary_source._load()

    assert {row.sha256 for row in rows} == {CANONICAL}
    file_glossary_source._reset_for_tests()


def test_dataset_exporter_excludes_alias_occurrences_and_contents(tmp_path, monkeypatch):
    structured = tmp_path / "structured"
    output = tmp_path / "dataset"
    monkeypatch.setattr(build_dataset, "_ROOT", tmp_path)
    monkeypatch.setattr(build_dataset, "_STRUCT", structured)
    monkeypatch.setattr(document_content_aliases, "load", lambda: {ALIAS: CANONICAL})
    _write_doc(structured, "s7_test", ALIAS, "별칭 데이터셋 본문")
    _write_doc(structured, "s7_test", CANONICAL, "대표 데이터셋 본문")

    assert build_dataset.main(["--clause-tag", "s7_test", "--out", str(output)]) == 0

    occurrences = [
        json.loads(line)
        for line in (output / "occurrences.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    contents = [
        json.loads(line)
        for line in (output / "clauses.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert {row["sha256"] for row in occurrences} == {CANONICAL}
    assert {row["content_hash"] for row in contents} == {CANONICAL[:16]}
    assert manifest["source_documents"] == 2
    assert manifest["documents"] == 1
    assert manifest["content_alias_documents_skipped"] == 1


def test_s7_approved_fact_loader_rejects_alias_occurrence():
    with pytest.raises(SystemExit, match="동일 원문 별칭 문서"):
        _reject_content_alias_occurrences(
            [{"sha12": ALIAS[:12]}],
            {ALIAS[:12]: ALIAS},
            {ALIAS: CANONICAL},
        )
