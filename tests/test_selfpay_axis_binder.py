from scripts.eval.selfpay_axis_binder import (
    bind_result,
    bind_table,
    expand_grid,
    grid_integrity,
    document_groups,
    parse_tables,
)


def _sample():
    return {
        "id": "sample",
        "insurer": "test",
        "sha12": "123456789abc",
        "page_1based": 26,
        "category": "withheld",
        "image_sha256": "a" * 64,
    }


def test_rowspan_is_expanded_into_amount_row():
    html = """
    <table>
      <tr><th>구분</th><th>항목</th><th>공제금액</th></tr>
      <tr><td rowspan="2">표준형</td><td>외래 의원</td><td>1만원과 20% 중 큰 금액</td></tr>
      <tr><td>처방조제 약국</td><td>8천원과 20% 중 큰 금액</td></tr>
    </table>
    """
    table = parse_tables(html)[0]
    grid = expand_grid(table)
    assert grid[2][0].text == "표준형"
    facts = bind_table(
        html=html,
        bbox=[0.1, 0.2, 0.9, 0.8],
        structured_index=0,
        sample=_sample(),
        parsed_table_index=0,
    )
    assert len(facts) == 2
    prescription = next(item for item in facts if "8천원" in item["amount_tokens"])
    assert prescription["plan"] == "표준형"
    assert "처방조제" in prescription["service"]
    assert prescription["institution"] == "처방조제 약국"
    assert prescription["serving_eligible"] is False
    assert prescription["source"]["grid_integrity"]["span_mismatch_cells"] == 0
    assert prescription["source"]["axis_binding"]["association_inferred"] is True


def test_page_boundary_missing_parent_is_not_inferred():
    html = """
    <table><tr><td>의원 외래</td><td>1만5천원과 자기부담금 20%</td></tr></table>
    """
    facts = bind_table(
        html=html,
        bbox=[0.1, 0.05, 0.9, 0.4],
        structured_index=0,
        sample=_sample(),
        parsed_table_index=0,
    )
    assert len(facts) == 1
    assert facts[0]["plan"] == ""
    assert facts[0]["inferred"] is False
    assert "page_boundary_continuation" in facts[0]["validation"]["reasons"]
    assert "missing_plan" in facts[0]["validation"]["reasons"]


def test_table_without_selfpay_marker_is_ignored():
    html = "<table><tr><td>가입금액</td><td>1억원</td></tr></table>"
    assert bind_table(
        html=html,
        bbox=[0.1, 0.2, 0.9, 0.8],
        structured_index=0,
        sample=_sample(),
        parsed_table_index=0,
    ) == []


def test_candidate_id_is_deterministic():
    html = "<table><tr><td>표준형 외래 의원</td><td>자기부담금 1만원</td></tr></table>"
    kwargs = dict(
        html=html,
        bbox=[0.1, 0.2, 0.9, 0.8],
        structured_index=0,
        sample=_sample(),
        parsed_table_index=0,
    )
    first = bind_table(**kwargs)
    second = bind_table(**kwargs)
    assert first[0]["candidate_id"] == second[0]["candidate_id"]


def test_selection_type_four_is_not_truncated_to_type_one():
    html = "<table><tr><td>선택형 IV 외래 의원</td><td>자기부담금 1만원</td></tr></table>"
    fact = bind_table(
        html=html,
        bbox=[0.1, 0.2, 0.9, 0.8],
        structured_index=0,
        sample=_sample(),
        parsed_table_index=0,
    )[0]
    assert fact["plan"].replace(" ", "") == "선택형IV"


def test_exact_image_alias_provenance_reaches_candidate():
    result = {
        "status": "success",
        "model_slug": "mineru_2_5_pro_2605",
        "model_id": "opendatalab/MinerU2.5-Pro-2605-1.2B",
        "model_revision": "revision",
        "runner_environment": {"torch": "test"},
        "image_sha256": "a" * 64,
        "exact_image_alias": {
            "rule": "identical rendered PNG SHA-256 only",
            "representative_id": "representative",
            "representative_device": "runpod1",
            "representative_result_sha256": "b" * 64,
            "expanded": True,
        },
        "structured": [
            {
                "type": "table",
                "bbox": [0.1, 0.2, 0.9, 0.8],
                "content": (
                    "<table><tr><td>표준형 외래 의원</td>"
                    "<td>자기부담금 1만원</td></tr></table>"
                ),
            }
        ],
    }

    fact = bind_result(result, _sample())[0]

    alias = fact["source"]["ocr"]["exact_image_alias"]
    assert alias["representative_id"] == "representative"
    assert alias["expanded"] is True


def test_ragged_expanded_grid_requires_review():
    html = (
        "<table><tr><td>표준형</td><td>공제금액</td></tr>"
        "<tr><td>외래 의원 자기부담금 1만원</td></tr></table>"
    )
    parsed = parse_tables(html)[0]
    integrity = grid_integrity(parsed, expand_grid(parsed))
    fact = bind_table(
        html=html,
        bbox=[0.1, 0.2, 0.9, 0.8],
        structured_index=0,
        sample=_sample(),
        parsed_table_index=0,
    )[0]

    assert integrity["ragged_rows"] == 1
    assert "ragged_expanded_grid" in fact["validation"]["reasons"]


def test_document_groups_preserve_sample_category():
    groups = document_groups(
        {
            "samples": [
                {
                    **_sample(),
                    "id": "sample-a",
                    "category": "missed",
                }
            ]
        }
    )

    assert groups[0]["category"] == "missed"
