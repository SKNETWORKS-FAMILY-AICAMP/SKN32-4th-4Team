# -*- coding: utf-8 -*-
"""Part 5 최초 검수 60건에 날짜 재생성 검수 10건을 반영한다."""

from __future__ import annotations

import argparse
import json
import pathlib


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_BASE = (
    ROOT / "docs" / "review" / "qa_pilot_pkg" / "qa_pilot_review_part5_codex.jsonl"
)
DEFAULT_REPAIRS = (
    ROOT / "docs" / "review" / "qa_pilot_pkg" / "qa_pilot_part5_repaired_10.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "docs"
    / "review"
    / "qa_pilot_pkg"
    / "qa_pilot_review_part5_codex_merged.jsonl"
)


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def merge(base_path: pathlib.Path, repair_path: pathlib.Path) -> list[dict]:
    base = _read_jsonl(base_path)
    repair_doc = json.loads(repair_path.read_text(encoding="utf-8"))
    repairs = [row["codex_review"] for row in repair_doc.get("items") or []]

    if len(base) != 60 or len({row["item_id"] for row in base}) != 60:
        raise RuntimeError("기존 검수 파일은 고유한 60건이어야 합니다")
    if len(repairs) != 10 or len({row["item_id"] for row in repairs}) != 10:
        raise RuntimeError("재생성 검수 파일은 고유한 10건이어야 합니다")

    base_ids = {row["item_id"] for row in base}
    repair_ids = {row["item_id"] for row in repairs}
    missing = sorted(repair_ids - base_ids)
    if missing:
        raise RuntimeError(f"기존 60건에 없는 교체 ID가 있습니다: {missing}")

    old_rejected = {row["item_id"] for row in base if row["decision"] == "R"}
    if repair_ids != old_rejected:
        raise RuntimeError(
            "재생성 10건은 기존 반려 10건과 정확히 일치해야 합니다: "
            f"교체 외 반려={sorted(old_rejected - repair_ids)}, "
            f"반려 외 교체={sorted(repair_ids - old_rejected)}"
        )

    replacements = {row["item_id"]: row for row in repairs}
    merged = [replacements.get(row["item_id"], row) for row in base]
    if len(merged) != 60 or len({row["item_id"] for row in merged}) != 60:
        raise RuntimeError("병합 결과의 행 수 또는 ID가 잘못됐습니다")
    if any(row["decision"] not in {"A", "E", "N", "R", "S"} for row in merged):
        raise RuntimeError("병합 결과에 허용되지 않은 결정값이 있습니다")
    if any(row["decision"] == "E" and not row["edited_answer"].strip() for row in merged):
        raise RuntimeError("수정(E) 결정에 수정 문장이 비어 있습니다")
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=pathlib.Path, default=DEFAULT_BASE)
    parser.add_argument("--repairs", type=pathlib.Path, default=DEFAULT_REPAIRS)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    rows = merge(args.base, args.repairs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    counts = {
        key: sum(row["decision"] == key for row in rows) for key in "AENRS"
    }
    print(
        json.dumps(
            {"output": str(args.output), "rows": len(rows), "decisions": counts},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
