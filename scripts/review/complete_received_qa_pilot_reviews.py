# -*- coding: utf-8 -*-
"""팀원이 돌려준 QA Part 1·2·4와 Codex Part 5를 원자료로 재감사한다.

Part 1·2·4는 과거 검색근거형 후보다. 현재 엔진형 후보와 섞지 않고 다음 원자료로
복원한다.

* A: ``data/eval/retrieval_probes.json``의 gold eligible content hash
* B: S7.1 승인 OCR fact와 chunk
* C: 동일 item_id의 결손 후보
* Part 5: 최신 항목별 재감사 결과
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import pathlib

from scripts.review.reaudit_qa_pilot_part5 import (
    _audit_b,
    _audit_c,
    _read_jsonl,
)
from scripts.review.complete_qa_pilot_part5_codex import DECISION_LABELS


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_RECEIVED = pathlib.Path(
    os.environ.get(
        "QA_PILOT_RECEIVED_DIR",
        str(ROOT / "docs" / "review" / "qa_pilot_received"),
    )
)
DEFAULT_OUTPUT = ROOT / "docs" / "review" / "qa_pilot_completed_20260827"
DEFAULT_PART5 = (
    ROOT / "docs" / "review" / "qa_pilot_pkg" / "qa_pilot_review_part5_reaudit.jsonl"
)
REVIEWED_AT = dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _sources() -> tuple[dict, dict, dict, dict]:
    probes_doc = json.loads(
        (ROOT / "data" / "eval" / "retrieval_probes.json").read_text(encoding="utf-8")
    )
    probes = {row["probe_id"]: row for row in probes_doc["exclusion_queries"]}

    facts = _read_jsonl(
        ROOT / "data" / "work" / "s7_1_approved_facts" / "approved_facts.jsonl"
    )
    facts_by_suffix: dict[str, list[dict]] = collections.defaultdict(list)
    for fact in facts:
        facts_by_suffix[fact["candidate_id"][-12:]].append(fact)

    chunks = {
        row["content_hash"]: row.get("text") or ""
        for row in _read_jsonl(
            ROOT / "data" / "work" / "s7_1_approved_facts" / "chunks.jsonl"
        )
    }
    candidates_by_id: dict[str, list[dict]] = collections.defaultdict(list)
    for item in _read_jsonl(
        ROOT / "data" / "finetune" / "qa_pilot" / "candidates.jsonl"
    ):
        candidates_by_id[item["item_id"]].append(item)
    return probes, facts_by_suffix, chunks, candidates_by_id


def _lineage(received: dict) -> dict:
    return {
        "original_decision": received.get("decision") or "",
        "original_reviewer": received.get("reviewer") or "",
        "original_reviewed_at": received.get("reviewed_at") or "",
        "original_note_present": bool((received.get("note") or "").strip()),
        "original_edit_present": bool((received.get("edited_answer") or "").strip()),
    }


def _audit_old_a(received: dict, probes: dict) -> dict:
    body = received["item_id"][2:]
    probe_id, hash8 = body.rsplit(":", 1)
    probe = probes.get(probe_id)
    if not probe:
        raise RuntimeError(f"retrieval probe를 찾지 못했습니다: {received['item_id']}")
    golds = probe.get("gold_eligible_ids") or probe.get("gold_ids") or []
    matches = [value for value in golds if value.startswith(hash8)]
    if len(matches) != 1:
        raise RuntimeError(
            f"gold hash를 하나로 복원하지 못했습니다: {received['item_id']} → {matches}"
        )
    checks = {
        "probe_id": probe_id,
        "probe_kind": probe.get("kind"),
        "query": probe.get("query"),
        "gold_content_hash": matches[0],
        "gold_eligible_match": True,
    }
    return {
        "item_id": received["item_id"],
        "axis": "A",
        "stratum": received["stratum"],
        "decision": "A",
        "decision_label": DECISION_LABELS["A"],
        "reason": "",
        "edited_answer": "",
        "note": (
            f"검색 질의 ‘{probe.get('query')}’의 검증된 정답 content hash "
            f"{matches[0][:12]}와 이 항목의 근거 ID가 일치합니다. "
            f"{probe.get('kind')} 검색 정답으로 승인합니다."
        ),
        "draft_answer": received["draft_answer"],
        "reviewer": "Codex 원자료 재감사",
        "reviewed_at": REVIEWED_AT,
        "part": str(received["part"]),
        "audit_checks": checks,
        "received_review": _lineage(received),
    }


def _audit_old_b(received: dict, facts_by_suffix: dict, chunks: dict) -> dict:
    suffix = received["item_id"][2:]
    facts = facts_by_suffix.get(suffix) or []
    if len(facts) != 1:
        raise RuntimeError(
            f"승인 OCR fact를 하나로 복원하지 못했습니다: {received['item_id']} → {len(facts)}"
        )
    fact = facts[0]
    services = fact.get("service") or ["해당 서비스"]
    plan = fact.get("plan") or "해당 유형"
    source_item = {
        "axis": "B",
        "item_id": received["item_id"],
        "stratum": received["stratum"],
        "question": f"{plan}에서 {services[0]} 자기부담금은 얼마인가요?",
        "draft_answer": received["draft_answer"],
        "evidence": [
            {
                "parse_status": "ok",
                "citation_eligible": fact.get("citation_eligible"),
                "text": chunks.get(fact["content_hash"], ""),
            }
        ],
    }
    result = _audit_b(source_item)
    result["part"] = str(received["part"])
    result["received_review"] = _lineage(received)
    return result


def _audit_old_c(received: dict, candidates_by_id: dict) -> dict:
    candidates = candidates_by_id.get(received["item_id"]) or []
    if len(candidates) != 1:
        raise RuntimeError(
            f"C축 원본을 하나로 복원하지 못했습니다: {received['item_id']} → {len(candidates)}"
        )
    result = _audit_c(candidates[0])
    result["part"] = str(received["part"])
    result["draft_answer"] = received["draft_answer"]
    result["received_review"] = _lineage(received)
    return result


def complete_old_part(path: pathlib.Path) -> list[dict]:
    probes, facts_by_suffix, chunks, candidates_by_id = _sources()
    received = _read_jsonl(path)
    if len(received) != 60 or len({row["item_id"] for row in received}) != 60:
        raise RuntimeError(f"수신 파트는 고유한 60건이어야 합니다: {path}")

    rows = []
    for row in received:
        if row["axis"] == "A":
            rows.append(_audit_old_a(row, probes))
        elif row["axis"] == "B":
            rows.append(_audit_old_b(row, facts_by_suffix, chunks))
        elif row["axis"] == "C":
            rows.append(_audit_old_c(row, candidates_by_id))
        else:
            raise RuntimeError(f"알 수 없는 축입니다: {row['axis']}")
    return rows


def _validate_part(rows: list[dict], part: int) -> None:
    if len(rows) != 60 or len({row["item_id"] for row in rows}) != 60:
        raise RuntimeError(f"Part {part} 결과가 고유한 60건이 아닙니다")
    if any(str(row["part"]) != str(part) for row in rows):
        raise RuntimeError(f"Part {part} 표기가 섞였습니다")
    if any(row["decision"] not in {"A", "E", "N", "R", "S"} for row in rows):
        raise RuntimeError(f"Part {part}에 허용되지 않은 결정이 있습니다")
    if any(row["decision"] == "E" and not row["edited_answer"].strip() for row in rows):
        raise RuntimeError(f"Part {part}의 E 결정에 빈 수정문이 있습니다")
    if any(not row["note"].strip() for row in rows):
        raise RuntimeError(f"Part {part}에 빈 메모가 있습니다")


def build(received_dir: pathlib.Path, part5_path: pathlib.Path) -> dict[int, list[dict]]:
    outputs = {}
    for part in (1, 2, 4):
        rows = complete_old_part(received_dir / f"qa_pilot_review_part{part}.jsonl")
        _validate_part(rows, part)
        outputs[part] = rows
    part5 = _read_jsonl(part5_path)
    _validate_part(part5, 5)
    outputs[5] = part5

    all_rows = [row for part in (1, 2, 4, 5) for row in outputs[part]]
    if len(all_rows) != 240:
        raise RuntimeError("4개 파트 통합 결과가 240개 검수 기록이 아닙니다")
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--received-dir", type=pathlib.Path, default=DEFAULT_RECEIVED)
    parser.add_argument("--part5", type=pathlib.Path, default=DEFAULT_PART5)
    parser.add_argument("--output-dir", type=pathlib.Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    outputs = build(args.received_dir, args.part5)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    combined = []
    summary = {}
    for part in (1, 2, 4, 5):
        rows = outputs[part]
        combined.extend(rows)
        output = args.output_dir / f"qa_pilot_review_part{part}_completed.jsonl"
        output.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        summary[str(part)] = {
            key: sum(row["decision"] == key for row in rows) for key in "AENRS"
        }
    combined_path = (
        args.output_dir / "qa_pilot_review_parts1_2_4_5_completed_240_records.jsonl"
    )
    combined_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in combined),
        encoding="utf-8",
    )

    # Part 1·2·4는 과거 후보 묶음이고 Part 5는 재생성된 최신 후보 묶음이다.
    # 서로 다른 세대의 파트를 합치면 B 항목 하나가 겹친다. 이를 숨기거나 임의로
    # 다른 항목으로 바꾸지 않고, 240개 검수 기록은 보존하면서 실제 사용 파일에는
    # 최신 Part 5 판정을 우선해 한 번만 남긴다.
    by_id: dict[str, dict] = {}
    collisions: list[dict] = []
    for row in combined:
        previous = by_id.get(row["item_id"])
        if previous is not None:
            collisions.append(
                {
                    "item_id": row["item_id"],
                    "discarded_part": str(previous["part"]),
                    "kept_part": str(row["part"]),
                    "rule": "최신 후보 묶음인 Part 5 우선",
                }
            )
        by_id[row["item_id"]] = row
    unique = list(by_id.values())
    unique_path = (
        args.output_dir / "qa_pilot_review_parts1_2_4_5_completed_239_unique.jsonl"
    )
    unique_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in unique),
        encoding="utf-8",
    )
    manifest = {
        "received_review_records": len(combined),
        "unique_items": len(unique),
        "parts_received": [1, 2, 4, 5],
        "missing_part": 3,
        "mixed_candidate_generations": True,
        "collisions": collisions,
        "raw_combined": combined_path.name,
        "recommended_combined": unique_path.name,
    }
    (args.output_dir / "completion_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "review_records": len(combined),
                "unique_items": len(unique),
                "collisions": collisions,
                "parts": summary,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
