from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.legal import review_legal_cases as review
from scripts.legal.legal_review_html import (
    build_review_items,
    render_assignment_markdown,
    render_review_html,
)


ROOT = Path(__file__).resolve().parents[1]
LEGAL = ROOT / "data" / "legal"


def _ledger() -> list[dict]:
    return [
        json.loads(line)
        for line in (LEGAL / "legal_case_normalized_final.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_real_review_dataset_is_complete() -> None:
    queue = json.loads((LEGAL / "human_review_queue.json").read_text(encoding="utf-8"))
    items = build_review_items(queue, _ledger(), LEGAL)

    assert len(items) == 114
    assert sum(item["source"] == "court" for item in items) == 33
    assert sum(item["source"] == "fss" for item in items) == 81
    assert all(item["case_id"] and item["raw_text"] for item in items)
    needs_source = [item["case_id"] for item in items if item["source_level"] == "원문 재확인 필요"]
    assert needs_source == ["case_145"]
    assert next(item for item in items if item["case_id"] == "case_145")["source_url"]

    expected = {
        1: (23, 7, 16),
        2: (23, 7, 16),
        3: (23, 7, 16),
        4: (22, 6, 16),
        5: (23, 6, 17),
    }
    for part, (total, court, fss) in expected.items():
        assigned = [item for item in items if item["review_part"] == part]
        assert len(assigned) == total
        assert sum(item["source"] == "court" for item in assigned) == court
        assert sum(item["source"] == "fss" for item in assigned) == fss

    assignment = render_assignment_markdown(items)
    assert assignment.count("## 1파트") == 1
    assert assignment.count("| `case_145` |") == 1

    laser_case = next(item for item in items if item["case_id"] == "2011나7162")
    assert "[판결문 앞부분 발췌]" in laser_case["raw_text"]
    assert "[판결 이유 발췌]" in laser_case["raw_text"]
    assert "원고는 신청을 취하해주면 보험금 전액을 지급하겠다고" in laser_case["raw_text"]
    assert "[판결문 앞부분 발췌]" in laser_case["raw_text"]
    assert "[판결 이유 발췌]" in laser_case["raw_text"]


def test_html_has_offline_review_safety_features() -> None:
    queue = json.loads((LEGAL / "human_review_queue.json").read_text(encoding="utf-8"))
    items = build_review_items(queue, _ledger(), LEGAL)
    output = render_review_html(items, queue)

    assert "판례·금감원 사람 검토" in output
    assert "localStorage" in output
    assert "human_review_queue.json" in output
    assert "팀원 JSON 합치기" in output
    assert "multiple" in output
    assert "판결문 앞부분 발췌" in output
    assert "담당 파트 필터" in output
    assert "수정 필요/제외" not in output  # 화면 문구는 사람이 읽기 쉬운 표현을 쓴다.
    assert output.count('"case_id"') >= 228  # items와 원본 queue에 각각 114건


def test_apply_rejects_corrected_without_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "ledger.jsonl"
    queue = tmp_path / "queue.json"
    ledger.write_text(
        json.dumps({"case": {"id": "C1"}, "verified_by": "unreviewed"}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    queue.write_text(
        json.dumps([{"case_id": "C1", "verdict": "corrected", "note": ""}], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(review, "_LEDGER", ledger)
    monkeypatch.setattr(review, "_QUEUE", queue)

    with pytest.raises(SystemExit, match="이유가 없는"):
        review.apply_review("검토자")
    assert json.loads(ledger.read_text(encoding="utf-8"))["verified_by"] == "unreviewed"
