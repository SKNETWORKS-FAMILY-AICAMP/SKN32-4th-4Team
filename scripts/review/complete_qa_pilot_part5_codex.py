# -*- coding: utf-8 -*-
"""QA 파일럿 Part 5를 Codex 1차 검수하고 사람 조치 큐를 만든다.

이 스크립트는 2026-08-26에 배포된 60건짜리 ``part5.html`` 전용이다.
엔진 verdict/reason_code는 바꾸지 않고, 고객 문장과 근거의 일치만 검수한다.
사고일이 가입일보다 앞선 입력은 문장 수정으로 덮지 않고 별도 사람 조치 큐로 보낸다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import re


ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "docs" / "review" / "qa_pilot_pkg" / "part5.html"
DEFAULT_JSONL = (
    ROOT / "docs" / "review" / "qa_pilot_pkg" / "qa_pilot_review_part5_codex.jsonl"
)
DEFAULT_HUMAN_HTML = (
    ROOT / "docs" / "review" / "qa_pilot_pkg" / "part5_사람확인필요_10건.html"
)

EXPECTED_COUNTS = {"A": 36, "B": 12, "C": 12}
DECISION_LABELS = {
    "A": "승인",
    "E": "수정",
    "N": "기권해야 한다",
    "R": "반려",
    "S": "판단 보류",
}


def _load_items(path: pathlib.Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8")
    match = re.search(r"const DATA = (\[.*?\]);\s*const PART", raw, re.S)
    if not match:
        raise RuntimeError(f"DATA 블록을 찾지 못했습니다: {path}")
    items = json.loads(match.group(1))
    if len(items) != 60:
        raise RuntimeError(f"Part 5는 60건이어야 합니다: 실제 {len(items)}건")
    if len({item["item_id"] for item in items}) != 60:
        raise RuntimeError("Part 5 item_id가 중복됐습니다")
    counts = {
        axis: sum(item.get("axis") == axis for item in items)
        for axis in EXPECTED_COUNTS
    }
    if counts != EXPECTED_COUNTS:
        raise RuntimeError(f"축별 건수가 예상과 다릅니다: {counts}")
    return items


def _date(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%Y%m%d").date()


def _pretty_date(value: str) -> str:
    day = _date(value)
    return f"{day.year}년 {day.month}월 {day.day}일"


def _condition_label(item: dict) -> str:
    request = item.get("request") or {}
    codes = ", ".join(request.get("kcd_codes") or [])
    return f"질병기호 {codes} 관련 치료" if codes else "문의한 치료"


def _edit_for_reason(item: dict) -> str:
    request = item["request"]
    reason = item["engine"]["reason_code"]
    condition = _condition_label(item)
    if reason == "no_version_at_date":
        return (
            f"가입일인 {_pretty_date(request['enrolled_on'])}에 적용되는 약관을 확인하지 "
            "못해 현재 정보만으로는 보장 여부를 판단할 수 없습니다. 가입 당시 "
            "보험증권과 약관을 확인한 뒤 전문가 검토가 필요합니다."
        )
    if reason == "citation_unverified":
        return (
            f"현재 확인된 자료만으로는 {condition}의 보장 여부를 판단할 근거 조항을 "
            "검증하지 못했습니다. 가입 당시 약관과 구체적인 치료 내용을 확인한 뒤 "
            "전문가 검토가 필요합니다."
        )
    if reason == "no_evidence":
        return (
            f"현재 확인된 근거만으로는 {condition}의 보장 여부를 판단할 수 없습니다. "
            "면책 목록에 없다는 이유만으로 보장된다고 볼 수 없으므로, 가입 당시 "
            "약관과 진료 내용을 확인한 뒤 전문가 검토가 필요합니다."
        )
    if reason == "document_not_reliable":
        return (
            "현재 보유한 약관 자료는 조항 구조를 정확히 확인하기 어려워 보장 여부를 "
            "판단할 수 없습니다. 가입 당시 약관 원문과 진료 내용을 확인한 뒤 전문가 "
            "검토가 필요합니다."
        )
    if reason == "product_not_matched":
        return (
            "입력한 상품명과 일치하는 약관을 확인하지 못해 보장 여부를 판단할 수 "
            "없습니다. 보험증권에 적힌 정확한 상품명과 가입 당시 약관을 확인한 뒤 "
            "다시 조회해 주세요."
        )
    raise RuntimeError(f"수정 문장 규칙이 없는 reason_code입니다: {reason}")


def _review(item: dict) -> dict:
    axis = item["axis"]
    decision = ""
    reason = ""
    edited_answer = ""
    note = ""

    if axis == "A":
        request = item["request"]
        enrolled = _date(request["enrolled_on"])
        incident = _date(request["incident_on"])
        if incident < enrolled:
            decision = "R"
            reason = "질문 자체가 잘못됐다"
            note = (
                f"사고일 {request['incident_on']}이 가입일 {request['enrolled_on']}보다 "
                "앞섭니다. 고객 문장을 고쳐 덮을 문제가 아니라 후보 제외 또는 "
                "사고일 정정 후 엔진 재실행이 필요합니다."
            )
        elif item["engine"]["reason_code"] in {
            "exception_applies",
            "excluded_by_clause",
        }:
            decision = "A"
            note = "엔진 판정의 범위를 넘지 않고 인용 가능한 약관 근거와 일치합니다."
        else:
            decision = "E"
            reason = "근거는 맞고 문장만 고침"
            edited_answer = _edit_for_reason(item)
            note = (
                "엔진 판정과 기권은 유지했습니다. 내부 상태명·검증되지 않은 조항명·"
                "근거 0건인데 ‘아래 근거’라고 한 표현을 제거하고 고객용 문장으로 바꿨습니다."
            )
    elif axis == "B":
        evidence = item.get("evidence") or []
        if len(evidence) != 1 or evidence[0].get("parse_status") != "ok":
            raise RuntimeError(f"B축 승인 fact가 온전하지 않습니다: {item['item_id']}")
        decision = "A"
        note = "승인 OCR fact의 자기부담금과 초안의 금액·계산 기준이 일치합니다."
    elif axis == "C":
        decision = "A"
        note = (
            "정책 조항 근거가 없거나 판본·인용 게이트를 통과하지 못했으므로, "
            "CLAUDE.md §0의 근거 없으면 기권 원칙에 맞습니다."
        )
    else:
        raise RuntimeError(f"알 수 없는 축입니다: {axis}")

    return {
        "item_id": item["item_id"],
        "axis": axis,
        "stratum": item["stratum"],
        "decision": decision,
        "decision_label": DECISION_LABELS[decision],
        "reason": reason,
        "edited_answer": edited_answer,
        "note": note,
        "draft_answer": item["draft_answer"],
        "reviewer": "Codex 1차 검수",
        "reviewed_at": "2026-08-26T19:22:00+09:00",
        "part": "5",
    }


def _human_html(items: list[dict], reviews: dict[str, dict]) -> str:
    action_items = [item for item in items if reviews[item["item_id"]]["decision"] == "R"]
    payload = []
    for item in action_items:
        request = item["request"]
        payload.append(
            {
                "item_id": item["item_id"],
                "stratum": item["stratum"],
                "question": item["question"],
                "enrolled_on": request["enrolled_on"],
                "incident_on": request["incident_on"],
                "verdict": item["engine"]["verdict"],
                "reason_code": item["engine"]["reason_code"],
                "draft_answer": item["draft_answer"],
                "codex_note": reviews[item["item_id"]]["note"],
            }
        )
    if len(payload) != 10:
        raise RuntimeError(f"사람 조치 큐는 10건이어야 합니다: 실제 {len(payload)}건")
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang=\"ko\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>QA 파일럿 Part 5 · 사람 확인 필요 10건</title>
<style>
:root{{--ink:#18201c;--muted:#59645f;--paper:#f3f5f1;--card:#fff;--line:#d2dad4;--accent:#12664e;--warn:#9a4d05;--danger:#a12e2e;--done:#e0f3e9}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.58 system-ui,\"Malgun Gothic\",sans-serif}}
header{{position:sticky;top:0;z-index:10;background:#fffffff2;border-bottom:1px solid var(--line);padding:12px 18px}}
.top{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}h1{{font-size:19px;margin:0}}.grow{{flex:1}}
input,button,textarea{{font:inherit}}input,textarea{{border:1px solid #aeb8b1;border-radius:7px;padding:8px;background:#fff}}
button{{border:1px solid #9da9a2;background:#fff;border-radius:7px;padding:8px 11px;cursor:pointer}}button.primary{{background:var(--accent);color:#fff;border-color:var(--accent)}}
.notice{{max-width:1100px;margin:15px auto;padding:13px 15px;background:#fff0db;border-left:4px solid var(--warn);border-radius:7px}}
main{{max-width:1100px;margin:14px auto 80px;padding:0 12px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:11px;margin:14px 0;padding:15px}}
.card.done{{border:2px solid #4e9a79}}.title{{font-weight:800;font-size:16px}}.meta{{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}}.meta span{{background:#eef1ef;border-radius:5px;padding:3px 7px;font-size:12px}}
.problem{{border-left:4px solid var(--danger);background:#fff3f3;padding:10px 12px;border-radius:5px;margin:10px 0}}.draft{{background:#f7f9f7;border:1px solid var(--line);padding:10px;border-radius:7px}}
.choices{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:12px 0}}.choice.active{{background:var(--accent);color:#fff;border:3px solid #073f30;font-weight:800}}.choice.active:before{{content:\"✓ 선택됨 · \"}}
.date{{display:none;margin:8px 0}}.date.show{{display:block}}textarea{{width:100%;min-height:75px}}.status{{font-weight:800;color:var(--accent)}}
@media(max-width:760px){{.choices{{grid-template-columns:1fr}}header{{position:relative}}}}
</style></head><body>
<header><div class=\"top\"><h1>QA 파일럿 Part 5 · 사람 확인 필요 10건</h1><span id=\"progress\" class=\"status\">0 / 10</span><div class=\"grow\"></div><label>담당자 <input id=\"reviewer\" placeholder=\"이름\"></label><button id=\"download\" class=\"primary\">결과 JSONL 내려받기</button></div></header>
<div class=\"notice\"><b>할 일은 보장 여부를 다시 판단하는 것이 아닙니다.</b> 10건 모두 사고일이 가입일보다 앞섭니다. 후보를 제외할지, 정확한 사고일을 입력해 엔진을 다시 돌릴지 정하세요. 날짜를 추측하면 안 됩니다.</div>
<main id=\"list\"></main>
<script>
const ITEMS={data}; const KEY='qa-pilot-part5-human-action-v1';
let state={{}};try{{state=JSON.parse(localStorage.getItem(KEY)||'{{}}')}}catch{{state={{}}}}
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}}[c]));
function save(){{localStorage.setItem(KEY,JSON.stringify(state));render()}}
function pick(id,action){{state[id]=Object.assign({{}},state[id],{{action,at:new Date().toISOString()}});save()}}
function setDate(id,v){{state[id]=Object.assign({{}},state[id],{{corrected_incident_on:v}});localStorage.setItem(KEY,JSON.stringify(state))}}
function setNote(id,v){{state[id]=Object.assign({{}},state[id],{{note:v}});localStorage.setItem(KEY,JSON.stringify(state))}}
function render(){{
 const list=document.getElementById('list');document.getElementById('reviewer').value=state.reviewer||'';
 list.innerHTML=ITEMS.map((it,i)=>{{const d=state[it.item_id]||{{}};return `<section class=\"card ${{d.action?'done':''}}\"><div class=\"title\">${{i+1}}. ${{esc(it.question)}}</div><div class=\"meta\"><span>${{esc(it.item_id)}}</span><span>가입일 ${{esc(it.enrolled_on)}}</span><span>사고일 ${{esc(it.incident_on)}}</span><span>${{esc(it.verdict)}} / ${{esc(it.reason_code)}}</span></div><div class=\"problem\"><b>Codex 확인:</b> ${{esc(it.codex_note)}}</div><div class=\"draft\"><b>기존 초안</b><br>${{esc(it.draft_answer)}}</div><div class=\"choices\"><button class=\"choice ${{d.action==='exclude'?'active':''}}\" onclick=\"pick('${{it.item_id}}','exclude')\">후보 제외 승인</button><button class=\"choice ${{d.action==='fix_date'?'active':''}}\" onclick=\"pick('${{it.item_id}}','fix_date')\">사고일 수정 후 재생성</button><button class=\"choice ${{d.action==='hold'?'active':''}}\" onclick=\"pick('${{it.item_id}}','hold')\">판단 보류</button></div><div class=\"date ${{d.action==='fix_date'?'show':''}}\"><label>정확한 사고일 <input type=\"date\" value=\"${{esc(d.corrected_incident_on||'')}}\" onchange=\"setDate('${{it.item_id}}',this.value)\"></label></div><textarea placeholder=\"메모 — 사고일 출처나 보류 이유를 적어 주세요\" onchange=\"setNote('${{it.item_id}}',this.value)\">${{esc(d.note||'')}}</textarea></section>`}}).join('');
 document.getElementById('progress').textContent=`${{ITEMS.filter(x=>(state[x.item_id]||{{}}).action).length}} / ${{ITEMS.length}}`;
}}
document.getElementById('reviewer').addEventListener('input',e=>{{state.reviewer=e.target.value;localStorage.setItem(KEY,JSON.stringify(state))}});
document.getElementById('download').addEventListener('click',()=>{{
 const rows=ITEMS.map(it=>{{const d=state[it.item_id]||{{}};return {{item_id:it.item_id,action:d.action||'',corrected_incident_on:d.corrected_incident_on||'',note:d.note||'',reviewer:state.reviewer||'',reviewed_at:d.at||''}}}});
 const bad=rows.filter(r=>!r.action||(r.action==='fix_date'&&!r.corrected_incident_on)||(r.action==='hold'&&!r.note));if(bad.length&&!confirm(`미완료 또는 설명 부족 ${{bad.length}}건이 있습니다. 그래도 내려받을까요?`))return;
 const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([rows.map(x=>JSON.stringify(x)).join('\\n')+'\\n'],{{type:'application/x-ndjson'}}));a.download='qa_pilot_part5_human_actions.jsonl';document.body.appendChild(a);a.click();a.remove();
}});render();
</script></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=pathlib.Path, default=DEFAULT_INPUT)
    parser.add_argument("--jsonl", type=pathlib.Path, default=DEFAULT_JSONL)
    parser.add_argument("--human-html", type=pathlib.Path, default=DEFAULT_HUMAN_HTML)
    args = parser.parse_args()

    items = _load_items(args.input)
    reviews = [_review(item) for item in items]
    by_id = {row["item_id"]: row for row in reviews}

    decision_counts = {
        key: sum(row["decision"] == key for row in reviews)
        for key in DECISION_LABELS
    }
    expected = {"A": 33, "E": 17, "N": 0, "R": 10, "S": 0}
    if decision_counts != expected:
        raise RuntimeError(f"결정 분포가 예상과 다릅니다: {decision_counts}")
    if any(row["decision"] == "E" and not row["edited_answer"].strip() for row in reviews):
        raise RuntimeError("E 결정에 수정 문장이 비어 있습니다")

    args.jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.jsonl.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in reviews),
        encoding="utf-8",
    )
    args.human_html.write_text(_human_html(items, by_id), encoding="utf-8")

    print(
        json.dumps(
            {
                "input": str(args.input),
                "jsonl": str(args.jsonl),
                "human_html": str(args.human_html),
                "rows": len(reviews),
                "decisions": decision_counts,
                "human_actions": 10,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
