from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from scripts.legal import verify_normalized


def _court_row(case_id: str, locator: str) -> dict:
    return {
        "case": {"id": case_id, "source": "court"},
        "facts": [{"evidence_ref": {"locator": locator}}],
        "holdings": [],
    }


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def test_verify_keeps_locator_result_and_missing_source_signal(
    monkeypatch, tmp_path: Path
) -> None:
    raw = tmp_path / "raw"
    bodies = raw / "bodies"
    bodies.mkdir(parents=True)
    _write_json(
        bodies / "001.json",
        {
            "PrecService": {
                "사건번호": "2024다1",
                "판시사항": "<p>보험금&nbsp;지급 요건</p>",
                "판결요지": "",
                "판례내용": "",
            }
        },
    )
    _write_json(bodies / "002.json", {"Law": "일치하는 판례가 없습니다."})
    normalized = tmp_path / "normalized.jsonl"
    _write_jsonl(
        normalized,
        [
            _court_row("2024다1", "보험금 지급 요건..."),
            _court_row("2024다404", "존재하지 않는 원문"),
        ],
    )
    monkeypatch.setattr(verify_normalized, "RAW", raw)

    result = verify_normalized.verify(normalized)

    assert result == {
        "총_레코드": 2,
        "검사_항목": 2,
        "원문에서_확인됨": 1,
        "미확인": 1,
        "미확인_목록": [("2024다404", "존재하지 않는 원문")],
        "원문_자체를_못_찾은_사건": ["2024다404"],
    }


def test_court_bodies_are_read_at_most_once_for_multiple_cases(
    monkeypatch, tmp_path: Path
) -> None:
    raw = tmp_path / "raw"
    bodies = raw / "bodies"
    bodies.mkdir(parents=True)
    for filename, case_id, text in (
        ("001.json", "무관사건", "무관 본문"),
        ("002.json", "2024다1", "첫 번째 근거"),
        ("003.json", "2024다2", "두 번째 근거"),
    ):
        _write_json(
            bodies / filename,
            {
                "PrecService": {
                    "사건번호": case_id,
                    "판시사항": text,
                    "판결요지": "",
                    "판례내용": "",
                }
            },
        )
    normalized = tmp_path / "normalized.jsonl"
    _write_jsonl(
        normalized,
        [
            _court_row("2024다1", "첫 번째 근거"),
            _court_row("2024다2", "두 번째 근거"),
        ],
    )
    monkeypatch.setattr(verify_normalized, "RAW", raw)

    original_read_text = Path.read_text
    body_reads: list[str] = []

    def tracked_read_text(self: Path, *args, **kwargs) -> str:
        if self.parent == bodies:
            body_reads.append(self.name)
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked_read_text)

    result = verify_normalized.verify(normalized)

    assert result["원문에서_확인됨"] == 2
    assert result["미확인"] == 0
    assert body_reads
    assert max(Counter(body_reads).values()) == 1
