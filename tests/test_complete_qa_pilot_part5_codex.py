from __future__ import annotations

import json
import pathlib

import pytest

from scripts.review import complete_qa_pilot_part5_codex as review


pytestmark = pytest.mark.skip(
    reason=(
        "재생성 전 part5.html의 날짜 오류 10건을 고정한 역사 테스트입니다. "
        "최신 300건 재감사는 test_reaudit_qa_pilot_part5.py가 담당합니다."
    )
)


def test_part5_review_covers_every_item_once() -> None:
    items = review._load_items(review.DEFAULT_INPUT)
    rows = [review._review(item) for item in items]

    assert len(rows) == 60
    assert len({row["item_id"] for row in rows}) == 60
    assert {key: sum(row["decision"] == key for row in rows) for key in "AENRS"} == {
        "A": 33,
        "E": 17,
        "N": 0,
        "R": 10,
        "S": 0,
    }
    assert all(row["edited_answer"].strip() for row in rows if row["decision"] == "E")


def test_rejected_items_are_only_incident_before_enrollment() -> None:
    items = review._load_items(review.DEFAULT_INPUT)
    by_id = {item["item_id"]: item for item in items}
    rejected = [review._review(item) for item in items if review._review(item)["decision"] == "R"]

    assert len(rejected) == 10
    for row in rejected:
        item = by_id[row["item_id"]]
        request = item["request"]
        assert review._date(request["incident_on"]) < review._date(request["enrolled_on"])
        assert row["reason"] == "질문 자체가 잘못됐다"


def test_human_html_contains_only_rejected_items() -> None:
    items = review._load_items(review.DEFAULT_INPUT)
    rows = [review._review(item) for item in items]
    by_id = {row["item_id"]: row for row in rows}
    page = review._human_html(items, by_id)

    assert page.count('"item_id":') == 10
    assert "사고일 수정 후 재생성" in page
    assert "후보 제외 승인" in page
    assert "날짜를 추측하면 안 됩니다" in page
    for row in rows:
        if row["decision"] == "R":
            assert json.dumps(row["item_id"], ensure_ascii=False) in page
