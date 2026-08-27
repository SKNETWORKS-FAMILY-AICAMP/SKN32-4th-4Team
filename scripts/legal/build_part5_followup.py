# -*- coding: utf-8 -*-
"""5파트 23건을 Codex가 먼저 검토하고 사람 확인 대상만 HTML로 만든다."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from scripts.legal.legal_review_html import build_review_items

ROOT = Path(__file__).resolve().parents[2]
LEGAL = ROOT / "data" / "legal"
LEDGER = LEGAL / "legal_case_normalized_final.jsonl"
QUEUE = LEGAL / "human_review_queue.json"
OUT_JSON = ROOT / "docs" / "review" / "part5_codex_review_20260825.json"
OUT_HTML = ROOT / "docs" / "review" / "part5_human_followup_20260825.html"

PART5_IDS = [
    "2011가합2760", "2015가단210771", "2019다267020", "2021나2046811",
    "2022다216688", "2023다283913", "case_100", "case_128", "case_137",
    "case_141", "case_146", "case_150", "case_155", "case_160", "case_22",
    "case_4", "case_56", "case_72", "case_79", "case_94", "dcsn_64524",
    "dcsn_64714", "fvst_332",
]


def _confirmed(reason: str) -> dict:
    return {"status": "codex_confirmed", "reason": reason, "human_question": ""}


REVIEW: dict[str, dict] = {
    "2011가합2760": {
        "status": "codex_corrected",
        "reason": "사실 문장이 중간에서 잘렸고, 법리 요약도 같은 판시사항을 반복해 실제 판단 이유를 설명하지 못합니다. ‘보험계약이민법’은 ‘보험계약이 민법’의 띄어쓰기 오류입니다.",
        "proposed": {
            "issue": "단기간에 보장 내용이 유사한 보험계약 13개를 체결하고 반복적인 장기 입원으로 보험금을 받은 경우, 보험금 부정취득 목적의 계약으로서 민법 제103조에 반하여 무효인지 여부",
            "fact": "피보험자는 단기간에 유사한 보험계약 13개를 체결했고 이를 감당할 소득 자료가 없었습니다. 기관지염·고혈압·염좌 등으로 반복적 장기 입원을 하여 보험금을 받았으며, 계약 당시 다수 보험 가입 사실을 알리지 않았습니다.",
            "conclusion": "계약 무효 / 보험금 지급채무 없음",
            "legal_summary": "여러 사정을 종합하면 순수한 위험 대비가 아니라 보험사고를 가장하거나 과장해 보험금을 부정 취득할 목적으로 체결한 계약이므로 민법 제103조의 선량한 풍속 기타 사회질서에 반하여 무효입니다.",
        },
        "human_question": "이 사건을 단순 not_covered로 유지할지, 계약무효 유형으로 따로 분류할지 승인해 주세요.",
    },
    "2015가단210771": {
        "status": "codex_corrected",
        "reason": "쟁점·사실·법리 요약이 모두 판결문을 그대로 복사한 뒤 잘라 붙인 형태입니다.",
        "proposed": {
            "issue": "비염 치료 목적의 트리암시놀론 주사가 적법한 비급여 진료행위인지, 보험사가 피보험자의 부당이득반환청구권을 대위하여 병원에 진료비 반환을 청구할 수 있는지 여부",
            "fact": "병원은 피보험자들에게 트리암시놀론 주사를 시행하고 비급여 진료비 합계 38,457,020원을 받았으며, 보험사는 같은 금액을 실손보험금으로 지급했습니다.",
            "conclusion": "진료계약 무효 / 병원의 부당이득 반환의무 인정",
            "legal_summary": "병원은 해당 진료행위를 요양급여 대상으로 편입하기 위한 절차를 밟지 않았고, 예외적으로 임의 비급여가 허용될 의학적 필요성·안전성·유효성도 입증하지 못했습니다. 진료계약은 무효이고 병원은 보험사가 대위 행사하는 피보험자들의 청구에 따라 진료비를 반환해야 합니다.",
        },
        "human_question": "실손 보장 여부 판례가 아니라 부당이득반환 사건이므로, 법률 참고자료로 유지할지 승인해 주세요.",
    },
    "2019다267020": {
        "status": "codex_corrected",
        "reason": "쟁점 3개는 맞지만 사실은 판시사항 복사본이 잘렸고, 세 법리 요약이 모두 같은 미완성 문장입니다. not_covered도 조건 없는 보장 제외처럼 오해될 수 있습니다.",
        "proposed": {
            "issue": "부당한 보험금 청구로 신뢰관계가 중대하게 파괴된 경우 보험계약 해지가 가능한 범위와 판단 기준",
            "fact": "입원 필요성이 없는 기간을 포함해 보험금을 청구하거나 지급받은 사안에서 보험자가 신뢰관계 파괴를 이유로 계약을 해지했습니다.",
            "conclusion": "중대한 신뢰관계 파괴가 인정되는 경우 장래를 향한 계약 해지 가능",
            "legal_summary": "입원 경위, 부정취득 목적, 불필요한 입원 기간과 보험금, 청구 횟수, 다른 보험 가입, 서류 조작 등을 종합해 엄격하게 판단해야 합니다. 이 해지권은 민법 제2조에 근거하므로 별도 설명의무가 없고, 중대한 행위가 특약에 관한 것이어도 특별한 사정이 없으면 계약 전부에 해지 효력이 미칠 수 있습니다.",
        },
        "human_question": "단순 not_covered가 아니라 조건부 계약해지 법리로 저장하는 수정안을 승인해 주세요.",
    },
    "2021나2046811": {
        "status": "needs_source",
        "reason": "쟁점과 사실이 판결 이유를 복사하다 잘렸고, covered는 사건 결과와 맞지 않습니다. 항소심 주문상 병원이 보험사에 220,377,276원을 지급하도록 일부 인용됐지만 로컬 수집본은 판단 이유 전체가 없습니다.",
        "proposed": {
            "issue": "병원이 실손 보장 검사비를 비정상적으로 높이고 비보장 다초점 인공수정체 비용을 낮춘 진료비명세서를 발급한 행위가 보험금 편취 방조 또는 공동불법행위에 해당하는지 여부",
            "fact": "2016년 약관 개정 뒤 다초점 인공수정체는 실손 보장에서 제외됐습니다. 병원은 인공수정체 비용을 낮추고 보장 검사비를 크게 높인 명세서를 발급했고, 보험사는 이를 근거로 지급된 보험금 중 손해배상을 청구했습니다.",
            "conclusion": "항소심 일부 인용(병원이 보험사에 220,377,276원 지급) — 전체 법리 원문 추가 확보 필요",
            "legal_summary": "현재 로컬 자료만으로는 일부 인용 범위의 구체적인 불법행위 판단 이유를 끝까지 확인할 수 없습니다.",
        },
        "human_question": "전체 판결문을 추가 확보할 때까지 보류할지, 주문과 확보된 발췌만으로 참고자료에 남길지 선택해 주세요.",
    },
    "2022다216688": {
        "status": "codex_corrected",
        "reason": "사실과 법리 요약이 판시사항 중간에서 잘렸습니다. not_covered는 보험금 보장 판단이 아니라 채권자대위소송의 절차상 결론을 잘못 단순화한 값입니다.",
        "proposed": {
            "issue": "보험사가 피보험자의 병원 상대 부당이득반환채권을 대위 행사할 때 피보험자가 자력이 있어도 보전의 필요성이 인정되는지 여부",
            "fact": "보험사는 백내장 관련 진료가 위법한 임의 비급여이고 보험금 지급사유도 아니라며, 피보험자를 대위하여 병원을 상대로 진료비 반환을 청구했습니다.",
            "conclusion": "보험사 상고기각 / 피보험자가 자력이 있으면 보전 필요성 부정",
            "legal_summary": "금전채권을 보전하기 위한 채권자대위소송에서 채무자인 피보험자가 자력이 있다면 보전의 필요성이 인정되지 않습니다. 원심의 소 각하 판단이 유지됐습니다.",
        },
        "human_question": "보장 여부가 아닌 절차법 판례이므로 procedural_dismissal과 같은 별도 유형으로 유지할지, 보장 판정 자료에서 제외할지 선택해 주세요.",
    },
    "2023다283913": {
        "status": "codex_corrected",
        "reason": "쟁점 4개는 맞지만 사실이 잘렸고, 서로 다른 네 법리에 모두 remanded와 동일한 미완성 설명을 붙였습니다.",
        "proposed": {
            "issue": "본인부담상한액 초과 환급금이 질병입원의료비 특약의 보상대상인지 여부와 관련 약관 해석 원칙",
            "fact": "피보험자는 본인부담상한액을 초과해 먼저 낸 뒤 국민건강보험공단에서 환급받을 수 있는 금액까지 실손보험금으로 청구했고, 보험사는 그 부분의 지급을 거절했습니다.",
            "conclusion": "환급받은 본인부담상한액 초과분은 not_covered / 원심 파기환송",
            "legal_summary": "특약은 피보험자가 최종적으로 부담하는 요양급여 부분만 담보합니다. 공단이 부담해 환급되는 상한액 초과분은 보상대상이 아닙니다. 약관을 합리적으로 해석한 결과 의미가 하나로 정해지면 작성자 불이익 원칙을 적용하지 않습니다. 나머지 쟁점은 소액사건 심리와 상고이유 기재에 관한 절차법 원칙입니다.",
        },
        "human_question": "보험 보장과 직접 관련된 쟁점 i2·i3만 남기고 절차법 쟁점 i1·i4를 분리할지 승인해 주세요.",
    },
    "case_100": _confirmed("금감원 원문과 쟁점·사실·처리결과가 일치합니다. 책임보험 한도 초과 치료비 중 피해자 과실비율 부분의 구상청구가 가능하다는 취지가 정확합니다."),
    "case_128": {
        "status": "codex_corrected",
        "reason": "금감원 원문은 A씨의 요양병원 치료와 B씨의 비밸브재건술이라는 서로 다른 두 사례인데, 현재는 하나의 포괄적인 쟁점과 결론으로 합쳐져 있습니다.",
        "proposed": {
            "issue": "A: 호흡기질환 관련 요양병원 입원·고주파치료의 의학적 필요성이 객관적으로 입증됐는지 여부 / B: 비밸브재건술이 코막힘 치료 목적이었다는 객관적 자료가 있는지 여부",
            "fact": "A씨와 B씨 모두 치료 필요성 또는 치료 목적을 뒷받침할 검사결과·수술기록지 등 객관적인 의학 자료가 충분하지 않았습니다.",
            "conclusion": "두 사례 모두 객관적 의학 근거 부족으로 not_covered",
            "legal_summary": "의사의 소견만으로는 충분하지 않고 증상 개선과 치료 필요성을 보여주는 검사결과·수술기록지 등 객관적 의학 자료가 필요합니다. 두 사례를 별도 쟁점으로 나누는 것이 검색과 판단에 안전합니다.",
        },
        "human_question": "한 금감원 게시물 안의 A·B 사례를 쟁점 2개로 분리하는 수정안을 승인해 주세요.",
    },
    "case_137": _confirmed("금감원 원문과 일치합니다. 코로나19가 법정전염병이어도 해당 약관이 열거한 특정전염병이 아니므로 진단보험금 대상이 아니라는 처리결과가 정확합니다."),
    "case_141": _confirmed("금감원 원문과 일치합니다. 관상동맥조영술은 진단 목적이고 약관의 절단·절제 수술 정의에도 해당하지 않는다는 처리결과가 정확합니다."),
    "case_146": _confirmed("금감원 원문과 일치합니다. 보험기간 중 암 진단이라는 보험사고가 발생했으므로 계약 해지 뒤 같은 암의 표적항암치료를 받아도 보험금 지급의무가 있다는 취지가 정확합니다."),
    "case_150": _confirmed("금감원 원문과 일치합니다. 처음부터 실제 직업이 바뀌지 않았다면 직업 변경 통지의무 위반은 아니고, 고지의무 해지권 행사기간도 지났다는 취지가 정확합니다."),
    "case_155": _confirmed("금감원 원문과 일치합니다. 해당 약관이 한방치료의 비급여 의료비를 제외하고 있어 약침·첩약 비용이 보상대상이 아니라는 취지가 정확합니다."),
    "case_160": _confirmed("금감원 원문과 일치합니다. 2021년 7월 이후 약관의 성장호르몬제 전액본인부담금 제외 조항에 따른 처리결과가 정확합니다."),
    "case_22": {
        "status": "codex_corrected",
        "reason": "민감정보 제공 동의 전까지 보험금 심사가 보류된 사건인데 not_covered로 기록돼 최종 지급 거절처럼 보입니다.",
        "proposed": {
            "issue": "보험금 지급사유 조사에 필요한 민감정보 제공 동의를 요구할 수 있는지 여부",
            "fact": "신청인은 질병 수술비를 청구하면서 민감정보 제공에 동의하지 않았고, 보험사는 동의가 완료될 때까지 심사를 보류했습니다.",
            "conclusion": "pending 또는 indeterminate — 동의 후 지급 여부 재검토",
            "legal_summary": "질병과 수술 사실을 조사하려면 민감정보 제공 동의가 필요하므로 동의 전 심사를 보류한 업무는 약관에 위배되지 않습니다. 이는 보장 제외 확정이 아닙니다.",
        },
        "human_question": "not_covered를 pending/indeterminate로 바꾸는 수정안을 승인해 주세요.",
    },
    "case_4": {
        "status": "needs_source",
        "reason": "금감원 원문 자체의 민원내용이 ‘-’로 비어 있어 구체적인 사실관계를 확인할 수 없습니다. 쟁점과 처리결과만 존재합니다.",
        "proposed": {
            "issue": "협심증(I20)이 급성심근경색증 특별약관의 보장대상인지 여부",
            "fact": "원문에 구체적 사실관계가 기재되어 있지 않음",
            "conclusion": "not_covered",
            "legal_summary": "협심증 I20은 심장질환이지만 특약의 급성심근경색증 분류표가 열거한 I21·I22·I23에는 포함되지 않습니다.",
        },
        "human_question": "사실이 없는 상태로 참고자료에 유지할지, 원문 부족으로 제외할지 선택해 주세요.",
    },
    "case_56": _confirmed("금감원 원문과 일치합니다. 압박고정용 재료대가 신체 고정과 추가 손상 예방 기능을 하므로 약관상 보조기 면책에 해당한다는 취지가 정확합니다."),
    "case_72": _confirmed("금감원 원문과 일치합니다. 2017년 4월 이후 상품에서 비급여 주사료 특약 미가입 시 관절강내 주사치료가 보상되지 않는다는 취지가 정확합니다."),
    "case_79": _confirmed("금감원 원문과 일치합니다. 부담보 해제 조건은 5년간 보험금 미청구가 아니라 추가 진단 또는 치료 사실이 없는 것이므로 치료 이력이 있으면 해제되지 않는다는 취지가 정확합니다."),
    "case_94": _confirmed("금감원 원문과 일치합니다. 단체실손 종료일부터 1개월 이내라는 개인실손 재개 신청기한을 넘겨 인수가 거절된 처리결과가 정확합니다."),
    "dcsn_64524": _confirmed("금감원 분쟁조정 원문과 일치합니다. 자동차보험으로 충당된 치료비도 실제 치료에 든 비용에 포함해 약관상 비율을 추가 지급한다는 취지가 정확합니다."),
    "dcsn_64714": _confirmed("금감원 분쟁조정 원문과 일치합니다. 2016년 개정 전 약관에서는 백내장 치료 목적의 다초점인공수정체 비용을 시력교정술 면책으로 볼 수 없다는 제한까지 정확히 반영했습니다."),
    "fvst_332": _confirmed("금감원 판례 원문과 일치합니다. 항암제를 투여하지 않은 기간의 식이·심리요법은 암 직접치료 목적 입원으로 보기 어렵다는 취지가 정확합니다."),
}


def _load_rows() -> tuple[list[dict], list[dict]]:
    ledger = [
        json.loads(line)
        for line in LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    part_queue = [item for item in queue if item.get("case_id") in PART5_IDS]
    items = build_review_items(part_queue, ledger, LEGAL)
    by_id = {item["case_id"]: item for item in items}
    return ledger, [by_id[case_id] for case_id in PART5_IDS]


def build_payload() -> dict:
    _, items = _load_rows()
    if set(REVIEW) != set(PART5_IDS):
        raise RuntimeError("REVIEW와 5파트 사건 목록이 일치하지 않습니다.")
    rows = []
    for item in items:
        audit = REVIEW[item["case_id"]]
        rows.append({**item, **audit})
    counts = Counter(row["status"] for row in rows)
    return {
        "version": 1,
        "reviewed_at": "2026-08-25",
        "part": 5,
        "total_checked": len(rows),
        "counts": dict(counts),
        "human_items": [row for row in rows if row["status"] != "codex_confirmed"],
        "confirmed_items": [
            {"case_id": row["case_id"], "source_label": row["source_label"], "title": row["title"], "reason": row["reason"]}
            for row in rows
            if row["status"] == "codex_confirmed"
        ],
        "all_reviews": [
            {"case_id": row["case_id"], "status": row["status"], "reason": row["reason"], "proposed": row.get("proposed"), "human_question": row["human_question"]}
            for row in rows
        ],
    }


def render_html(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return HTML.replace("__DATA__", data)


HTML = r'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>5파트 Codex 선검토 후 사람 확인</title><style>
:root{--bg:#f3f6f8;--paper:#fff;--ink:#17222c;--muted:#5c6872;--line:#d8e0e6;--navy:#173b57;--blue:#1769aa;--green:#247a52;--amber:#936000;--red:#a33838}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 system-ui,"Malgun Gothic",sans-serif}button,textarea,select{font:inherit}button{cursor:pointer}button:focus-visible,textarea:focus-visible,select:focus-visible{outline:3px solid #f6b73c;outline-offset:2px}
header{position:sticky;top:0;z-index:10;background:var(--navy);color:#fff;padding:14px 20px;box-shadow:0 2px 8px #0003}.head,.tools,.stats,.decisions{display:flex;gap:9px;align-items:center;flex-wrap:wrap}h1{font-size:21px;margin:0}.stats{margin-left:auto}.stat{padding:4px 9px;border-radius:999px;background:#ffffff20}.tools{margin-top:10px}header button,header select{border:1px solid #ffffff66;border-radius:7px;background:#ffffff10;color:#fff;padding:7px 10px}header select option{color:#111}
.wrap{max-width:1450px;margin:16px auto;padding:0 18px}.intro,.card,.confirmed{background:#fff;border:1px solid var(--line);border-radius:10px}.intro{padding:14px 18px;margin-bottom:14px}.intro strong{color:var(--navy)}.card{margin:16px 0;overflow:hidden}.casehead{padding:15px 18px;border-bottom:1px solid var(--line)}.casehead h2{font-size:20px;margin:7px 0 0}.badge{display:inline-block;padding:2px 8px;border-radius:999px;background:#e9eef2;font-size:12px;font-weight:700}.badge.fix{background:#fff0c8;color:#754d00}.badge.source{background:#f9dddd;color:#812c2c}.badge.court{background:#e3f1fb;color:#174b70}.badge.fss{background:#fff0c7;color:#725000}
.source-link{margin-left:8px;color:#145d8d;font-weight:700}.source-link:visited{color:#65417a}
.why{padding:11px 14px;margin:12px 18px;background:#fff8e7;border-left:4px solid var(--amber)}.grid{display:grid;grid-template-columns:1fr 1fr;gap:0}.col{padding:16px 18px;min-width:0}.col+.col{border-left:1px solid var(--line)}h3{font-size:16px;margin:0 0 9px}h4{margin:14px 0 5px}.sourcebox{white-space:pre-wrap;max-height:500px;overflow:auto;background:#fafcfd;border:1px solid var(--line);border-radius:8px;padding:12px;word-break:break-word}.block{padding:10px 12px;margin:7px 0;background:#fafcfd;border:1px solid var(--line);border-radius:8px}.proposal{background:#f1faf5;border-color:#b9dfc9}.proposal b{color:#175b3b}.question{margin:12px 0;padding:10px 12px;background:#eef6fb;border-left:4px solid var(--blue)}
.review{padding:16px 18px;border-top:1px solid var(--line);background:#f9fbfc}.decisions button{padding:8px 12px;border:1px solid #aab5be;background:#fff;border-radius:7px;font-weight:700}.decisions button.on[data-value=approve]{background:var(--green);color:#fff}.decisions button.on[data-value=keep]{background:var(--blue);color:#fff}.decisions button.on[data-value=recheck]{background:var(--amber);color:#fff}.decisions button.on[data-value=exclude]{background:var(--red);color:#fff}textarea{width:100%;min-height:80px;margin-top:9px;border:1px solid #aab5be;border-radius:7px;padding:9px}.confirmed{padding:12px 16px}.confirmed li{margin:6px 0}.progress{font-weight:700}.hidden{display:none!important}
@media(max-width:850px){.stats{margin-left:0;width:100%}.grid{grid-template-columns:1fr}.col+.col{border-left:0;border-top:1px solid var(--line)}}@media print{header,.review{display:none}.sourcebox{max-height:none}.card{break-inside:avoid}}
</style></head><body><header><div class="head"><h1>5파트 · Codex 선검토 후 사람 확인</h1><div class="stats"><span class="stat">전수 점검 <b id="total"></b></span><span class="stat">자동 확인 <b id="confirmed"></b></span><span class="stat">사람 확인 <b id="human"></b></span><span class="stat progress" id="progress"></span></div></div><div class="tools"><select id="filter"><option value="all">사람 확인 전체</option><option value="codex_corrected">수정안 승인 필요</option><option value="needs_source">원문·유지 여부 결정</option></select><button id="next" type="button">다음 미결정</button><button id="export" type="button">결정 JSON 내려받기</button><button id="reset" type="button">내 결정 초기화</button></div></header>
<div class="wrap"><section class="intro"><strong>사람이 할 일은 9건뿐입니다.</strong> 왼쪽 원문과 현재 AI 내용을 보고, 오른쪽 Codex 수정안 또는 질문에 답해 주세요. 자동 확인된 14건은 아래 접힌 목록에서 확인만 할 수 있습니다.</section><details class="confirmed"><summary>자동 확인된 14건 보기 — 별도 작업 없음</summary><ul id="confirmedList"></ul></details><div id="cards"></div></div>
<script id="data" type="application/json">__DATA__</script><script>(()=>{'use strict';const D=JSON.parse(document.getElementById('data').textContent),KEY='part5-human-followup-'+D.reviewed_at;let R=JSON.parse(localStorage.getItem(KEY)||'{}');const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const label={codex_corrected:'수정안 승인 필요',needs_source:'원문·유지 여부 결정'};
function blocks(title,arr,key){return `<h4>${title}</h4>`+(arr||[]).map(x=>`<div class="block"><b>${esc(x.issue_id||'')}</b><div>${esc(x[key]||'내용 없음')}</div></div>`).join('')}
function holdings(arr){return '<h4>현재 결론</h4>'+(arr||[]).map(x=>`<div class="block"><b>${esc(x.issue_id||'')} · ${esc(x.결론||'분류 없음')} · 신뢰도 ${esc(x.confidence||'없음')}</b><div>${esc(x.법리_요약||'내용 없음')}</div></div>`).join('')}
function proposal(p){if(!p)return '<div class="block proposal">확정 수정안 없음</div>';return `<div class="block proposal"><b>제안 쟁점</b><div>${esc(p.issue)}</div></div><div class="block proposal"><b>제안 사실</b><div>${esc(p.fact)}</div></div><div class="block proposal"><b>제안 결론</b><div>${esc(p.conclusion)}</div></div><div class="block proposal"><b>제안 법리</b><div>${esc(p.legal_summary)}</div></div>`}
function render(){const f=document.getElementById('filter').value;document.getElementById('cards').innerHTML=D.human_items.map((x,i)=>{const d=R[x.case_id]||{},link=x.source_url?`<a class="source-link" href="${esc(x.source_url)}" target="_blank" rel="noopener noreferrer">공식 원문 열기</a>`:'';return `<article class="card ${f!=='all'&&x.status!==f?'hidden':''}" data-id="${esc(x.case_id)}"><div class="casehead"><span class="badge ${x.source}">${esc(x.source_label)}</span> <span class="badge ${x.status==='codex_corrected'?'fix':'source'}">${label[x.status]}</span>${link}<h2>${i+1}. ${esc(x.case_id)} · ${esc(x.title)}</h2></div><div class="why"><b>왜 사람이 봐야 하나</b><br>${esc(x.reason)}</div><div class="grid"><section class="col"><h3>① 원문·현재 AI 내용</h3><div class="sourcebox">${esc(x.raw_text)}</div>${blocks('현재 쟁점',x.issues,'쟁점문구')}${blocks('현재 사실',x.facts,'fact')}${holdings(x.holdings)}</section><section class="col"><h3>② Codex 1차 수정안</h3>${proposal(x.proposed)}<div class="question"><b>사람이 결정할 부분</b><br>${esc(x.human_question)}</div></section></div><section class="review"><div class="decisions">${[['approve','수정안 승인'],['keep','현재 내용 유지'],['recheck','추가 재검토'],['exclude','자료 제외']].map(([v,t])=>`<button type="button" data-value="${v}" class="${d.decision===v?'on':''}">${t}</button>`).join('')}</div><textarea placeholder="판단 이유나 수정할 내용을 적으세요.">${esc(d.note||'')}</textarea></section></article>`}).join('');bind();progress()}
function bind(){document.querySelectorAll('.card').forEach(c=>{c.querySelectorAll('[data-value]').forEach(b=>b.onclick=()=>{const old=R[c.dataset.id]||{};R[c.dataset.id]={decision:b.dataset.value,note:old.note||'',at:new Date().toISOString()};save();render()});c.querySelector('textarea').oninput=e=>{const old=R[c.dataset.id]||{};R[c.dataset.id]={...old,note:e.target.value,at:new Date().toISOString()};save();progress()}})}function save(){localStorage.setItem(KEY,JSON.stringify(R))}function progress(){const n=D.human_items.filter(x=>R[x.case_id]?.decision).length;document.getElementById('progress').textContent=`${n} / ${D.human_items.length} 결정`}
document.getElementById('total').textContent=D.total_checked;document.getElementById('confirmed').textContent=D.confirmed_items.length;document.getElementById('human').textContent=D.human_items.length;document.getElementById('confirmedList').innerHTML=D.confirmed_items.map(x=>`<li><b>${esc(x.case_id)}</b> · ${esc(x.title)} — ${esc(x.reason)}</li>`).join('');document.getElementById('filter').onchange=render;document.getElementById('next').onclick=()=>{const x=D.human_items.find(x=>!R[x.case_id]?.decision);if(x)document.querySelector(`[data-id="${CSS.escape(x.case_id)}"]`)?.scrollIntoView({behavior:'smooth'});else alert('9건을 모두 결정했습니다.')};document.getElementById('reset').onclick=()=>{if(confirm('이 브라우저에 저장된 결정을 모두 지울까요?')){R={};localStorage.removeItem(KEY);render()}};document.getElementById('export').onclick=()=>{const missing=D.human_items.filter(x=>!R[x.case_id]?.decision);if(missing.length&&!confirm(`${missing.length}건이 아직 결정되지 않았습니다. 미완성 상태로 내려받을까요?`))return;const out={part:5,reviewed_at:new Date().toISOString(),is_complete:missing.length===0,decisions:D.human_items.map(x=>({case_id:x.case_id,status:x.status,codex_proposed:x.proposed||null,human_decision:R[x.case_id]?.decision||'',human_note:R[x.case_id]?.note||''}))};const u=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)+'\n'],{type:'application/json'})),a=document.createElement('a');a.href=u;a.download='part5_human_decisions.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)};render()})();</script></body></html>'''


def main() -> int:
    payload = build_payload()
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUT_HTML.write_text(render_html(payload), encoding="utf-8")
    print(f"5파트 전수 점검: {payload['total_checked']}건")
    print(f"Codex 확인 완료: {len(payload['confirmed_items'])}건")
    print(f"사람 확인 HTML: {len(payload['human_items'])}건 → {OUT_HTML.relative_to(ROOT)}")
    print(f"검토 장부 → {OUT_JSON.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
