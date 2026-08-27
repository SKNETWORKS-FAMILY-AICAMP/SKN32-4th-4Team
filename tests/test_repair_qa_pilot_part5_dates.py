from __future__ import annotations

from scripts.finetune.build_qa_pilot import _incident_after
from scripts.review.repair_qa_pilot_part5_dates import _question


def test_incident_rule_is_after_enrollment_and_not_after_cutoff() -> None:
    cases = {
        "20250215": "20260215",
        "20250515": "20260515",
        "20250815": "20260815",
        "20260215": "20260826",
        "20260815": "20260826",
    }
    for enrolled, expected in cases.items():
        actual = _incident_after(enrolled)
        assert actual == expected
        assert enrolled < actual <= "20260826"


def test_repaired_question_contains_the_new_incident_date() -> None:
    question = _question(
        {
            "insurer": "현대해상",
            "product_name": "테스트 상품",
            "enrolled_on": "20250815",
            "incident_on": "20260815",
            "condition_text": "우울증으로 입원 치료를 받았습니다",
            "kcd_codes": ["F32"],
        }
    )

    assert "2025년 8월에 가입" in question
    assert "2026년 8월 15일에 우울증" in question
    assert "(F32)" in question
