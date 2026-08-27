# -*- coding: utf-8 -*-
"""Part 5의 다시 물을 23건을 항목별 재감사 결과로 완성한다."""

from __future__ import annotations

import argparse
import json
import pathlib

from app.core.domain import kcd_ranges as kcd


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_INPUT = (
    ROOT / "docs" / "review" / "qa_pilot_pkg" / "part5_다시물을_23건.jsonl"
)
DEFAULT_REAUDIT = (
    ROOT / "docs" / "review" / "qa_pilot_pkg" / "qa_pilot_review_part5_reaudit.jsonl"
)
DEFAULT_OUTPUT = (
    ROOT / "docs" / "review" / "qa_pilot_pkg" / "part5_다시물을_23건_완료.jsonl"
)


def _read(path: pathlib.Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _a_note(row: dict) -> str:
    machine = row.get("기계대조") or []
    code = str(machine[0].get("code") if machine else "")
    mentions = kcd.scan_clause(row.get("인용_본문") or "")
    judged = kcd.judge(code, mentions)
    if row["stratum"].endswith("exception_applies"):
        excluded = sorted(
            {str(m.range) for m in mentions if m.kind == "exclude" and m.range.contains(kcd.CodeRef.parse(code))}
        )
        exceptions = sorted(
            {str(m.range) for m in mentions if m.kind == "exception" and m.range.contains(kcd.CodeRef.parse(code))}
        )
        return (
            f"{code}는 면책 범위 {', '.join(excluded) or '정신 및 행동장애 범위'}에 들지만 "
            f"같은 인용문에서 {', '.join(exceptions) or '요양급여 치료'} 예외에도 포함됩니다. "
            "따라서 요양급여 해당 여부 등 추가 서류가 필요하다는 엔진 판정과 고객문장이 맞습니다."
        )
    if judged["status"] == "excluded":
        hit_ranges = sorted({hit["range"] for hit in judged["hits"] if hit["kind"] == "exclude"})
        return (
            f"{code}가 인용문의 면책 범위 {', '.join(hit_ranges)}에 포함됩니다. "
            "고객문장도 면책을 확정하지 않고 가능성으로 제한해 판정과 맞습니다."
        )
    return (
        f"인용문에 {code}가 보상하지 않는 항목으로 직접 나열되고 인용 조항도 "
        "‘보상하지 않는 사항’입니다. 고객문장은 면책 가능성으로 표현해 판정보다 세지 않습니다."
    )


def complete(input_path: pathlib.Path, reaudit_path: pathlib.Path) -> list[dict]:
    rows = _read(input_path)
    audit = {row["item_id"]: row for row in _read(reaudit_path)}
    if len(rows) != 23 or len({row["item_id"] for row in rows}) != 23:
        raise RuntimeError("다시 물을 파일은 고유한 23건이어야 합니다")
    if any(row["item_id"] not in audit for row in rows):
        raise RuntimeError("최신 재감사 결과에 없는 item_id가 있습니다")

    completed = []
    for source in rows:
        row = dict(source)
        reviewed = audit[row["item_id"]]
        row["decision"] = reviewed["decision"]
        row["edited_answer"] = reviewed["edited_answer"]
        row["note"] = _a_note(row) if row["stratum"].startswith("A:") else reviewed["note"]
        completed.append(row)

    counts = {key: sum(row["decision"] == key for row in completed) for key in "AENRS"}
    if counts != {"A": 13, "E": 10, "N": 0, "R": 0, "S": 0}:
        raise RuntimeError(f"완료 결정 분포가 예상과 다릅니다: {counts}")
    if any(row["decision"] == "E" and not row["edited_answer"].strip() for row in completed):
        raise RuntimeError("수정(E) 결정에 수정 문장이 비어 있습니다")
    if any(not row["note"].strip() for row in completed):
        raise RuntimeError("메모가 비어 있는 항목이 있습니다")
    return completed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=pathlib.Path, default=DEFAULT_INPUT)
    parser.add_argument("--reaudit", type=pathlib.Path, default=DEFAULT_REAUDIT)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rows = complete(args.input, args.reaudit)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "rows": len(rows),
                "A": sum(row["decision"] == "A" for row in rows),
                "E": sum(row["decision"] == "E" for row in rows),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
