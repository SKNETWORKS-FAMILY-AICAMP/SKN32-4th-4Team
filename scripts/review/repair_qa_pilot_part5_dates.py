# -*- coding: utf-8 -*-
"""QA 파일럿 Part 5의 가입 전 사고 10건을 고쳐 엔진을 다시 실행한다.

기존 행을 조용히 덮어쓰지 않는다. 원래 날짜와 수정 규칙, 실제 엔진 응답을
함께 기록한 교체용 JSON을 만들어 데이터 계보를 보존한다.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import pathlib

from fastapi.testclient import TestClient

from app.main import create_app
from scripts.finetune.build_qa_pilot import _incident_after, _insurer
from scripts.review.complete_qa_pilot_part5_codex import (
    DEFAULT_INPUT,
    _date,
    _load_items,
    _review,
)


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    ROOT / "docs" / "review" / "qa_pilot_pkg" / "qa_pilot_part5_repaired_10.json"
)
GENERATED_AT = dt.datetime.now().astimezone().isoformat(timespec="seconds")
DATE_RULE_ID = "enrollment_plus_1y_capped_at_20260826"


def _question(body: dict) -> str:
    enrolled = _date(body["enrolled_on"])
    incident = _date(body["incident_on"])
    codes = ", ".join(body.get("kcd_codes") or [])
    condition = (body.get("condition_text") or "치료를 받았습니다").strip()
    return (
        f"{_insurer(body['insurer'])} 「{body.get('product_name') or ''}」에 "
        f"{enrolled.year}년 {enrolled.month}월에 가입했습니다. "
        f"{incident.year}년 {incident.month}월 {incident.day}일에 "
        f"{condition}({codes}). 보장되나요?"
    )


def _evidence(engine_response: dict) -> list[dict]:
    policy = engine_response.get("applied_policy") or {}
    sha = policy.get("sha256") or ""
    rows = []
    for citation in engine_response.get("citations") or []:
        rows.append(
            {
                "clause_id": citation.get("clause_id"),
                "sha12": sha[:12],
                "insurer": policy.get("insurer"),
                "qualified_no": citation.get("qualified_no"),
                "section": citation.get("section"),
                "title": citation.get("title"),
                "page_from": citation.get("page_from"),
                "page_to": citation.get("page_to"),
                "parse_status": "ok",
                "citation_eligible": True,
                "text": citation.get("quote") or "",
            }
        )
    return rows


def _repair_item(item: dict, client: TestClient) -> dict:
    original_incident = item["request"]["incident_on"]
    corrected_incident = _incident_after(item["request"]["enrolled_on"])
    if _date(corrected_incident) <= _date(item["request"]["enrolled_on"]):
        raise RuntimeError(f"수정 사고일이 가입일 뒤가 아닙니다: {item['item_id']}")

    body = copy.deepcopy(item["request"])
    body["incident_on"] = corrected_incident
    response = client.post("/v1/prechecks", json=body)
    if response.status_code != 200:
        raise RuntimeError(
            f"엔진 재실행 실패 {item['item_id']}: HTTP {response.status_code} {response.text}"
        )
    engine_response = response.json()
    required = {"verdict", "reason_code", "message", "citations", "abstained"}
    missing = sorted(required - engine_response.keys())
    if missing:
        raise RuntimeError(f"엔진 응답 필수 키 누락 {item['item_id']}: {missing}")

    replacement = copy.deepcopy(item)
    replacement["question"] = _question(body)
    replacement["request"] = body
    replacement["stratum"] = (
        f"A:{engine_response.get('verdict')}:{engine_response.get('reason_code')}"
    )
    replacement["engine"] = {
        "verdict": engine_response.get("verdict"),
        "reason_code": engine_response.get("reason_code"),
        "abstained": engine_response.get("abstained"),
        "citations": len(engine_response.get("citations") or []),
    }
    replacement["draft_answer"] = engine_response.get("message") or ""
    replacement["evidence"] = _evidence(engine_response)
    replacement["decision"] = ""
    replacement["note"] = ""
    replacement["repair"] = {
        "revision": "date-repair-v1",
        "source_item_id": item["item_id"],
        "field": "request.incident_on",
        "original_value": original_incident,
        "corrected_value": corrected_incident,
        "rule_id": DATE_RULE_ID,
        "rule_description": (
            "가입일 1년 뒤를 사용하되 2026-08-26을 넘으면 2026-08-26으로 제한"
        ),
        "engine_rerun": True,
        "engine_endpoint": "/v1/prechecks",
    }

    codex_review = _review(replacement)
    codex_review["reviewer"] = "Codex 재생성 검수"
    codex_review["reviewed_at"] = GENERATED_AT
    return {
        "item": replacement,
        "engine_response": engine_response,
        "codex_review": codex_review,
        "comparison": {
            "verdict_changed": item["engine"].get("verdict")
            != engine_response.get("verdict"),
            "reason_code_changed": item["engine"].get("reason_code")
            != engine_response.get("reason_code"),
            "citation_count_before": item["engine"].get("citations", 0),
            "citation_count_after": len(engine_response.get("citations") or []),
        },
    }


def build(input_path: pathlib.Path) -> dict:
    all_items = _load_items(input_path)
    invalid = [
        item
        for item in all_items
        if item.get("axis") == "A"
        and _date(item["request"]["incident_on"])
        < _date(item["request"]["enrolled_on"])
    ]
    if len(invalid) != 10:
        raise RuntimeError(f"수정 대상은 10건이어야 합니다: 실제 {len(invalid)}건")

    client = TestClient(create_app("customer"))
    repaired = [_repair_item(item, client) for item in invalid]
    if len({row["item"]["item_id"] for row in repaired}) != 10:
        raise RuntimeError("수정 결과 item_id가 중복됐습니다")

    reviews = [row["codex_review"] for row in repaired]
    return {
        "schema_version": "qa-pilot-date-repair/v1",
        "generated_at": GENERATED_AT,
        "source": str(input_path.relative_to(ROOT)).replace("\\", "/"),
        "purpose": "가입일보다 사고일이 앞선 Part 5 후보 10건을 폐기하지 않고 재생성",
        "date_rule": {
            "id": DATE_RULE_ID,
            "description": "가입일 1년 뒤, 단 생성 기준일 2026-08-26을 넘지 않음",
            "basis": "scripts/finetune/build_qa_pilot.py::_incident_after",
        },
        "merge_policy": "items[].item.item_id가 같은 기존 행을 교체",
        "counts": {
            "source_invalid": len(invalid),
            "repaired": len(repaired),
            "engine_http_200": len(repaired),
            "remaining_invalid_dates": sum(
                _date(row["item"]["request"]["incident_on"])
                <= _date(row["item"]["request"]["enrolled_on"])
                for row in repaired
            ),
            "codex_approved": sum(row["decision"] == "A" for row in reviews),
            "codex_edited": sum(row["decision"] == "E" for row in reviews),
            "codex_requires_human": sum(
                row["decision"] in {"N", "R", "S"} for row in reviews
            ),
        },
        "items": repaired,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=pathlib.Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"output": str(args.output), **result["counts"]}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
