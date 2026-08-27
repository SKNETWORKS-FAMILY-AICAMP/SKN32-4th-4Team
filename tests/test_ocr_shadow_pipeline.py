import json
import sys

import pytest

from scripts.eval.ocr_sota5_runner import main as runner_main
from scripts.eval.prepare_ocr_shadow48 import generation_candidate, page_category
from scripts.eval.selfpay_axis_binder import valid_normalized_bbox


@pytest.mark.parametrize(
    ("sale_start", "expected"),
    [
        ("20090930", 1),
        ("20091001", 2),
        ("20170401", 3),
        ("20210701", 4),
        ("20260506", 5),
        ("", None),
    ],
)
def test_generation_candidate_uses_profile_boundaries(sale_start, expected):
    assert generation_candidate(sale_start) == expected


@pytest.mark.parametrize(
    ("bbox", "expected"),
    [
        ([0.1, 0.2, 0.9, 0.8], True),
        ([0, 0, 0, 0], False),
        ([10, 20, 90, 80], False),
        ([0.1, 0.2, 0.9], False),
        (None, False),
    ],
)
def test_bbox_contract_requires_normalized_positive_area(bbox, expected):
    assert valid_normalized_bbox(bbox) is expected


def test_runner_rejects_missing_manifest_when_config_has_no_image_sha(tmp_path, monkeypatch):
    config = {
        "models": [
            {
                "slug": "mineru",
                "model_id": "unused",
                "adapter": "mineru",
                "max_new_tokens": 1,
            }
        ],
        "samples": [{"id": "sample"}],
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ocr_sota5_runner.py",
            "--config",
            str(config_path),
            "--model",
            "mineru",
            "--input-dir",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )
    with pytest.raises(SystemExit, match="manifest is required"):
        runner_main()


def test_page_with_legacy_table_is_accepted_even_without_coordinate_candidates():
    assert page_category({"tables": [[["구분", "금액"]]], "tables_coords": []}) == "accepted"


def test_page_without_any_table_output_is_missed():
    assert page_category({"tables": [], "tables_coords": []}) == "missed"


def test_rejected_coordinate_candidate_is_withheld():
    assert page_category(
        {"tables": [], "tables_coords": [{"is_table": False, "method": "2열짝짓기"}]}
    ) == "withheld"
