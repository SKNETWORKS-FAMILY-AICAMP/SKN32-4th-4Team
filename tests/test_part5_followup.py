from __future__ import annotations

import pytest

from scripts.legal.build_part5_followup import PART5_IDS, REVIEW, build_payload, render_html


HUMAN_IDS = {
    "2011가합2760",
    "2015가단210771",
    "2019다267020",
    "2021나2046811",
    "2022다216688",
    "2023다283913",
    "case_128",
    "case_22",
    "case_4",
}


@pytest.fixture(scope="module")
def payload() -> dict:
    return build_payload()


def test_part5_review_covers_every_assigned_case_once() -> None:
    assert len(PART5_IDS) == 23
    assert len(set(PART5_IDS)) == 23
    assert set(REVIEW) == set(PART5_IDS)


def test_payload_only_sends_nine_cases_to_human_followup(payload: dict) -> None:
    assert payload["total_checked"] == 23
    assert payload["counts"] == {
        "codex_corrected": 7,
        "needs_source": 2,
        "codex_confirmed": 14,
    }
    assert {row["case_id"] for row in payload["human_items"]} == HUMAN_IDS
    assert len(payload["confirmed_items"]) == 14


def test_human_items_include_evidence_reason_and_concrete_question(payload: dict) -> None:
    for row in payload["human_items"]:
        assert row["raw_text"].strip()
        assert row["reason"].strip()
        assert row["human_question"].strip()
        assert row["proposed"]


def test_html_has_review_controls_links_and_safe_export_state(payload: dict) -> None:
    html = render_html(payload)

    assert "사람이 할 일은 9건뿐입니다" in html
    assert "공식 원문 열기" in html
    assert "수정안 승인" in html
    assert "현재 내용 유지" in html
    assert "추가 재검토" in html
    assert "자료 제외" in html
    assert "is_complete:missing.length===0" in html
    assert "part5_human_decisions.json" in html
    assert "localStorage" in html
