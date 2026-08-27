# -*- coding: utf-8 -*-
"""최신 QA 후보에서 Part 5를 다시 구성해 항목별로 재감사한다.

축이나 층 이름만 보고 같은 결정을 찍지 않는다.

* A축: 날짜, 인용 게이트, 질병기호와 조항의 관계를 검사한다.
* B축: 질문의 가입유형·서비스와 원문의 자기부담금 사실을 각각 대조한다.
* C축: 결손 종류가 실제 evidence 상태와 일치하는지 확인하고 고객 문장을 검사한다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re

from app.core.domain import kcd_ranges as kcd
from scripts.review.complete_qa_pilot_part5_codex import (
    DECISION_LABELS,
    _date,
    _edit_for_reason,
)


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "data" / "finetune" / "qa_pilot" / "candidates.jsonl"
DEFAULT_OUTPUT = (
    ROOT / "docs" / "review" / "qa_pilot_pkg" / "qa_pilot_review_part5_reaudit.jsonl"
)
REVIEWED_AT = dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _part5(path: pathlib.Path) -> list[dict]:
    rows = _read_jsonl(path)
    if len(rows) != 300:
        raise RuntimeError(f"QA 후보는 300건이어야 합니다: 실제 {len(rows)}건")
    mine = [row for index, row in enumerate(rows) if index % 5 == 4]
    counts = {axis: sum(row.get("axis") == axis for row in mine) for axis in "ABC"}
    if len(mine) != 60 or counts != {"A": 36, "B": 12, "C": 12}:
        raise RuntimeError(f"Part 5 배정이 예상과 다릅니다: {len(mine)}건, {counts}")
    if len({row["item_id"] for row in mine}) != 60:
        raise RuntimeError("Part 5 item_id가 중복됐습니다")
    return mine


def _base(item: dict, decision: str, *, reason: str, edited: str, note: str,
          checks: dict) -> dict:
    return {
        "item_id": item["item_id"],
        "axis": item["axis"],
        "stratum": item["stratum"],
        "decision": decision,
        "decision_label": DECISION_LABELS[decision],
        "reason": reason,
        "edited_answer": edited,
        "note": note,
        "draft_answer": item["draft_answer"],
        "reviewer": "Codex 항목별 재감사",
        "reviewed_at": REVIEWED_AT,
        "part": "5",
        "audit_checks": checks,
    }


def _audit_a(item: dict) -> dict:
    request = item["request"]
    engine = item["engine"]
    evidence = item.get("evidence") or []
    code = request["kcd_codes"][0]
    reason_code = engine["reason_code"]
    date_ok = _date(request["incident_on"]) > _date(request["enrolled_on"])
    gates_ok = all(
        row.get("parse_status") == "ok" and row.get("citation_eligible") is True
        for row in evidence
    )
    citations_declared = int(engine.get("citations") or 0)

    mentions = []
    ranges = []
    for row in evidence:
        text = row.get("text") or ""
        mentions.extend(kcd.scan_clause(text))
        ranges.extend(kcd.parse_ranges(text))
    judged = kcd.judge(code, mentions)
    parsed_code = kcd.CodeRef.parse(code)
    code_mentioned = bool(
        parsed_code and any(code_range.contains(parsed_code) for code_range in ranges)
    )
    negative_title = any(
        re.search(r"보상하지|지급하지|면책", row.get("title") or "")
        for row in evidence
    )
    checks = {
        "incident_after_enrollment": date_ok,
        "evidence_count": len(evidence),
        "engine_citation_count": citations_declared,
        "evidence_gates_ok": gates_ok,
        "kcd_code": code,
        "kcd_code_mentioned": code_mentioned,
        "kcd_scan_status": judged["status"],
        "negative_clause_title": negative_title,
    }
    if not date_ok:
        return _base(
            item, "R", reason="질문 자체가 잘못됐다", edited="",
            note="사고일이 가입일보다 앞서 질의가 성립하지 않습니다.", checks=checks,
        )

    if reason_code in {"excluded_by_clause", "exception_applies"}:
        expected = "excluded" if reason_code == "excluded_by_clause" else "exception"
        #: 일부 인용문은 면책 선언이 목록 뒤에 잘려 붙는다. 이 경우에도
        #: `보상하지 않는 사항`이라는 조항 제목과 코드 직접 언급이 함께 있어야 받는다.
        semantic_ok = judged["status"] == expected
        if expected == "excluded":
            semantic_ok = semantic_ok or (
                judged["status"] == "not_mentioned" and code_mentioned and negative_title
            )
        support_ok = bool(evidence) and citations_declared > 0 and gates_ok and semantic_ok
        checks["reason_matches_evidence"] = semantic_ok
        checks["support_ok"] = support_ok
        if not support_ok:
            return _base(
                item, "R", reason="근거가 주장을 받치지 않는다", edited="",
                note=(f"질병기호 {code}와 {reason_code} 판정을 인용문에서 재현하지 "
                      "못했습니다. 엔진 판정과 인용 생성 경로를 다시 확인해야 합니다."),
                checks=checks,
            )
        if reason_code == "excluded_by_clause" and "가능성" not in item["draft_answer"]:
            return _base(
                item, "E", reason="문장이 판정보다 강하다",
                edited=(f"{code}가 약관의 보상하지 않는 사항에 포함된 근거가 확인되었습니다. "
                        "면책 가능성이 있으며, 최종 지급 여부는 실제 치료 내용과 청구 "
                        "서류를 함께 확인해야 합니다."),
                note="근거는 맞지만 면책을 확정하지 않도록 표현을 낮췄습니다.", checks=checks,
            )
        return _base(
            item, "A", reason="", edited="",
            note=(f"질병기호 {code}, 인용 게이트, 조항 제목과 본문의 "
                  f"{expected} 근거를 이 항목에서 각각 확인했습니다."), checks=checks,
        )

    zero_citation_ok = citations_declared == 0 and not evidence
    checks["zero_citation_state_consistent"] = zero_citation_ok
    if not zero_citation_ok:
        return _base(
            item, "R", reason="판정과 근거 상태가 맞지 않는다", edited="",
            note="기권 사유인데 인용 수 또는 evidence 상태가 예상과 다릅니다.", checks=checks,
        )
    edited = _edit_for_reason(item)
    return _base(
        item, "E", reason="근거는 맞고 문장만 고침", edited=edited,
        note=(f"{reason_code} 기권과 인용 0건을 이 항목에서 확인했습니다. 내부 상태명, "
              "검증되지 않은 조항명 또는 근거가 있는 것처럼 보이는 표현을 제거했습니다."),
        checks=checks,
    )


def _fact_fields(text: str) -> dict[str, str]:
    fields = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def _compact(value: str) -> str:
    return re.sub(r"[\s.,()]+", "", value or "")


def _clean_amount(value: str) -> str:
    text = re.sub(r"\s+", " ", value.strip())
    text = re.sub(r"(\d)만\s*(\d)천원", r"\1만 \2천원", text)
    text = re.sub(r"보상대상\s*의료비", "보상 대상 의료비", text)
    text = re.sub(r"보장대상\s*의료비", "보장 대상 의료비", text)
    text = re.sub(r"공제기준\s*금액", "공제 기준 금액", text)
    text = re.sub(r"본인부담률을\s*곱한", "본인부담률을 곱한", text)
    text = re.sub(r"원과\s*", "원과 ", text)
    # OCR 원문에서 `주)`는 표 아래 각주를 가리키는 표식이다. 각주 본문 없이
    # 표식만 고객 답변에 남기면 오히려 의미가 끊기므로 표시만 제거한다.
    text = re.sub(r"\s*주\)", "", text)
    text = re.sub(r",\s*", ", ", text)
    text = re.sub(r"의\s*(\d+%)", r"의 \1", text)
    text = re.sub(r"(\d+%)\s*중", r"\1 중", text)
    text = re.sub(r"\s*중\s*큰\s*금액", " 중 큰 금액", text)
    return re.sub(r"\s+", " ", text).strip()


_B_QUESTION = re.compile(r"^(.*?)에서 (.*?) 자기부담금은 얼마인가요\?$")


def _audit_b(item: dict) -> dict:
    evidence = item.get("evidence") or []
    match = _B_QUESTION.match(item["question"])
    fields = _fact_fields(evidence[0].get("text") if len(evidence) == 1 else "")
    plan = match.group(1).strip() if match else ""
    service = match.group(2).strip() if match else ""
    fact_services = [part.strip() for part in fields.get("의료서비스", "").split(",")]
    amount = fields.get("자기부담금", "")
    draft_amount = re.sub(r"\s*입니다\.?$", "", item["draft_answer"].strip())
    checks = {
        "single_evidence": len(evidence) == 1,
        "parse_status_ok": bool(evidence) and evidence[0].get("parse_status") == "ok",
        "citation_eligible": bool(evidence) and evidence[0].get("citation_eligible") is True,
        "question_plan": plan,
        "fact_plan": fields.get("가입유형", ""),
        "plan_matches": _compact(plan) == _compact(fields.get("가입유형", "")),
        "question_service": service,
        "fact_services": fact_services,
        "service_matches": _compact(service) in {_compact(x) for x in fact_services},
        "fact_amount": amount,
        "draft_amount_matches": _compact(draft_amount) == _compact(amount),
    }
    required = (
        checks["single_evidence"]
        and checks["parse_status_ok"]
        and checks["citation_eligible"]
        and checks["plan_matches"]
        and checks["service_matches"]
        and checks["draft_amount_matches"]
    )
    if not required:
        return _base(
            item, "R", reason="표의 항목 또는 금액 짝이 맞지 않는다", edited="",
            note="가입유형·서비스·자기부담금을 원문 필드와 각각 대조했으며 하나 이상 불일치했습니다.",
            checks=checks,
        )
    edited = _clean_amount(amount) + "입니다."
    if item["draft_answer"] == edited:
        return _base(
            item, "A", reason="", edited="",
            note=(f"가입유형 {plan}, 서비스 {service}, 자기부담금 {amount}를 "
                  "승인 fact와 개별 대조했습니다."), checks=checks,
        )
    return _base(
        item, "E", reason="값은 맞고 문장 표기만 고침", edited=edited,
        note=(f"가입유형 {plan}, 서비스 {service}, 자기부담금 ‘{amount}’은 원문과 "
              "일치합니다. 값은 바꾸지 않고 띄어쓰기·쉼표와 단독 각주 표식을 "
              "고객이 읽기 쉬운 형태로 정리했습니다."),
        checks=checks,
    )


_C_EDIT = {
    "no_evidence": (
        "현재 확인된 약관 근거가 없어 이 질문에 대한 답을 확정할 수 없습니다. "
        "가입 상품과 적용 약관을 확인한 뒤 다시 검토해야 합니다."
    ),
    "ambiguous_product": (
        "적용 가능한 상품을 하나로 특정할 정보가 부족해 답을 확정할 수 없습니다. "
        "보험증권의 정확한 상품명과 가입일을 확인해 주세요."
    ),
    "document_not_reliable": (
        "찾은 약관 자료의 읽기 상태를 확인할 수 없어 근거로 사용하지 않았습니다. "
        "약관 원문을 확인한 뒤 다시 검토해야 합니다."
    ),
    "citation_unverified": (
        "찾은 조항이 인용 가능한 근거로 검증되지 않아 답을 확정하지 않았습니다. "
        "검증된 약관 원문으로 다시 확인해야 합니다."
    ),
}


def _audit_c(item: dict) -> dict:
    kind = item["stratum"].split(":", 1)[1]
    evidence = item.get("evidence") or []
    if kind == "no_evidence":
        state_ok = not evidence
    elif kind == "ambiguous_product":
        state_ok = not evidence
    elif kind == "document_not_reliable":
        state_ok = bool(evidence) and all(row.get("parse_status") is None for row in evidence)
    elif kind == "citation_unverified":
        state_ok = bool(evidence) and all(
            row.get("parse_status") == "ok" and row.get("citation_eligible") is False
            for row in evidence
        )
    else:
        raise RuntimeError(f"알 수 없는 C축 결손 종류입니다: {kind}")
    checks = {
        "abstain_kind": kind,
        "evidence_count": len(evidence),
        "evidence_state_matches_kind": state_ok,
        "legal_reference_is_not_policy_clause": bool(item.get("reference")),
    }
    if not state_ok:
        return _base(
            item, "R", reason="기권 사유와 근거 상태가 맞지 않는다", edited="",
            note=f"{kind}로 만든 항목이지만 실제 evidence 상태가 그 조건과 다릅니다.",
            checks=checks,
        )
    return _base(
        item, "E", reason="기권은 맞고 고객 문장만 고침", edited=_C_EDIT[kind],
        note=(f"{kind} 조건을 이 항목의 evidence에서 확인했습니다. 판례·분쟁조정례가 "
              "붙어 있어도 개별 가입 약관을 대신하지 않으며, 내부 시스템 표현을 고객용으로 바꿨습니다."),
        checks=checks,
    )


def audit(item: dict) -> dict:
    if item["axis"] == "A":
        return _audit_a(item)
    if item["axis"] == "B":
        return _audit_b(item)
    if item["axis"] == "C":
        return _audit_c(item)
    raise RuntimeError(f"알 수 없는 축입니다: {item['axis']}")


def build(path: pathlib.Path) -> list[dict]:
    rows = [audit(item) for item in _part5(path)]
    if any(row["decision"] == "E" and not row["edited_answer"].strip() for row in rows):
        raise RuntimeError("수정(E) 결정에 수정 문장이 비어 있습니다")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=pathlib.Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    rows = build(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    counts = {key: sum(row["decision"] == key for row in rows) for key in "AENRS"}
    print(json.dumps({"output": str(args.output), "rows": len(rows), "decisions": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
