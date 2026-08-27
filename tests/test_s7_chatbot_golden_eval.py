from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.eval.s7_chatbot_golden import canonical_sha256, evaluate_run, evaluate_suite

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests/golden/s7_chatbot_grounded_v1.json"
NEGATIVE = ROOT / "tests/golden/s7_chatbot_negative_v1.json"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_twelve_cases_and_three_repeats_pass():
    fixture = _load(GOLDEN)
    result = evaluate_suite(fixture)
    assert len(fixture["cases"]) == 12
    assert {len(case["responses"]) for case in fixture["cases"]} == {3}
    assert result["summary"] == {"passed": 12, "failed": 0, "total": 12}
    assert result["fixture_sha256"] == canonical_sha256(fixture)
    assert fixture["expected_release"] == {
        "fingerprint": "37ccfe9e1317da2b8323ecb37eb58ef43cdf1922ef304d0dfd2e22ce10c727de",
        "occurrence_count": 850,
    }


def test_expected_release_is_pinned_to_packaged_approved_metadata():
    fixture = _load(GOLDEN)
    accepted = _load(ROOT / "config/accepted_extraction.json")
    supplemental = _load(ROOT / accepted["supplemental_facts"])
    assert fixture["expected_release"] == {
        "fingerprint": supplemental["provenance"]["fact_manifest_sha256"],
        "occurrence_count": supplemental["materialized"]["occurrences"],
    }


@pytest.mark.parametrize("mutant", _load(NEGATIVE)["mutants"], ids=lambda item: item["id"])
def test_negative_fixture_fails_for_exact_reason(mutant):
    case = copy.deepcopy(_load(GOLDEN)["cases"][mutant["case_index"]])
    case["execution"]["release"].update(mutant.get("release_patch", {}))
    failures = evaluate_run(case, mutant["response"], _load(GOLDEN)["expected_release"])
    assert [failure["code"] for failure in failures] == mutant["expected_failure_codes"]


def test_numeric_occurrences_are_multiset_checked():
    case = copy.deepcopy(_load(GOLDEN)["cases"][0])
    response = copy.deepcopy(case["responses"][0])
    response["message"] += " 추가 1만원"
    assert [f["code"] for f in evaluate_run(case, response)] == ["unsupported_numeric_token"]


def test_metadata_requires_exact_used_provider_model_triplet():
    case = copy.deepcopy(_load(GOLDEN)["cases"][0])
    response = copy.deepcopy(case["responses"][0])
    response["llm"] = {"used": True, "provider": "openai", "model": "wrong"}
    assert [f["code"] for f in evaluate_run(case, response)] == ["llm_metadata_mismatch"]
