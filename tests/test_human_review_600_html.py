from __future__ import annotations

from collections import Counter

from scripts.eval.build_human_review_600 import (
    _examples_html,
    _index_html,
    _outside_suggestion,
    _review_html,
    _summaries,
    assign_parts,
    build_items,
)


def test_review_600_counts_uniqueness_and_important_part() -> None:
    parts = assign_parts(build_items(embed_images=False))
    items = [row for part in parts for row in part]

    assert len(items) == 600
    assert len({row["review_id"] for row in items}) == 600
    assert Counter(row["source_type"] for row in items) == {
        "table_b": 65,
        "table_a": 60,
        "outside_clause": 440,
        "b8_disability": 26,
        "f4_interest": 9,
    }
    assert all(row["source_type"] not in {"b8_disability", "f4_interest"} for part in parts[:4] for row in part)
    assert all(row["source_type"] in {"b8_disability", "f4_interest"} for row in parts[4])
    assert sum(len(row.get("facts") or []) for row in parts[4]) == 228


def test_review_600_is_balanced_by_time_not_count() -> None:
    parts = assign_parts(build_items(embed_images=False))
    minutes = [sum(row["estimated_minutes"] for row in part) for part in parts]
    counts = [len(part) for part in parts]

    assert counts[4] == 35
    assert min(counts[:4]) >= 140
    assert max(minutes) - min(minutes) <= 10


def test_review_html_keeps_jsonl_newlines_as_javascript_escapes() -> None:
    parts = assign_parts(build_items(embed_images=False))
    rendered = _review_html(1, parts[0], _summaries(parts)[0])

    assert "join('\\n')+'\\n'" in rendered
    assert "split(/\\r?\\n/)" in rendered


def test_review_html_stores_machine_value_and_marks_selected_choice() -> None:
    parts = assign_parts(build_items(embed_images=False))
    rendered = _review_html(1, parts[0], _summaries(parts)[0])

    assert "label:value" in rendered
    assert 'content:"✓ 선택됨 · "' in rendered
    assert "✓ 현재 선택:" in rendered
    assert "saved.label===value||saved.label===text" in rendered
    assert "aria-pressed" in rendered


def test_outside_suggestion_recognizes_policy_usage_guide() -> None:
    label, reason = _outside_suggestion(
        {"cause_proxy": "", "risk_class": "business_signal", "signals": {}},
        "보험약관이란? 보험계약의 내용을 적은 문서입니다. QR코드로 약관해설 영상을 보고 보험금 지급절차를 확인하세요.",
    )

    assert label == "front_or_index"
    assert "안내" in reason


def test_easy_guide_contains_start_labels_resume_and_return_instructions() -> None:
    parts = assign_parts(build_items(embed_images=False))
    rendered = _index_html(_summaries(parts))

    for required in (
        "작업 시작 방법",
        "선택지는 이런 뜻입니다",
        "저장과 재개",
        "완료 후 보내는 파일",
        "문제가 생겼을 때",
        "human_review_600_part1.jsonl",
    ):
        assert required in rendered


def test_statute_reference_has_a_distinct_human_choice_and_context() -> None:
    items = build_items(embed_images=False)
    target = next(
        row
        for row in items
        if row["source_type"] == "outside_clause"
        and row["sha12"] == "15d0cf40e56c"
        and row["page"] == 312
    )

    assert target["suggested_label"] == "external_reference"
    assert "법규47" in target["page_text"]
    assert target["previous_page_text"]
    assert target["next_page_text"]
    assert target["gap_start"] == 301
    assert target["gap_end"] == 316


def test_model_examples_cover_every_part_and_key_decisions() -> None:
    rendered = _examples_html()

    for part in range(1, 6):
        assert f'id="part{part}"' in rendered
    for decision in (
        "외부 법령·참고자료",
        "약관 내용인데 조항에서 빠짐",
        "표 아님 / 본문을 표로 잘못 잡음",
        "표는 맞지만 칸 연결이 깨짐",
        "판단 보류",
        "모든 항목과 지급률 짝이 맞음",
        "일부 짝만 수정하면 됨",
        "여러 짝이 틀려 사용할 수 없음",
        "네 기간과 이율이 모두 맞음",
    ):
        assert decision in rendered
