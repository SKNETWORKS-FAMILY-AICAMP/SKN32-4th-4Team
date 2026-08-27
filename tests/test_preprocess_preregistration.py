"""D4 prospective 사전등록의 fail-closed/mutation 회귀 테스트."""
from __future__ import annotations

import copy
import json

import pytest

from scripts.verify import verify_preprocess_preregistration as V
from scripts.extract import build_manifest


def _registry() -> dict:
    return json.loads(V.DEFAULT_REGISTRY.read_text(encoding="utf-8"))


def _machine_results() -> dict:
    return {
        "document_count": 1367,
        "manifest_mismatch_count": 0,
        "reference_confirmed_violation_count": 0,
        "candidate_serving_leak_count": 0,
        "candidate_citation_leak_count": 0,
        "d6_killed_mutants": sorted(V.REQUIRED_MUTANTS),
    }


def _external_summary() -> dict:
    return {
        "d5_human_table_quality": {
            "status": "passed",
            "human_reviewed": True,
            "adjudication_complete": True,
            "strata": [
                {"precision": 0.95, "recall": 0.94, "f1": 0.945, "ci95": [0.9, 0.99]}
            ],
        },
        "quality_threshold_binding": {
            "status": "passed",
            "thresholds": {
                "word_coverage": {"operator": "gte", "value": 0.9},
                "cell_mislink_rate": {"operator": "lte", "value": 0.05},
                "sentence_cut_rate": {"operator": "lte", "value": 0.05},
            },
        },
        "quality_metrics": {
            "word_coverage": 0.91,
            "cell_mislink_rate": 0.04,
            "sentence_cut_rate": 0.03,
        },
        "human_release_approval": {"approved": True, "approved_by": "test-reviewer"},
    }


def test_repository_registry_and_byte_sidecar_are_valid():
    registry, errors = V.load_registry()

    assert registry["policy_id"] == "preprocess-d4-prospective-v1"
    assert errors == []


def test_current_s7_is_explicitly_not_retroactively_eligible():
    assert V.generation_status(_registry(), "s7_hybrid-table-v1") == "not_eligible_retroactively"


def test_preregistration_registry_is_not_an_extraction_input():
    assert "preprocess_quality_preregistration.json" not in build_manifest.config_state()


def test_registry_sidecar_detects_one_byte_mutation(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(V.DEFAULT_REGISTRY.read_text(encoding="utf-8"), encoding="utf-8")
    V.sidecar_path(registry).write_text(V.sha256_file(registry) + "\n", encoding="ascii")
    registry.write_text(registry.read_text(encoding="utf-8") + " ", encoding="utf-8")

    _, errors = V.load_registry(registry)

    assert any("지문 불일치" in error for error in errors)


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("document_count", 1366),
        ("manifest_mismatch_count", 1),
        ("reference_confirmed_violation_count", 1),
        ("candidate_serving_leak_count", 1),
        ("candidate_citation_leak_count", 1),
    ],
)
def test_each_machine_contract_mutation_fails_closed(field, bad_value):
    results = _machine_results()
    results[field] = bad_value

    errors = V.evaluate_machine_gates(_registry(), results)

    assert any(field in error for error in errors)


def test_missing_machine_evidence_fails_closed():
    results = _machine_results()
    del results["manifest_mismatch_count"]

    assert any("누락(fail-closed)" in error for error in V.evaluate_machine_gates(_registry(), results))


def test_each_required_d6_mutant_must_be_killed():
    for mutant in V.REQUIRED_MUTANTS:
        results = _machine_results()
        results["d6_killed_mutants"].remove(mutant)

        errors = V.evaluate_machine_gates(_registry(), results)

        assert any(mutant in error for error in errors)


def test_external_human_and_threshold_evidence_are_required():
    errors = V.evaluate_external_summary(_registry(), {})

    assert any("D5" in error and "required_external" in error for error in errors)
    assert any("binding" in error and "required_external" in error for error in errors)
    assert any("release 승인" in error for error in errors)


def test_complete_synthetic_external_summary_passes_contract():
    assert V.evaluate_external_summary(_registry(), _external_summary()) == []


def test_manifest_outputs_are_rehashed_instead_of_trusting_claimed_zero(tmp_path):
    output = tmp_path / "out.json"
    output.write_text('{"ok":true}', encoding="utf-8")
    manifest = {
        "documents": [
            {
                "sha12": "abc123def456",
                "outputs": {
                    "structured/s8": {
                        "path": "out.json",
                        "sha256": V.sha256_file(output),
                    }
                },
            }
        ],
        "config": {},
    }
    assert V.count_manifest_mismatches(manifest, tmp_path) == 0

    output.write_text('{"ok":false}', encoding="utf-8")

    assert V.count_manifest_mismatches(manifest, tmp_path) == 1


@pytest.mark.parametrize(
    ("metric", "bad_value"),
    [
        ("word_coverage", 0.89),
        ("cell_mislink_rate", 0.06),
        ("sentence_cut_rate", 0.06),
    ],
)
def test_quality_metric_mutations_fail_against_prior_binding(metric, bad_value):
    evidence = copy.deepcopy(_external_summary())
    evidence["quality_metrics"][metric] = bad_value

    errors = V.evaluate_external_summary(_registry(), evidence)

    assert any(metric in error and "기준 미달" in error for error in errors)
