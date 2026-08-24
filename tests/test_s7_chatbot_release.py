from __future__ import annotations

import json

import pytest

from app.adapters import file_glossary_source as source
from app.core.errors import InfraError
from app.core.usecases import glossary


@pytest.fixture(autouse=True)
def _restore_glossary_cache():
    """★모듈 전역 캐시를 **끝나고도** 비운다.

    `monkeypatch` 는 `_PASSAGES`·`_META`·환경변수를 되돌려 주지만
    `source._cache` 는 모듈 전역이라 되돌리지 않는다.
    시작 전에만 비웠더니 tmp 픽스처의 가짜 1행이 캐시에 남아,
    뒤에 도는 `tests/test_terms_api.py::test_실제_색인이_있으면_동작한다` 가
    실제 색인(구절 2,739개)을 못 보고 `found=False` 로 깨졌다(2026-08-04 실측).
    단독 실행은 통과하고 전량 실행만 깨져서 원인이 늦게 잡힌다.
    """
    yield
    source._reset_for_tests()


def _jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _configure(monkeypatch, tmp_path):
    release = tmp_path / "accepted.json"
    release.write_text(
        json.dumps({"supplemental_facts": str(tmp_path / "accepted_s7.json")}),
        encoding="utf-8",
    )
    (tmp_path / "accepted_s7.json").write_text(
        json.dumps({
            "release_state": "accepted",
            "serving_eligible": True,
            "citation_eligible": True,
            "materialized": {"occurrences": 1},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(source, "_ACCEPTED_RELEASE", release)
    monkeypatch.setattr(source, "_PASSAGES", tmp_path / "missing-passages.jsonl")
    monkeypatch.setattr(source, "_META", tmp_path / "missing-meta.json")
    monkeypatch.setenv("S7_FACT_ROOT", str(tmp_path / "s7"))
    source._reset_for_tests()
    return tmp_path / "s7"


def test_partial_s7_bundle_fails_closed(monkeypatch, tmp_path):
    root = _configure(monkeypatch, tmp_path)
    root.mkdir()
    _jsonl(root / "approved_facts.jsonl", [])
    with pytest.raises(InfraError, match="일부만 배포"):
        source._load()


def test_only_approved_s7_facts_reach_chatbot(monkeypatch, tmp_path):
    root = _configure(monkeypatch, tmp_path)
    root.mkdir()
    approved = {"content_hash": "approved", "serving_eligible": True, "citation_eligible": True,
                "document_sha12": "a" * 12, "service": ["외래"], "plan": "표준형"}
    quarantined = {"content_hash": "quarantined", "serving_eligible": False, "citation_eligible": False}
    _jsonl(root / "approved_facts.jsonl", [approved, quarantined])
    _jsonl(root / "chunks.jsonl", [
        {"content_hash": "approved", "text": "검수 승인 자기부담금 표 사실"},
        {"content_hash": "quarantined", "text": "격리 후보"},
    ])
    _jsonl(root / "occurrences.jsonl", [
        {"content_hash": "approved", "insurer": "test", "page_from": 7},
        {"content_hash": "quarantined", "insurer": "test", "page_from": 8},
    ])
    rows = source._load()
    assert [row.content_hash for row in rows] == ["approved"]
    assert source.meta()["s7_approved_fact_passages"] == 1


def test_empty_but_complete_s7_bundle_fails_release_count(monkeypatch, tmp_path):
    root = _configure(monkeypatch, tmp_path)
    root.mkdir()
    for name in ("approved_facts.jsonl", "chunks.jsonl", "occurrences.jsonl"):
        _jsonl(root / name, [])

    with pytest.raises(InfraError, match="건수가 승인 릴리스와 일치하지"):
        source._load()


def test_s7_amount_fact_wins_for_approved_fact_term(monkeypatch, tmp_path):
    root = _configure(monkeypatch, tmp_path)
    root.mkdir()
    text = "통원 자기부담금은 1만원과 보장대상의료비의 20% 중 큰 금액입니다."
    _jsonl(root / "approved_facts.jsonl", [{
        "content_hash": "approved",
        "serving_eligible": True,
        "citation_eligible": True,
        "document_sha12": "7" * 12,
        "service": ["통원"],
        "plan": "표준형",
    }])
    _jsonl(root / "chunks.jsonl", [{"content_hash": "approved", "text": text}])
    _jsonl(root / "occurrences.jsonl", [{
        "content_hash": "approved", "insurer": "DB손해보험", "page_from": 7,
    }])
    _jsonl(source._PASSAGES, [{
        "kind": "appendix",
        "sha256": "1" * 64,
        "insurer": "DB손해보험",
        "qualified_no": "붙임1/용어의 정의",
        "title": "용어의 정의",
        "page_from": 53,
        "page_to": 53,
        "content_hash": "baseline-definition",
        "text": text,
    }])

    answer = glossary.explain("자기부담금", source=source, max_quotes=1)
    assert answer.found is True
    assert answer.quotes[0].kind == "s7_approved_fact"
    assert answer.quotes[0].sha256.startswith("7" * 12)


def test_s7_amount_table_does_not_replace_term_definition(monkeypatch, tmp_path):
    root = _configure(monkeypatch, tmp_path)
    root.mkdir()
    _jsonl(root / "approved_facts.jsonl", [{
        "content_hash": "amount",
        "serving_eligible": True,
        "citation_eligible": True,
        "document_sha12": "7" * 12,
        "service": ["통원"],
    }])
    _jsonl(root / "chunks.jsonl", [{
        "content_hash": "amount", "text": "통원 자기부담금은 1만원입니다.",
    }])
    _jsonl(root / "occurrences.jsonl", [{
        "content_hash": "amount", "insurer": "DB손해보험", "page_from": 7,
    }])
    _jsonl(source._PASSAGES, [{
        "kind": "appendix",
        "sha256": "1" * 64,
        "insurer": "DB손해보험",
        "qualified_no": "붙임1/용어의 정의",
        "title": "용어의 정의",
        "page_from": 53,
        "page_to": 53,
        "content_hash": "legacy-definition",
        "text": "통원은 의료기관에 입원하지 않고 의사의 관리하에 치료하는 것입니다.",
    }])

    answer = glossary.explain("통원", source=source, max_quotes=1)
    assert answer.found is True
    assert answer.quotes[0].kind == "appendix"


def test_s7_same_fact_from_different_insurers_keeps_both_quotes(monkeypatch, tmp_path):
    root = _configure(monkeypatch, tmp_path)
    (tmp_path / "accepted_s7.json").write_text(
        json.dumps({
            "release_state": "accepted",
            "serving_eligible": True,
            "citation_eligible": True,
            "materialized": {"occurrences": 2},
        }),
        encoding="utf-8",
    )
    root.mkdir()
    _jsonl(root / "approved_facts.jsonl", [{
        "content_hash": "amount",
        "serving_eligible": True,
        "citation_eligible": True,
        "document_sha12": "7" * 12,
        "service": ["통원"],
    }])
    _jsonl(root / "chunks.jsonl", [{
        "content_hash": "amount", "text": "통원 자기부담금은 1만원입니다.",
    }])
    _jsonl(root / "occurrences.jsonl", [
        {"content_hash": "amount", "insurer": "보험사A", "page_from": 7},
        {"content_hash": "amount", "insurer": "보험사B", "page_from": 9},
    ])

    answer = glossary.explain("자기부담금", source=source, max_quotes=3)
    assert {quote.insurer for quote in answer.quotes} == {"보험사A", "보험사B"}


def test_missing_release_config_does_not_silently_use_legacy(monkeypatch, tmp_path):
    monkeypatch.setattr(source, "_ACCEPTED_RELEASE", tmp_path / "missing-release.json")
    monkeypatch.setattr(source, "_PASSAGES", tmp_path / "passages.jsonl")
    monkeypatch.setenv("S7_FACT_ROOT", str(tmp_path / "missing-s7"))
    _jsonl(source._PASSAGES, [{"kind": "appendix", "text": "통원 정의"}])
    source._reset_for_tests()

    with pytest.raises(InfraError, match="승인 추출 릴리스 설정"):
        source._load()


def test_blank_s7_root_uses_release_default(monkeypatch, tmp_path):
    monkeypatch.setattr(source, "_S7_DEFAULT_DIR", tmp_path / "default-s7")
    monkeypatch.setenv("S7_FACT_ROOT", "")
    assert source._s7_dir() == tmp_path / "default-s7"
