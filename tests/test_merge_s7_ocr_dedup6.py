from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.eval.merge_s7_ocr_dedup6 import (
    DEVICE_RESULT_DIRS,
    MergeError,
    merge,
)


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path, list[Path], list[dict]]:
    allocation = tmp_path / "allocation"
    results = tmp_path / "results"
    image_shas = [f"{index:064x}" for index in range(1, 7)]
    samples = [
        {"id": f"doc_p{index:04d}", "image_sha256": image_shas[index], "insurer": "i", "sha12": "doc", "page_1based": index + 1}
        for index in range(6)
    ]
    samples.append(
        {"id": "alias_p0007", "image_sha256": image_shas[0], "insurer": "i", "sha12": "alias", "page_1based": 7}
    )
    hard_paths = [tmp_path / "hard0.json", tmp_path / "hard1.json"]
    _write(hard_paths[0], {"schema_version": "1", "samples": samples[:4]})
    _write(hard_paths[1], {"schema_version": "1", "samples": samples[4:]})

    groups = []
    for index, (device, dirname) in enumerate(DEVICE_RESULT_DIRS.items()):
        sample = samples[index]
        _write(allocation / device / "manifest.json", {"samples": [sample]})
        result = {
            "sample_id": sample["id"],
            "image": f"{sample['id']}.png",
            "expected_image_sha256": sample["image_sha256"],
            "image_sha256": sample["image_sha256"],
            "status": "success",
            "structured": [],
        }
        _write(results / dirname / f"{sample['id']}.json", result)
        members = [sample["id"]]
        if index == 0:
            members.append(samples[-1]["id"])
        groups.append(
            {
                "image_sha256": sample["image_sha256"],
                "representative_id": sample["id"],
                "member_ids": members,
            }
        )
    _write(
        allocation / "alias_map.json",
        {
            "unique_images": 6,
            "occurrences": 7,
            "saved_inferences": 1,
            "groups": groups,
        },
    )
    return allocation, results, hard_paths, samples


def test_merge_restores_only_exact_image_aliases(tmp_path: Path):
    allocation, results, hard_paths, samples = _fixture(tmp_path)
    output = tmp_path / "merged"
    summary = merge(
        allocation_root=allocation,
        results_root=results,
        hard_manifests=hard_paths,
        output_root=output,
        model_revision="a" * 40,
    )
    assert summary["unique_results"] == 6
    assert summary["occurrence_results"] == 7
    assert summary["expanded_aliases"] == 1
    assert summary["model_revision"] == "a" * 40
    restored = json.loads(
        (output / "expanded" / f"{samples[-1]['id']}.json").read_text("utf-8")
    )
    assert restored["sample_id"] == samples[-1]["id"]
    assert restored["image_sha256"] == samples[0]["image_sha256"]
    assert restored["exact_image_alias"]["representative_id"] == samples[0]["id"]
    assert restored["exact_image_alias"]["expanded"] is True
    assert restored["model_revision"] == "a" * 40
    assert restored["model_revision_provenance"]["source"] == "huggingface_cache_ref"


def test_merge_rejects_alias_with_different_image_sha(tmp_path: Path):
    allocation, results, hard_paths, samples = _fixture(tmp_path)
    hard1 = json.loads(hard_paths[1].read_text("utf-8"))
    hard1["samples"][-1]["image_sha256"] = "f" * 64
    _write(hard_paths[1], hard1)
    with pytest.raises(MergeError, match="alias member image SHA mismatch"):
        merge(
            allocation_root=allocation,
            results_root=results,
            hard_manifests=hard_paths,
            output_root=tmp_path / "merged",
        )
