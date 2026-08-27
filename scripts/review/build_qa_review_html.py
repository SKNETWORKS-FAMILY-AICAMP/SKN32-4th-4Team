# -*- coding: utf-8 -*-
"""승인 QA 후보를 **사람 검수 꾸러미**로 만든다 — 05D §3-2-1.

    python -m scripts.review.build_qa_review_html            # 꾸러미 전체(권장)
    python -m scripts.review.build_qa_review_html --parts 1  # 혼자 볼 때 한 파일

★설계 근거는 05D §3-2-1 「검수 화면 요구사항」이다(코덱스 검토 2026-08-26).
  600건 라벨링이 **건당 43.2초**를 낸 이유를 살린다 — **한 화면, 한 키 입력.**

  항상 보인다   질문 · 초안 답변 · 강조된 근거 본문 · 회사·조항·쪽·sha12·parse
  기본 숨김     기계 심판 의견 · 원시 JSON        ★**결정 전에는 안 보여준다**
  단축키        축에 따라 다르다(아래) · 1~9 사유 · ←→ 이동 · Ctrl+Z 되돌리기
  자동저장      매 입력마다 localStorage. 새로고침·중단 후 **같은 자리로 복귀**

★**기계 의견을 먼저 보여주면 사람이 그것을 따라 찍는다.**
  15번 문서에서 같은 이유로 후보 파일에서 자동값을 뺐다. 여기서는 **결정한 뒤에** 펼쳐진다.

★**버튼 뜻이 축마다 다르다.** C축은 초안 자체가 「기권」이라
  `승인` = 기권이 맞다 이고, `반려` = 기권이 틀렸다(근거가 있다) 다.
  같은 키에 다른 뜻을 두면 잘못 찍는다 — 그래서 **라벨을 축에 맞춰 바꾼다.**

꾸러미 구성(600건 검수와 같은 얼개):
    index.html · 00_먼저읽기.html · 01_모범선택사례.html
    part1..5.html · manifest.json · README_먼저읽기.txt · 검수결과_반환체크리스트.txt
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
IN_DEFAULT = ROOT / "data" / "finetune" / "qa_pilot" / "candidates.jsonl"
OUT_DEFAULT = ROOT / "docs" / "review" / "qa_pilot_pkg"

#: 600건 실측 43.2초/건에 축별 난이도를 얹은 **추정**이다. 실측이 아니라고 화면에 적는다.
SEC_PER_ITEM = {"A": 40, "B": 45, "C": 60}

_CSS = """
 :root{--fg:#1a1a1a;--mut:#666;--bd:#e2e2e2;--bg:#fff;--acc:#0b5fa5;--warn:#a54b0b;
       --dang:#a51b1b;--ok:#0b7a3b;--card:#fafafa;--hl:#fff3c4}
 @media(prefers-color-scheme:dark){:root{--fg:#e8e8e8;--mut:#a0a0a0;--bd:#333;--bg:#161616;
       --acc:#6fb3ec;--warn:#e8a05a;--dang:#e88;--ok:#6cc48f;--card:#1e1e1e;--hl:#4a4020}}
 *{box-sizing:border-box}
 body{margin:0;font:15px/1.75 -apple-system,"Segoe UI","Malgun Gothic",sans-serif;
      color:var(--fg);background:var(--bg)}
 a{color:var(--acc)}
 h1{font-size:1.5rem;margin:0 0 .3rem}
 h2{font-size:1.15rem;margin:2rem 0 .5rem;padding-top:.6rem;border-top:1px solid var(--bd)}
 table{border-collapse:collapse;width:100%;margin:.6rem 0;font-size:.95rem}
 th,td{border:1px solid var(--bd);padding:.45rem .6rem;text-align:left;vertical-align:top}
 th{background:var(--card)}
 code,kbd{font:12.5px ui-monospace,Consolas,monospace}
 kbd{border:1px solid var(--bd);border-bottom-width:2px;border-radius:4px;
     padding:0 .35em;background:var(--card)}
 .lead{color:var(--mut)}
 .box{border:1px solid var(--bd);border-left:4px solid var(--acc);border-radius:0 6px 6px 0;
      padding:.7rem 1rem;background:var(--card);margin:.7rem 0}
 .warnbox{border-left:4px solid var(--dang);background:var(--card);padding:.6rem .9rem;margin:.6rem 0}
 .ex{border:1px solid var(--bd);border-radius:8px;padding:.7rem 1rem;margin:.7rem 0;background:var(--card)}
 .ex pre{white-space:pre-wrap;margin:.4rem 0;font:13px ui-monospace,Consolas,monospace;color:var(--mut)}
 .pick{color:var(--ok);font-weight:600}
"""

# ─────────────────────────────────────────────────────────────── 검수 화면 ──
_PART_HTML = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>승인 QA 검수 · 파트@@PART@@ — @@N@@건</title>
<style>@@CSS@@
 header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--bd);
        padding:.55rem 1rem;display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
 .bar{flex:1;min-width:150px;height:8px;background:var(--card);border-radius:4px;overflow:hidden}
 .bar>i{display:block;height:100%;background:var(--ok);width:0}
 main{max-width:1000px;margin:0 auto;padding:1.1rem 1rem 7rem}
 .q{font-size:1.15rem;font-weight:600;margin:.2rem 0 .1rem}
 .ask{color:var(--warn);font-weight:600;margin:.5rem 0 1rem}
 .draft{border:1px solid var(--bd);border-left:4px solid var(--acc);border-radius:0 6px 6px 0;
        padding:.7rem 1rem;background:var(--card);margin:.6rem 0}
 .meta{font:12.5px ui-monospace,Consolas,monospace;color:var(--mut);margin:.35rem 0 .1rem;
       word-break:break-all}
 .ev{border:1px solid var(--bd);border-radius:8px;padding:.8rem 1rem;margin:.6rem 0;
     background:var(--card);white-space:pre-wrap;max-height:340px;overflow:auto}
 mark{background:var(--hl);color:inherit;padding:0 .1em;border-radius:2px}
 .btns{position:fixed;left:0;right:0;bottom:0;background:var(--bg);border-top:1px solid var(--bd);
       padding:.55rem 1rem;display:flex;gap:.45rem;justify-content:center;flex-wrap:wrap}
 button{font:inherit;padding:.42rem .85rem;border:1px solid var(--bd);border-radius:7px;
        background:var(--card);color:var(--fg);cursor:pointer}
 button.on{outline:2px solid var(--acc)}
 button.A{border-color:var(--ok)} button.N{border-color:var(--warn)}
 button.R{border-color:var(--dang)} button.S{border-color:var(--mut)}
 button.f.on{background:var(--acc);color:#fff}
 details{margin:.5rem 0}
 summary{cursor:pointer;color:var(--mut);font-size:.9rem}
 input[type=text]{padding:.4rem .55rem;border:1px solid var(--bd);border-radius:6px;
                  background:var(--bg);color:var(--fg);font:inherit}
 #note{width:100%}
 .chip{display:inline-block;font-size:12px;padding:.1rem .5rem;border:1px solid var(--bd);
       border-radius:99px;color:var(--mut);margin-right:.3rem}
 .done{color:var(--ok);font-weight:600}
</style></head><body>
<header>
  <b>승인 QA 검수 · 파트@@PART@@</b>
  <label class="chip">검수자 <input type="text" id="who" size="8" placeholder="이름"></label>
  <span class="chip" id="pos"></span>
  <div class="bar"><i id="prog"></i></div>
  <span class="chip" id="cnt"></span>
  <a href="00_먼저읽기.html">쉬운 설명 다시 보기</a>
  <button id="nextTodo">다음 미완료</button>
  <label class="chip">이전 결과 불러오기<input id="importFile" type="file" accept=".jsonl,.json" hidden></label>
  <button onclick="exportJsonl()">결과 JSONL 내려받기</button>
  <button onclick="if(confirm('이 파트의 저장된 결정을 모두 지웁니다. 계속?'))reset()">초기화</button>
</header>
<div id="filters" style="padding:.4rem 1rem;border-bottom:1px solid var(--bd)"></div>
<div id="nosave" style="display:none;background:#a51b1b;color:#fff;padding:.6rem 1rem;font-weight:600"></div>
<main id="app"></main>
<div class="btns" id="btns"></div>
<script>
const DATA = @@DATA@@;
const PART = "@@PART@@";
const KEY  = "qa_pilot_review::" + PART;
@@JS@@
</script></body></html>
"""

_PART_JS = r"""
//: ★버튼 뜻이 **축마다 다르다.** C축 초안은 그 자체가 「기권」이라
//:   승인 = 기권이 맞다, 반려 = 기권이 틀렸다(근거가 있다) 가 된다.
const CHOICES = {
  //: ★`N`(기권해야 한다) 을 뺐다 — 파트1·2·4·5 **240건에 한 건도 안 쓰였고**,
  //:   코덱스도 「인용이 있지만 질문을 못 받치면 N 이 아니라 R」이라고 판정했다.
  //:   쓰이지 않는 선택지는 고르는 사람을 헷갈리게만 한다.
  A: [["A","승인 — 이 문장 그대로"], ["E","수정"],
      ["R","반려 — 판정·근거와 안 맞다"], ["S","판단 보류"]],
  B: [["A","승인 — 금액이 맞다"], ["E","수정"],
      ["R","반려 — 짝이 안 맞다"], ["S","판단 보류"]],
  C: [["A","승인 — 기권이 맞다"], ["E","문장 수정"],
      ["R","반려 — 근거가 있다"], ["S","판단 보류"]],
};
const REASONS = {
  A: [],
  E: ["근거는 맞고 문장만 고침","금액·수치 정정","조항은 맞고 범위 정정"],
  R: ["근거가 주장을 받치지 않는다","다른 판본의 조항이다","본문이 잘려 판단 불가",
      "질문 자체가 잘못됐다","다른 조항에 답이 있다"],
  S: ["원문만으로 확실히 못 정하겠다","전문 지식이 필요하다"],
};

//: ★자동저장이 되는 브라우저인지 **먼저 확인한다.**
//:   `file://` · `data:` · 시크릿창에서 localStorage 가 막히는 경우가 있다.
//:   조용히 실패하면 60건을 다 찍고 나서야 잃은 걸 안다 — CLAUDE.md §0, 신호 없는 실패 금지.
const STORAGE_OK = (() => {
  try { localStorage.setItem(KEY + "::probe", "1"); localStorage.removeItem(KEY + "::probe"); return true; }
  catch { return false; }
})();

let state = load(), idx = state.cursor || 0, undo = [], filter = "all";

function load(){
  if (!STORAGE_OK) return {v:{}, cursor:0, who:"", log:[]};
  try { return JSON.parse(localStorage.getItem(KEY)) || {v:{}, cursor:0, who:"", log:[]}; }
  catch { return {v:{}, cursor:0, who:"", log:[]}; }
}
function save(){
  if (!STORAGE_OK) return;
  try { localStorage.setItem(KEY, JSON.stringify(state)); }
  catch(e) {
    const b = document.getElementById("nosave");
    if (b) { b.style.display = "block"; b.textContent = "⚠ 자동저장 실패 (" + e.message
             + ") — 지금 바로 「결과 JSONL 내려받기」를 누르세요. 창을 닫으면 잃습니다."; }
  }
}
function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function md(s){ return esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>"); }
function labelOf(axis, key){
  const row = (CHOICES[axis]||CHOICES.A).find(c => c[0] === key);
  return row ? row[1].split(" — ")[0] : key;
}
function highlight(text, q){
  //: 질의 어절을 근거 본문에서 강조한다 — 눈이 갈 곳을 줄이는 것이 속도의 핵심이다.
  let out = esc(text);
  const toks = String(q||"").split(/\s+/).filter(t => t.length >= 2).slice(0, 8);
  for (const t of toks) {
    const re = new RegExp(esc(t).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g");
    out = out.replace(re, m => "<mark>" + m + "</mark>");
  }
  return out;
}
function incomplete(it){
  //: ★★**끝나지 않은 결정**을 한 자리에서 정의한다.
  //:   파트1 에서 `E` 5건이 고친 문장 없이, `R` 19건이 메모 없이 나갔다.
  //:   화면은 경고만 하고 **내려받기를 막지 않았다** — 경고만 하고 통과시키면
  //:   그건 신호가 아니라 장식이다(CLAUDE.md §0).
  const d = state.v[it.item_id];
  if (!d || !d.decision) return "";
  if (d.decision === "E" && !(d.edited_answer || "").trim())
    return "「수정」인데 고친 문장이 없습니다";
  if ((d.decision === "R" || d.decision === "S") && !(d.note || "").trim())
    return "「" + labelOf(it.axis || "A", d.decision) + "」인데 메모가 없습니다";
  return "";
}
function incompleteList(){ return DATA.filter(it => incomplete(it)); }

function stateOf(it){
  const d = state.v[it.item_id];
  if (!d || !d.decision) return "todo";
  if (incomplete(it)) return "todo";   //: 끝나지 않은 것은 **완료로 세지 않는다**
  if (d.decision === "S" || d.decision === "E") return "unsure";
  return "done";
}
function matches(it){
  if (filter === "all") return true;
  if (filter === "inc") return !!incomplete(it);
  if (filter === "need") return !!(it.cons && it.cons.needs_human)
                              && !(state.v[it.item_id] || {}).decision;
  return stateOf(it) === filter;
}

function renderFilters(){
  const counts = {all: DATA.length, todo:0, done:0, unsure:0};
  DATA.forEach(it => counts[stateOf(it)]++);
  const names = {all:"전체", todo:"미완료", done:"완료", unsure:"보류·수정"};
  const inc = incompleteList().length;
  const need = DATA.filter(x => x.cons && x.cons.needs_human
                                && !(state.v[x.item_id] || {}).decision).length;
  document.getElementById("filters").innerHTML =
    ["all","todo","done","unsure"].map(k =>
      `<button class="f ${filter===k?"on":""}" onclick="setFilter('${k}')">${names[k]} ${counts[k]}</button>`
    ).join(" ")
    + (need ? ` <button class="f ${filter==="need"?"on":""}" style="border-color:var(--warn)"
                onclick="setFilter('need')">사람 필요 ${need}</button>` : "")
    + (inc ? ` <button class="f ${filter==="inc"?"on":""}" style="border-color:var(--dang)"
                onclick="setFilter('inc')">미완성 ${inc}</button>` : "");
}
function setFilter(k){
  filter = k;
  //: 필터를 걸면 **그 필터에 맞는 첫 항목으로** 옮긴다. 안 그러면 빈 화면처럼 보인다.
  const first = DATA.findIndex(matches);
  if (first >= 0) idx = first;
  render();
}

function render(){
  const it = DATA[idx];
  if (!it) return;
  const d = state.v[it.item_id] || {};
  const axis = it.axis || "A";
  document.getElementById("pos").textContent = `${idx+1} / ${DATA.length}`;
  document.getElementById("who").value = state.who || "";
  const done = DATA.filter(x => (state.v[x.item_id]||{}).decision && !incomplete(x)).length;
  const pending = incompleteList().length;
  document.getElementById("cnt").innerHTML = `완료 <span class="done">${done}</span> / ${DATA.length}`
    + (pending ? ` · <span style="color:var(--dang)">미완성 ${pending}</span>` : "");
  document.getElementById("prog").style.width = (done/DATA.length*100) + "%";
  renderFilters();

  const ev = (it.evidence||[])[0];
  let evHtml = "";
  if (ev) {
    const bad = [];
    //: ★`parse_status` 가 **비어 있는 것**도 경고다. 처음엔 falsy 로 걸러 버려서
    //:   「게이트 미기입」 항목 15건이 아무 표시 없이 나갔다(2026-08-26 실측으로 고침).
    //:   DB 실측: s6 발생 중 `parse_status IS NULL` 이 7,045건이다. 드문 일이 아니다.
    if (ev.parse_status == null) bad.push("인용 게이트 값(<b>parse_status</b>)이 <b>비어 있음</b>");
    else if (ev.parse_status !== "ok") bad.push(`parse_status=<b>${esc(ev.parse_status)}</b>`);
    if (ev.citation_eligible === false) bad.push("<b>인용 불가</b> 조항");
    evHtml = `
      <div class="meta">${esc(ev.insurer||"")} · ${esc(ev.qualified_no||"")}
        · p.${esc(ev.page_from)}–${esc(ev.page_to)} · sha12 <b>${esc(ev.sha12||"")}</b>
        · <span title="조항 ID">${esc(ev.clause_id||"")}</span>
        · parse=${ev.parse_status == null ? "<b>비어 있음</b>" : esc(ev.parse_status)}</div>
      ${bad.length ? `<div class="warnbox">⚠ ${bad.join(" · ")} — 근거로 쓸 수 없는 조항일 수 있습니다</div>` : ""}
      <div class="ev">${highlight(ev.text||"", it.question)}</div>`;
  } else {
    //: ★같은 「근거 0건」이라도 축마다 볼 것이 다르다. 문구를 축에 맞춘다.
    evHtml = axis === "A"
      ? `<div class="warnbox">인용 <b>0건</b> — 엔진이 근거를 대지 못한 상태입니다.
           <b>문장이 단정하고 있지 않은지</b> 보세요</div>`
      : `<div class="warnbox">근거 <b>0건</b> — 이 상태에서 기권이 정답인지 보는 항목입니다</div>`;
  }

  //: ★★엔진 판정은 **입력**이라 결정 전에도 보여 준다.
  //:   「기계 의견은 결정 전 숨김」 규칙과 어긋나 보이지만 다르다 —
  //:   숨기는 것은 **사람이 내릴 판단의 답**(초안 출처·원시 JSON)이고,
  //:   이건 **바꿀 수 없는 전제**다. 안 보여주면 문장이 판정과 맞는지 볼 수가 없다.
  const VER = {likely_covered:"보장 가능성", unlikely:"면책 가능성",
               needs_documents:"서류 필요", needs_expert:"전문가 확인"};
  //: ★★기계가 **먼저 훑은 결과**를 보여 준다. 판단이 아니라 확인 가능한 사실이다 —
  //:   「이 문장에 `parse_status` 가 있다」, 「인용문에 F32 가 범위로 들어 있다」.
  //:   사람이 하던 대조를 미리 끝내 둔 것이지, 결정을 대신한 것이 아니다.
  //:   결정은 여전히 사람이 키를 눌러야 저장된다(05D §3-3).
  const tg = it.triage || null;
  let tgHtml = "";
  if (tg) {
    const chk = (tg.checks || []).map(c =>
      `<div>${c.ok ? "✔" : "✘"} <b>${esc(c.check)}</b> — ${esc(c.result)}`
      + (c.note ? ` <span class="lead">(${esc(c.note)})</span>` : "")
      //: ★「있다」만 보여주면 안 된다 — 범위가 딴 문단에 있고 인용된 줄은
      //:   상관없는 내용일 수 있다. **맞은 자리를 그대로** 보여 준다.
      + (c.context
          ? `<div class="ev" style="max-height:120px;margin:.35rem 0 0">…${esc(c.context)}…</div>`
          : "")
      + `</div>`).join("");
    const def = (tg.defects || []).map(d =>
      `<div>• <b>${esc(d.rule)}</b> <code>${esc(d.hit)}</code> — ${esc(d.why)}</div>`).join("");
    const prop = tg.proposed_decision
      ? `<div style="margin-top:.5rem">제안: <b>${esc(labelOf(it.axis || "A", tg.proposed_decision))}</b>
           <span class="lead">(${esc(tg.proposed_why)})</span> —
           <kbd>P</kbd> 로 제안대로 적용</div>`
      : `<div style="margin-top:.5rem" class="lead">제안 없음 — ${esc(tg.proposed_why)}</div>`;
    const grp = (tg.group_size > 1)
      ? `<div style="margin-top:.4rem">이 문장은 이 파트에서 <b>${tg.group_size}건</b>에
           똑같이 나옵니다 — <kbd>G</kbd> 로 <b>같은 문장 전부에 함께 적용</b></div>`
      : "";
    tgHtml = `<div class="${(tg.defects||[]).length ? "warnbox" : "box"}">
        <b>기계가 먼저 훑은 결과</b> <span class="lead">(규칙만 씀 · 결정은 사람이 합니다)</span>
        ${chk}${def}${prop}${grp}</div>`;
  }

  //: ★★먼저 나온 판단을 **버리지도, 그대로 따르지도** 않는다.
  //:   등급과 문장 후보는 **결정 전에** 보여 준다 — 일을 두 번 하지 않으려면 필요하다.
  //:   누가 무엇을 결정했는지는 **접어 둔다** — 펼치면 보이되, 먼저 보고 따라 찍지 않도록.
  const cs = it.cons || null;
  const GRADE = {agreed:"규칙·검수 일치", disputed:"★서로 다름", review_only:"검수만 있음",
                 rule_only:"규칙 제안만", untouched:"★아무 판단 없음"};
  let csHtml = "";
  if (cs) {
    const hard = (cs.grade === "disputed" || cs.grade === "untouched");
    const cands = (cs.문장후보 || []).map((x, i) =>
      `<div style="margin:.3rem 0">
         <button onclick="useCandidate(${i})">이 문장 쓰기</button>
         <span class="lead">${esc(x.출처)}</span>
         <div class="ev" style="max-height:110px;margin:.25rem 0 0">${esc(x.문장)}</div>
       </div>`).join("");
    const revs = (cs.검수 || []).map(r =>
      `<div>• <b>${esc(r.출처)}</b> <span class="lead">(${esc(r.종류)})</span>
         → <b>${esc(labelOf(it.axis || "A", r.decision))}</b>
         ${r.reason ? " — " + esc(r.reason) : ""}
         ${r.note ? `<div class="lead">${esc(r.note)}</div>` : ""}</div>`).join("");
    csHtml = `<div class="${hard ? "warnbox" : "box"}">
        <b>먼저 나온 판단</b> — 등급 <b>${esc(GRADE[cs.grade] || cs.grade)}</b>
        ${cs.needs_human ? ` <span style="color:var(--dang)">사람이 봐야 하는 항목</span>` : ""}
        ${cands ? `<div style="margin-top:.5rem"><b>문장 후보</b>${cands}</div>` : ""}
        ${revs ? `<details style="margin-top:.4rem">
            <summary>다른 사람은 뭐라고 했나 (${(cs.검수 || []).length}명) — 먼저 보지 않는 편이 낫습니다</summary>
            ${revs}</details>` : ""}
      </div>`;
  }

  const eg = it.engine ? `
    <div class="box"><b>엔진 판정 — 이미 정해진 것입니다. 바꾸지 마세요.</b><br>
      판정 <b>${esc(VER[it.engine.verdict] || it.engine.verdict)}</b>
      <code>${esc(it.engine.verdict)}</code>
      · 사유 <code>${esc(it.engine.reason_code)}</code>
      · 기권 <b>${it.engine.abstained ? "예" : "아니오"}</b>
      · 인용 ${esc(it.engine.citations)}건
      ${it.request ? `<div class="meta">가입 ${esc(it.request.enrolled_on)}
        · 사고 ${esc(it.request.incident_on)} · 질병기호 ${esc((it.request.kcd_codes||[]).join(", "))}</div>` : ""}
    </div>` : "";

  const r = it.reference;
  const ref = r ? `
    <details><summary>참고 — 판례·분쟁조정례 (펼치기)</summary>
      <div class="meta">${esc(r.case_id||"")} · ${esc(r.authority||"")}
        · 사건결론 ${esc(r.case_verdict||"")}</div>
      <div class="ev">${esc(r.holding||"")}</div></details>` : "";

  //: ★기계 의견·원시 JSON 은 **결정한 뒤에만** 펼쳐진다.
  const after = d.decision ? `
    <details><summary>초안 출처·원시 데이터 (결정 후 확인용)</summary>
      <div class="meta">draft_source = ${esc(it.draft_source)}</div>
      <div class="ev">${esc(JSON.stringify(it, null, 1))}</div></details>` : "";

  document.getElementById("app").innerHTML = `
    <span class="chip">${esc(axis)}축</span><span class="chip">${esc(it.stratum)}</span>
    <span class="chip">${esc(it.item_id)}</span>
    <div class="q">${esc(it.question)}</div>
    <div class="ask">${md(it.ask)}</div>
    ${(it.triage && it.triage.extra_ask)
        ? `<div class="warnbox"><b>추가로 볼 것</b><br>${md(it.triage.extra_ask)}</div>` : ""}
    ${eg}${tgHtml}${csHtml}
    <div class="draft"><b>고객에게 나갈 문장 (초안)</b><br>${esc(it.draft_answer)}</div>
    ${evHtml}${ref}
    ${d.decision === "E" ? `
    <div style="margin-top:.9rem">
      <b>고친 답변</b> — ★여기에 적은 문장이 학습 데이터가 됩니다. 비워 두면 「수정」이 아무 것도 남기지 않습니다.
      <textarea id="edit" rows="4" oninput="setEdit(this.value)"
        style="width:100%;padding:.5rem .6rem;border:1px solid var(--bd);border-radius:6px;
               background:var(--bg);color:var(--fg);font:inherit;margin-top:.3rem"
        >${esc(d.edited_answer != null ? d.edited_answer
                : ((it.triage && it.triage.proposed_answer) || it.draft_answer))}</textarea>
      ${(d.edited_answer||"").trim() ? "" : `<div class="warnbox">아직 고친 문장이 없습니다 — 적고 <kbd>→</kbd> 로 넘어가세요</div>`}
    </div>` : ""}
    <div style="margin-top:.9rem">
      <input type="text" id="note" placeholder="메모 (보류·수정·반려는 한 줄 남겨 주세요)"
             value="${esc(d.note||"")}" onchange="setNote(this.value)">
    </div>
    ${d.decision
        ? (incomplete(it)
            ? `<div class="warnbox">⚠ ${esc(incomplete(it))} — 채워야 내려받을 수 있습니다</div>`
            : `<p class="done">✔ ${esc(labelOf(axis, d.decision))}${d.reason?" — "+esc(d.reason):""}</p>`)
        : ""}
    ${after}`;

  const bs = (CHOICES[axis]||CHOICES.A).map(([k, lab]) =>
    `<button class="${k} ${d.decision===k?"on":""}" onclick="decide('${k}')">
       <kbd>${k}</kbd> ${esc(lab)}</button>`).join("");
  const rs = (d.decision && REASONS[d.decision] && REASONS[d.decision].length)
    ? REASONS[d.decision].map((x,i) =>
        `<button class="${d.reason===x?"on":""}" onclick="setReason(${i})"><kbd>${i+1}</kbd> ${esc(x)}</button>`).join("")
    : "";
  document.getElementById("btns").innerHTML =
    bs + (rs ? `<span style="width:100%;height:0"></span>` + rs : "") +
    `<span style="width:100%;height:0"></span>
     <button onclick="go(-1)"><kbd>←</kbd> 이전</button>
     <button onclick="go(1)">다음 <kbd>→</kbd></button>
     <button onclick="undoLast()"><kbd>Ctrl+Z</kbd> 되돌리기</button>`
    + (it.triage && it.triage.proposed_decision
        ? `<button onclick="applyProposal(false)"><kbd>P</kbd> 제안대로</button>`
          + (it.triage.group_size > 1
              ? `<button onclick="applyProposal(true)"><kbd>G</kbd> 같은 문장 ${it.triage.group_size}건 함께</button>`
              : "")
        : "");
  state.cursor = idx; save();
}

function decide(k){
  const it = DATA[idx], axis = it.axis || "A";
  if (!(CHOICES[axis]||CHOICES.A).some(c => c[0] === k)) return;   //: 이 축에 없는 키는 무시
  undo.push(JSON.stringify(state.v));
  state.v[it.item_id] = Object.assign({}, state.v[it.item_id], {
    decision: k, reason: "", at: new Date().toISOString(),
  });
  state.log.push({item: it.item_id, decision: k, at: new Date().toISOString()});
  save();
  //: 사유가 필요 없는 「승인」은 바로 다음으로 — 이게 43.2초를 만든 동작이다.
  if (!REASONS[k] || !REASONS[k].length) { go(1); return; }
  render();
  if (k === "E") { const el = document.getElementById("edit"); if (el) { el.focus(); el.select(); } }
  //: ★반려·보류는 **왜**가 없으면 나중에 되짚을 수 없다. 커서를 메모로 보낸다.
  if (k === "R" || k === "S") { const el = document.getElementById("note"); if (el) el.focus(); }
}
function useCandidate(i){
  //: 후보 문장을 **골라 담는다.** 담으면 결정은 자동으로 `E` 가 된다 —
  //: 문장을 고쳤다는 뜻이므로.
  const it = DATA[idx], cs = it.cons;
  if (!cs || !(cs.문장후보 || [])[i]) return;
  undo.push(JSON.stringify(state.v));
  state.v[it.item_id] = Object.assign({}, state.v[it.item_id], {
    decision: "E", reason: "근거는 맞고 문장만 고침",
    edited_answer: cs.문장후보[i].문장,
    note: (state.v[it.item_id] || {}).note || ("문장 후보 채택: " + cs.문장후보[i].출처),
    at: new Date().toISOString(),
  });
  save(); render();
}
function applyProposal(spread){
  //: ★제안대로 적용 — **사람이 키를 눌러야** 저장된다. 자동 승인이 아니다.
  const it = DATA[idx], tg = it.triage;
  if (!tg || !tg.proposed_decision) { alert("이 항목에는 제안이 없습니다."); return; }
  const targets = spread
    ? DATA.filter(x => x.triage && x.triage.group_size > 1
                    && x.triage.group_key === tg.group_key)
    : [it];
  if (spread && targets.length < 2) { alert("같은 문장이 이 파트에 하나뿐입니다."); return; }
  undo.push(JSON.stringify(state.v));
  const now = new Date().toISOString();
  for (const x of targets) {
    state.v[x.item_id] = Object.assign({}, state.v[x.item_id], {
      decision: tg.proposed_decision,
      reason: tg.proposed_decision === "E" ? "근거는 맞고 문장만 고침" : "",
      edited_answer: x.triage ? (x.triage.proposed_answer || "") : "",
      note: (state.v[x.item_id] || {}).note || "기계 제안대로 확인함",
      at: now, applied: spread ? "group" : "single",
    });
    state.log.push({item: x.item_id, decision: tg.proposed_decision,
                    at: now, via: spread ? "제안·묶음" : "제안"});
  }
  save();
  if (spread) alert(`같은 문장 ${targets.length}건에 적용했습니다.`);
  go(1);
}
function setReason(i){
  const it = DATA[idx], d = state.v[it.item_id];
  if (!d || !d.decision) return;
  const list = REASONS[d.decision] || [];
  if (i >= list.length) return;
  undo.push(JSON.stringify(state.v));
  d.reason = list[i]; save();
  const why = incomplete(it);
  if (why) {
    //: ★사유 버튼만으론 부족하다 — 파트1 은 사유를 다 골랐는데 메모가 0건이었다.
    render();
    const el = document.getElementById(d.decision === "E" ? "edit" : "note");
    if (el) el.focus();
    return;
  }
  go(1);
}
function setEdit(v){
  //: ★「수정」은 **정정 문장이 있어야** 수정이다. 결정만 찍고 넘어가면 남는 게 없다.
  const it = DATA[idx];
  state.v[it.item_id] = Object.assign({}, state.v[it.item_id], {edited_answer: v});
  save();
}
function setNote(v){
  const it = DATA[idx];
  state.v[it.item_id] = Object.assign({}, state.v[it.item_id], {note: v});
  save();
}
function go(n){
  //: 필터가 걸려 있으면 **그 필터에 맞는 다음 항목**으로 건너뛴다.
  let i = idx;
  for (let s = 0; s < DATA.length; s++) {
    i += n;
    if (i < 0 || i >= DATA.length) { i = Math.max(0, Math.min(DATA.length-1, i)); break; }
    if (matches(DATA[i])) break;
  }
  idx = i; render();
}
function nextTodo(){
  const start = idx;
  for (let s = 1; s <= DATA.length; s++) {
    const i = (start + s) % DATA.length;
    if (stateOf(DATA[i]) === "todo") { idx = i; render(); return; }
  }
  alert("미완료 항목이 없습니다.");
}
function undoLast(){
  if (!undo.length) return;
  state.v = JSON.parse(undo.pop()); save(); render();
}
function reset(){ state = {v:{}, cursor:0, who:"", log:[]}; idx = 0; undo = []; save(); render(); }

function rows(){
  return DATA.map(it => {
    const d = state.v[it.item_id] || {};
    return {
      item_id: it.item_id, axis: it.axis, stratum: it.stratum,
      decision: d.decision || "", decision_label: d.decision ? labelOf(it.axis||"A", d.decision) : "",
      reason: d.reason || "", edited_answer: d.edited_answer || "", note: d.note || "",
      draft_answer: it.draft_answer, reviewer: state.who || "", reviewed_at: d.at || "", part: PART,
    };
  });
}
function exportJsonl(){
  //: ★★**막는다.** 경고만 하고 통과시켰더니 파트1 에서 미완성 5건이 그대로 나갔다.
  const inc = incompleteList();
  if (inc.length) {
    filter = "inc"; idx = DATA.indexOf(inc[0]); render();
    alert("끝나지 않은 항목이 " + inc.length + "건 있어 내려받을 수 없습니다." + String.fromCharCode(10)
        + String.fromCharCode(10) + inc.slice(0, 5).map(x => "· " + x.item_id + " — " + incomplete(x)).join(String.fromCharCode(10))
        + (inc.length > 5 ? String.fromCharCode(10) + "… 외 " + (inc.length - 5) + "건" : "")
        + String.fromCharCode(10) + String.fromCharCode(10) + "「미완성」 필터로 옮겼습니다. 채운 뒤 다시 눌러 주세요.");
    return;
  }
  if (!(state.who||"").trim() && !confirm("검수자 이름이 비어 있습니다. 그대로 내려받을까요?")) return;
  const lines = rows().map(r => JSON.stringify(r)).join("\n");
  try {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([lines], {type:"application/x-ndjson"}));
    a.download = `qa_pilot_review_part${PART}.jsonl`;
    document.body.appendChild(a); a.click(); a.remove();
  } catch (e) {
    //: ★내려받기는 브라우저가 막을 수 있다. 실패하면 새 창에 텍스트로 띄운다.
    const w = window.open("", "_blank");
    w.document.write("<pre>" + lines.replace(/</g,"&lt;") + "</pre>");
  }
}
function importFile(file){
  const fr = new FileReader();
  fr.onload = () => {
    let n = 0, skipped = 0;
    const ids = new Set(DATA.map(d => d.item_id));
    for (const line of String(fr.result).split(/\r?\n/)) {
      if (!line.trim()) continue;
      let r; try { r = JSON.parse(line); } catch { continue; }
      //: ★다른 파트의 결과를 섞지 않는다. 모르는 item_id 는 **세어서 보고**한다.
      if (!r.item_id || !ids.has(r.item_id)) { skipped++; continue; }
      if (!r.decision) continue;
      state.v[r.item_id] = {decision: r.decision, reason: r.reason || "",
                            edited_answer: r.edited_answer || "", note: r.note || "",
                            at: r.reviewed_at || new Date().toISOString()};
      n++;
    }
    if (r_who(fr.result)) state.who = r_who(fr.result);
    save(); render();
    alert(`불러왔습니다: ${n}건 반영` + (skipped ? ` · 이 파트에 없는 항목 ${skipped}건은 무시` : ""));
  };
  fr.readAsText(file, "utf-8");
}
function r_who(text){
  for (const line of String(text).split(/\r?\n/)) {
    if (!line.trim()) continue;
    try { const r = JSON.parse(line); if (r.reviewer) return r.reviewer; } catch {}
  }
  return "";
}

document.getElementById("who").addEventListener("input", e => { state.who = e.target.value; save(); });
document.getElementById("nextTodo").addEventListener("click", nextTodo);
document.getElementById("importFile").addEventListener("change", e => {
  if (e.target.files && e.target.files[0]) importFile(e.target.files[0]);
});
document.querySelector('label.chip input#importFile').parentElement.addEventListener("click", e => {
  if (e.target.tagName !== "INPUT") document.getElementById("importFile").click();
});
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
  if (e.ctrlKey && e.key.toLowerCase() === "z") { e.preventDefault(); undoLast(); return; }
  const k = e.key.toUpperCase();
  if (k === "P") { e.preventDefault(); applyProposal(false); return; }
  if (k === "G") { e.preventDefault(); applyProposal(true); return; }
  if (["A","E","R","S"].includes(k)) { e.preventDefault(); decide(k); return; }
  if (/^[1-9]$/.test(e.key)) { e.preventDefault(); setReason(parseInt(e.key,10)-1); return; }
  if (e.key === "ArrowLeft")  { e.preventDefault(); go(-1); }
  if (e.key === "ArrowRight") { e.preventDefault(); go(1); }
});
if (!STORAGE_OK) {
  const b = document.getElementById("nosave");
  b.style.display = "block";
  b.textContent = "⚠ 이 브라우저에서는 자동저장이 안 됩니다 — 새로고침하면 결정이 사라집니다. "
                + "끝내기 전에 반드시 「결과 JSONL 내려받기」를 누르세요. "
                + "(압축을 풀어 로컬 디스크에서 직접 열면 대개 해결됩니다)";
}
render();
"""


def _page(title: str, body: str) -> str:
    return (f'<!DOCTYPE html>{chr(10)}<html lang="ko"><head><meta charset="utf-8">{chr(10)}'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">{chr(10)}'
            f'<title>{title}</title>{chr(10)}<style>{_CSS}'
            ' main{max-width:860px;margin:0 auto;padding:1.5rem 1rem 4rem}'
            f'{chr(10)}</style></head><body><main>{chr(10)}{body}{chr(10)}</main></body></html>{chr(10)}')


# ───────────────────────────────────────────────────────── 먼저 읽기 ──
def _guide(parts: list[tuple[int, list[dict]]]) -> str:
    rows = []
    for pi, mine in parts:
        cnt = {"A": 0, "B": 0, "C": 0}
        for r in mine:
            cnt[r["axis"]] = cnt.get(r["axis"], 0) + 1
        sec = sum(SEC_PER_ITEM.get(r["axis"], 45) for r in mine)
        rows.append(
            f'<tr><td>파트 {pi}</td><td>{len(mine)}</td>'
            f'<td>약 {sec//3600}시간 {sec%3600//60}분</td>'
            f'<td>A {cnt["A"]} · B {cnt["B"]} · C {cnt["C"]}</td>'
            f'<td><a href="part{pi}.html">검수 시작</a></td></tr>')
    body = f"""
<h1>먼저 읽기 · 승인 QA 파일럿 검수</h1>
<p class="lead">이 폴더만 있으면 작업할 수 있습니다. 프로그램 설치나 원본 데이터 폴더가 필요하지 않습니다.</p>
<p>하는 일은 간단합니다. <b>질문과 초안 답변</b>을 읽고, 바로 아래 <b>약관 원문</b>을 확인한 뒤,
가장 맞는 버튼 하나를 누릅니다. 마지막에 결과 파일을 내려받아 보내면 끝입니다.</p>
<p><a href="01_모범선택사례.html"><b>축별 모범 선택사례 먼저 보기</b></a></p>

<div class="warnbox"><b>화면에 약관 원문이 그대로 들어 있습니다.</b>
저작물이므로 외부에 올리거나 공유하지 마세요. 팀 안에서만 씁니다.</div>

<h2>1. 다섯 명에게 나누는 방법</h2>
<p>팀원마다 서로 다른 파트 하나를 맡으세요. 같은 파트를 두 명이 하면 결과가 겹칩니다.</p>
<table><tr><th>담당</th><th>항목</th><th>예상시간</th><th>구성</th><th>시작</th></tr>
{chr(10).join(rows)}</table>
<p class="lead">예상시간은 600건 검수 실측(건당 43.2초)에 축별 난이도를 얹은 <b>추정</b>입니다.
실제로 재 보는 것이 이번 파일럿의 목적 중 하나입니다.</p>

<h2>2. 이 검수가 무엇을 정하는가</h2>
<p>여기서 <b>승인</b>한 답변이 그대로 <b>모델 학습 데이터</b>가 됩니다.
틀린 것을 승인하면 그 오류를 모델이 배웁니다.
<b>확신이 없으면 승인하지 말고 「판단 보류」</b>를 누르세요. 보류는 실패가 아니라 정답입니다.</p>

<h2>3. 세 가지 축 — 무엇을 보는가</h2>
<table>
<tr><th>축</th><th>질문</th><th>확인할 것</th></tr>
<tr><td><b>A</b> 판정 문장</td><td>「고객에게 나갈 이 문장이 맞나?」</td>
    <td><b>엔진 판정</b>(보장 가능성·면책 가능성·서류 필요·전문가 확인)과
        <b>인용된 약관 원문</b>에 문장이 맞는지. ★판정 자체는 <b>이미 정해진 것</b>이라
        바꾸지 않습니다. 문장이 판정보다 세게·약하게 말하지 않는지 봅니다.</td></tr>
<tr><td><b>B</b> 표 금액</td><td>「표에서 읽은 금액이 질문과 짝이 맞나?」</td>
    <td>가입유형(표준형·선택형…)과 의료서비스(외래·처방조제…)가
        <b>둘 다</b> 질문과 같은 줄인지. 하나만 맞으면 틀린 것입니다.</td></tr>
<tr><td><b>C</b> 기권</td><td>「판정하지 않은 것이 맞나?」</td>
    <td>근거가 <b>없다</b>는 초안입니다. 다른 조항이 답을 갖고 있으면 기권은 오답입니다.</td></tr>
</table>

<h2>4. 버튼은 이런 뜻입니다</h2>
<div class="warnbox"><b>C축은 버튼 뜻이 뒤집힙니다.</b> C축 초안은 그 자체가 「기권」이라
<b>승인 = 기권이 맞다</b>, <b>반려 = 기권이 틀렸다(근거가 있다)</b> 입니다.
화면의 버튼 글자가 축에 맞게 바뀌므로 <b>글자를 읽고</b> 누르세요.</div>
<table>
<tr><th>키</th><th>A·B축</th><th>C축</th><th>언제 누르나</th></tr>
<tr><td><kbd>A</kbd></td><td>승인 — 이 문장 그대로</td><td>승인 — 기권이 맞다</td>
    <td>초안을 그대로 학습에 써도 된다. <b>하나라도 틀리면 누르지 않습니다.</b></td></tr>
<tr><td><kbd>E</kbd></td><td colspan="2">수정</td>
    <td>근거는 맞는데 <b>문장</b>이 틀렸다. 입력란이 열리고,
        <b>거기 적은 문장이 학습 데이터가 됩니다.</b> 비워 두면 아무것도 안 남습니다.</td></tr>
<tr><td><kbd>R</kbd></td><td>반려</td><td>반려 — 근거가 있다</td>
    <td>초안이 틀렸다. A축은 「문장이 판정·근거와 안 맞다」,
        B축은 「금액과 질문의 짝이 안 맞다」,
        C축은 「기권할 게 아니라 답할 수 있다」.</td></tr>
<tr><td><kbd>S</kbd></td><td colspan="2">판단 보류</td>
    <td>원문만으로 확실히 못 정하겠다. <b>메모에 왜 어려운지 한 줄</b> 남깁니다.</td></tr>
</table>
<p>승인을 뺀 나머지는 <b>사유 버튼</b>이 이어서 뜹니다 — <kbd>1</kbd>~<kbd>9</kbd> 로 고릅니다.
사유를 고르면 자동으로 다음 항목으로 넘어갑니다.</p>
<p><kbd>P</kbd> 제안대로 적용 · <kbd>G</kbd> 같은 문장 전부에 적용 (아래 4-2 참고).</p>
<p>이동은 <kbd>←</kbd> <kbd>→</kbd>, 잘못 눌렀으면 <kbd>Ctrl</kbd>+<kbd>Z</kbd> 또는
같은 항목에서 올바른 버튼을 다시 누르면 덮어씁니다.</p>

<h2>4-2. 기계가 먼저 훑어 놓았습니다</h2>
<p>항목 위 상자에 <b>기계가 먼저 훑은 결과</b>가 있습니다. 판단이 아니라
<b>확인할 수 있는 사실</b>만 적혀 있습니다.</p>
<table>
<tr><th>줄</th><th>뜻</th></tr>
<tr><td>✔ / ✘ 대조</td><td>사람이 눈으로 하던 대조를 미리 끝낸 것입니다.
  「인용문에 그 질병기호가 있나」(범위 표기 <code>F04~F99</code> 도 봅니다),
  「가입유형·의료서비스가 질문과 같은 줄인가」. <b>✘ 인 것만 자세히 보세요.</b></td></tr>
<tr><td>• 결함</td><td>초안에서 찾은 결함입니다 — 내부용어 · 근거 없는 단정 ·
  「특정할 수 없다」면서 조항을 대는 모순 · 같은 조항 중복 · 붙어쓰기 ·
  인용이 0건인데 「아래 근거」라고 가리키는 것.</td></tr>
<tr><td>제안</td><td>결함이 있으면 <b>고쳐 쓸 문장</b>까지 제안합니다.
  <kbd>P</kbd> 를 누르면 그 제안대로 저장되고 다음으로 넘어갑니다.</td></tr>
<tr><td>같은 문장 n건</td><td>인용이 없는 항목은 <b>볼 것이 문장뿐</b>이라,
  글자까지 같은 문장은 한 번만 보면 됩니다. <kbd>G</kbd> 로 <b>함께 적용</b>합니다.
  ★인용이 있는 항목은 문장이 같아도 <b>근거가 달라</b> 묶지 않습니다.</td></tr>
</table>
<div class="warnbox"><b><kbd>P</kbd> 는 자동 승인이 아닙니다.</b>
제안을 읽고 맞다고 판단했을 때 누르세요. 틀렸으면 <kbd>E</kbd> 로 직접 고치거나
<kbd>R</kbd>·<kbd>S</kbd> 를 고르면 됩니다. <b>기계가 놓치는 것이 있습니다</b> —
규칙은 글자만 보지 내용이 맞는지는 모릅니다.</div>
<p class="lead">기계가 쓰는 것은 <b>규칙뿐</b>입니다(AI 아님). 그래서 왜 그렇게 제안했는지
항상 화면에 적혀 있습니다.</p>

<h2>4-3. 먼저 나온 판단이 붙어 있습니다</h2>
<p>이 항목들은 <b>이미 한 번씩 검수를 거쳤습니다</b>(팀원 2명 · LLM 2개).
그 결과를 버리지 않고 붙여 두었습니다.</p>
<table>
<tr><th>등급</th><th>뜻</th><th>할 일</th></tr>
<tr><td>규칙·검수 일치</td><td>규칙 제안과 앞선 검수가 같은 결정</td><td>확인만</td></tr>
<tr><td><b>★서로 다름</b></td><td>규칙과 검수가 갈렸다</td><td><b>직접 보세요</b></td></tr>
<tr><td>검수만 있음</td><td>규칙이 할 말이 없고 검수 결정만 있다</td><td>확인</td></tr>
<tr><td>규칙 제안만</td><td>아직 아무도 안 봤지만 규칙이 결함을 짚었다</td><td>제안 확인</td></tr>
<tr><td><b>★아무 판단 없음</b></td><td>규칙도 검수도 없다</td><td><b>직접 보세요</b></td></tr>
</table>
<p><b>문장 후보</b>가 여럿 있으면 「이 문장 쓰기」로 담습니다. 담으면 결정이 <kbd>E</kbd> 가 됩니다.
그대로 두지 말고 <b>읽고 나서</b> 담으세요.</p>
<div class="warnbox"><b>「다른 사람은 뭐라고 했나」는 접혀 있습니다.</b>
먼저 펼쳐 보면 그대로 따라 찍게 됩니다. 자기 판단을 정한 뒤 확인용으로 쓰세요.</div>
<p class="lead">위쪽 <b>「사람 필요 n」</b> 필터를 누르면 직접 봐야 하는 항목만 남습니다.</p>

<h2>5. 판단이 갈리는 자리</h2>
<div class="warnbox"><b>승인(<kbd>A</kbd>)의 최소선 — 이게 안 정해져 있어 사람마다 8배 갈렸습니다.</b>
<br>같은 60건을 두고 승인이 <b>5건인 사람과 40건인 사람</b>이 나왔습니다(2026-08-26 실측).
기준을 이렇게 못박습니다 — <b>넷 중 하나라도 걸리면 <kbd>A</kbd> 가 아닙니다.</b>
<ol>
<li><b>고객이 이 문장만 읽고 다음에 뭘 할지 알 수 있어야</b> 합니다.
    「○○ 조항이 근거입니다」처럼 <b>조항 제목만</b> 말하는 문장은 승인하지 않습니다 —
    그대로 학습하면 모델이 제목만 답합니다.</li>
<li>판정보다 <b>세게도 약하게도</b> 말하지 않아야 합니다.</li>
<li>내부 용어(<code>parse_status</code> 같은 필드명·상태값)가 없어야 합니다.</li>
<li>띄어쓰기가 깨져 있지 않아야 합니다. <b>숫자가 맞아도</b> 문장이 깨졌으면 <kbd>E</kbd> 입니다.</li>
</ol></div>
<div class="warnbox"><b><kbd>R</kbd> 반려 · <kbd>S</kbd> 보류는 메모가 필수입니다.</b>
사유 버튼만으로는 나중에 왜 그랬는지 되짚을 수 없습니다.
<b><kbd>E</kbd> 는 고친 문장이 없으면 끝난 것이 아닙니다.</b>
둘 다 <b>미완성으로 세고, 하나라도 남아 있으면 내려받기가 막힙니다.</b></div>
<div class="box"><b>엔진 판정은 바꿀 수 없습니다.</b> 판정이 「전문가 확인」인데
문장이 「보장되지 않습니다」처럼 <b>단정</b>하면 그건 문장이 틀린 것입니다 —
판정을 고치는 게 아니라 <kbd>E</kbd> 로 <b>문장</b>을 고칩니다.</div>
<div class="box"><b>노란 강조는 근거가 아닙니다.</b> 질문의 단어가 원문에 있다고 해서
그 조항이 답이 되지는 않습니다. 강조는 눈이 갈 곳을 줄이는 표시일 뿐입니다.</div>
<div class="box"><b>「보상하지 않는 사항」이 나왔다고 항상 맞는 건 아닙니다.</b>
질문이 묻는 <b>그 항목</b>이 그 목록에 있어야 합니다.</div>
<div class="box"><b>빨간 경고(<code>parse_status</code>·인용 불가)가 붙은 항목</b>은
근거로 쓸 수 없는 조항일 수 있습니다. 내용이 맞아 보여도 <kbd>R</kbd> 또는 <kbd>S</kbd> 를 고려하세요.</div>
<div class="box"><b>C축에서 「못 찾았다」와 「없다」는 다릅니다.</b>
원문을 읽어 보고 <b>다른 조항이 답을 갖고 있을 것 같으면</b> 기권이 오답입니다(<kbd>R</kbd>).
정말 근거가 없어 보이면 <kbd>A</kbd>.</div>

<h2>6. 저장과 재개</h2>
<p>버튼을 누를 때마다 <b>현재 브라우저에 자동 저장</b>됩니다. 창을 닫았다 다시 열면 같은 자리로 돌아옵니다.</p>
<p>다른 컴퓨터에서 이어 하려면 먼저 <b>결과 JSONL 내려받기</b> 로 저장한 뒤,
새 컴퓨터에서 <b>이전 결과 불러오기</b> 를 누릅니다.</p>
<p>브라우저 기록을 지우면 임시저장이 사라질 수 있으므로 <b>20~30개마다 한 번 내려받기</b>를 권합니다.</p>
<div class="warnbox">화면 맨 위에 <b>빨간 배너</b>가 떠 있으면 이 브라우저는 자동저장이 안 되는 상태입니다.
새로고침하면 다 사라지니, 끝내기 전에 반드시 내려받으세요.</div>

<h2>7. 완료 후 보내는 파일</h2>
<p>위쪽 <b>검수자</b> 칸에 이름을 적었는지, <b>미완료 0 · 미완성 0</b> 인지 확인합니다.
미완성이 남아 있으면 <b>내려받기 버튼이 막히고</b> 어느 항목인지 알려 줍니다.
그다음 <b>결과 JSONL 내려받기</b> 를 누르고,
받은 <code>qa_pilot_review_part1.jsonl</code> 같은 <b>파일 하나만</b> 취합 담당자에게 보냅니다.</p>
<p class="lead">보내면 안 되는 것: 화면 캡처만 보내기 · HTML을 직접 고치기 ·
다른 파트 결과를 한 파일에 합치기.</p>

<h2>8. 문제가 생겼을 때</h2>
<ul>
<li>화면이 비어 보이면 압축을 풀어서 다시 여세요. 메신저 미리보기에서 열지 마세요.</li>
<li>버튼을 눌러도 안 넘어가면 메모 칸에 커서가 있는지 보세요(입력 중에는 단축키가 안 먹습니다).</li>
<li>판단이 어려우면 억지로 승인하지 말고 <kbd>S</kbd> 와 이유를 남기세요.</li>
<li>결과 파일을 두 번 내려받으면 이름 뒤에 숫자가 붙습니다. <b>가장 마지막 것</b>을 보내세요.</li>
</ul>
"""
    return _page("먼저 읽기 · 승인 QA 파일럿 검수", body)


# ─────────────────────────────────────────────────── 모범 선택사례 ──
def _examples() -> str:
    body = """
<h1>축별 모범 선택사례</h1>
<p><a href="00_먼저읽기.html">← 먼저 읽기로 돌아가기</a></p>
<p class="lead">각 축에서 만날 수 있는 대표 상황입니다. 정답 버튼뿐 아니라
<b>왜 그렇게 고르는지</b>도 읽어 보세요.</p>
<div class="warnbox">이것은 판단 방법을 익히는 <b>연습 예시</b>입니다.
문장이 비슷하다는 이유만으로 그대로 복사하지 말고, 실제 항목의 원문을 확인하세요.</div>

<h2>A축 · 판정 문장</h2>
<p class="lead">엔진이 이미 <b>판정</b>과 <b>인용 조항</b>을 정해 놓았습니다.
보는 것은 <b>그 위에 얹힌 고객 문장</b>입니다.</p>

<div class="ex"><b>사례 A-1 · 면책 조항에 질병기호가 이름으로 있다</b>
<pre>엔진 판정: 면책 가능성 (unlikely) · excluded_by_clause · 인용 1건
인용 조항: … 5. 비만(E66) … 보상하지 않습니다
문장: E66에 대해 약관의 면책 조항과 일치하는 내용이 확인되었습니다.
      면책 가능성이 있는 결과이며 … 최종 지급 여부는 실제 사고 내용과
      청구 서류에 따라 달라질 수 있습니다.</pre>
<p class="pick">모범 선택: 승인 — 이 문장 그대로</p>
<p>이유: 인용 조항에 <b>E66 이 이름으로</b> 있고, 문장이 「가능성」과 「최종은 달라질 수 있다」까지
말해 판정보다 세게 나가지 않습니다.</p></div>

<div class="ex"><b>사례 A-2 · 문장이 판정보다 세게 말한다</b>
<pre>엔진 판정: 전문가 확인 (needs_expert) · no_evidence · 인용 0건
문장: 해당 치료는 보장되지 않습니다.</pre>
<p class="pick">모범 선택: 수정 → 「약관에서 근거를 찾지 못해 판정하지 않았습니다.
가입하신 상품의 약관으로 확인해 주세요.」</p>
<p>이유: 근거가 <b>0건</b>인데 문장이 <b>단정</b>했습니다. 이건 사람이 손해를 보는 방향의 오류입니다
(CLAUDE.md §0). 판정을 바꾸는 게 아니라 <b>문장</b>을 판정에 맞춥니다.</p></div>

<div class="ex"><b>사례 A-3 · 인용 조항이 질문의 질병기호와 무관하다</b>
<pre>질문: … 급성심근경색(I21)으로 입원했습니다. 보장되나요?
엔진 판정: 면책 가능성 · excluded_by_clause · 인용 1건
인용 조항: … 4. 선천성 뇌질환(Q00~Q04) … 보상하지 않습니다</pre>
<p class="pick">모범 선택: 반려 — 판정·근거와 안 맞다 → 사유 <kbd>1</kbd> 근거가 주장을 받치지 않는다</p>
<p>이유: 인용 조항 어디에도 <b>I21</b> 이 없습니다. 조항이 면책 조항이라는 이유만으로
면책이라 말할 수 없습니다.</p></div>

<div class="ex"><b>사례 A-4 · 판정은 「서류 필요」인데 문장이 그걸 안 말한다</b>
<pre>엔진 판정: 서류 필요 (needs_documents) · exception_applies
문장: 약관상 면책 조항이 확인되었습니다.</pre>
<p class="pick">모범 선택: 수정 → 어떤 서류가 왜 필요한지 한 줄 덧붙인다</p>
<p>이유: <code>exception_applies</code> 는 <b>단서 조항이 걸려 있다</b>는 뜻입니다.
그 단서가 적용되는지 보려면 진단서 등이 필요한데, 문장이 그 말을 빠뜨렸습니다.</p></div>

<div class="ex"><b>사례 A-5 · 문장은 맞는데 내부 용어가 그대로 나갔다</b>
<pre>문장: parse_status 가 ok 가 아니라 판정 근거로 쓸 수 없습니다.</pre>
<p class="pick">모범 선택: 수정 → 「해당 약관 문서를 정확히 읽지 못해 판정하지 않았습니다.」</p>
<p>이유: 판정도 근거도 맞습니다. 다만 <b>고객이 읽을 문장</b>에 내부 필드명을 쓰면 안 됩니다.</p></div>

<h2>B축 · 표에서 읽은 금액</h2>

<div class="ex"><b>사례 B-1 · 가입유형과 서비스가 둘 다 맞다</b>
<pre>질문: 표준형에서 처방조제 자기부담금은 얼마인가요?
원문: 가입유형: 표준형 / 의료서비스: 처방조제
      → 8천원과 보상대상의료비의 20% 중 큰 금액</pre>
<p class="pick">모범 선택: 승인</p>
<p>이유: <b>가입유형</b>과 <b>의료서비스</b>가 모두 질문과 같은 줄에서 왔습니다.</p></div>

<div class="ex"><b>사례 B-2 · 띄어쓰기가 깨져 있다</b>
<pre>초안: 8천원과보상대상의료비의20%중 큰 금액 입니다.</pre>
<p class="pick">모범 선택: 수정 → 「8천원과 보상대상의료비의 20% 중 큰 금액입니다.」</p>
<p>이유: 표에서 기계가 읽은 그대로라 띄어쓰기가 깨져 있습니다.
<b>숫자가 맞아도 문장이 이러면 승인하지 않습니다</b> — 그대로 학습되기 때문입니다.</p></div>

<div class="ex"><b>사례 B-3 · 유형은 맞는데 서비스가 다르다</b>
<pre>질문: 선택형에서 외래 자기부담금은?
원문: 가입유형: 선택형 / 의료서비스: 처방조제 → 8천원…</pre>
<p class="pick">모범 선택: 반려 — 짝이 안 맞다</p>
<p>이유: 하나만 맞으면 틀린 것입니다. <b>둘 다</b> 같은 줄이어야 합니다.</p></div>

<h2>C축 · 기권이 정답인가</h2>

<div class="ex"><b>사례 C-1 · 정말 근거가 없다</b>
<pre>질문: (약관에 다루지 않는 새 시술) 비용이 보장되나요?
초안: 판정하지 않았습니다 — 검색에서 인용 가능한 조항이 한 건도 나오지 않았다.
화면: 근거 0건</pre>
<p class="pick">모범 선택: 승인 — 기권이 맞다</p>
<p>이유: 약관에 해당 내용이 없으면 <b>「보장됩니다」라고 말하지 않는 것</b>이 정답입니다.</p></div>

<div class="ex"><b>사례 C-2 · 사실은 답할 수 있다</b>
<pre>질문: 예방목적의 건강검진 비용이 보상 대상인가요?
초안: 판정하지 않았습니다 — 근거를 찾지 못했다.
참고(판례): 예방목적 검진은 보상 대상이 아니라는 결론</pre>
<p class="pick">모범 선택: 반려 — 근거가 있다 → 사유 <kbd>5</kbd> 다른 조항에 답이 있다</p>
<p>이유: 「건강검진, 예방접종」은 대부분 약관의 면책 목록에 <b>이름으로</b> 있습니다.
못 찾은 것이지 없는 것이 아닙니다. 메모에 어디에 있을지 적어 주면 큰 도움이 됩니다.</p></div>

<div class="ex"><b>사례 C-3 · 판본을 좁힐 수 없다</b>
<pre>초안: 판정하지 않았습니다 — 같은 시점에 적용 가능한 상품이 여럿이라
      하나로 좁혀지지 않는다.</pre>
<p class="pick">모범 선택: 승인 — 기권이 맞다</p>
<p>이유: 상품이 특정되지 않으면 조항도 특정되지 않습니다.
<b>되묻는 것</b>이 맞지 추측해서 답하면 안 됩니다.</p></div>

<div class="ex"><b>사례 C-4 · 기권은 맞는데 말투가 불친절하다</b>
<pre>초안: 판정하지 않았습니다 — parse_status 가 ok 가 아니라 판정 근거로 쓸 수 없다.</pre>
<p class="pick">모범 선택: 수정 → 「해당 약관 문서를 정확히 읽지 못해 판정하지 않았습니다.
가입하신 상품의 약관 원문으로 다시 확인해 주세요.」</p>
<p>이유: 기권 자체는 맞습니다. 다만 <b>내부 용어</b>를 고객에게 그대로 쓰면 안 됩니다.</p></div>
"""
    return _page("축별 모범 선택사례", body)


def main() -> int:
    ap = argparse.ArgumentParser(description="승인 QA 검수 꾸러미 생성")
    ap.add_argument("--in", dest="src", default=str(IN_DEFAULT))
    ap.add_argument("--out", default=str(OUT_DEFAULT), help="꾸러미 폴더")
    ap.add_argument("--parts", type=int, default=5, help="검수자 수만큼 나눈다(작업 잠금 대용)")
    ap.add_argument("--zip", default=str(ROOT / "dist" / "승인QA_파일럿300_검수_20260826.zip"),
                    help="배포용 압축 경로. 빈 문자열이면 만들지 않는다")
    args = ap.parse_args()

    src = pathlib.Path(args.src)
    if not src.exists():
        raise SystemExit(
            f"후보 파일이 없습니다: {src}" + chr(10)
            + "  먼저 만드세요: python -m scripts.finetune.build_qa_pilot")
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]

    #: ★규칙 사전분류를 **있으면** 붙인다. 없으면 화면은 그냥 예전처럼 돈다.
    #:   `scripts/review/triage_qa_pilot.py` 가 만든다.
    #: ★통합본이 있으면 **먼저 나온 판단들**을 항목에 붙인다.
    #:   `scripts/review/consolidate_qa_pilot.py` 가 만든다.
    cons_path = src.with_name("consolidated.jsonl")
    if cons_path.exists():
        cons = {r["item_id"]: r for r in
                (json.loads(l) for l in cons_path.read_text(encoding="utf-8").splitlines()
                 if l.strip())}
        n = 0
        for r in rows:
            if r["item_id"] in cons:
                r["cons"] = cons[r["item_id"]]
                n += 1
        print(f"통합본 붙임: {n} / {len(rows)}건")

    tri_path = src.with_name("triage.jsonl")
    if tri_path.exists():
        tri = {r["item_id"]: r for r in
               (json.loads(l) for l in tri_path.read_text(encoding="utf-8").splitlines()
                if l.strip())}
        hit = 0
        for r in rows:
            if r["item_id"] in tri:
                r["triage"] = tri[r["item_id"]]
                hit += 1
        print(f"규칙 사전분류 붙임: {hit} / {len(rows)}건")
    else:
        print("규칙 사전분류 없음 — 먼저 돌리면 사람 작업이 줄어든다:"
              + chr(10) + "  python -m scripts.review.triage_qa_pilot")

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    #: ★파트로 나눠 **한 사람이 한 파트만** 본다. 정적 HTML 이라 서버 잠금을 못 쓰므로
    #:   겹치지 않게 나누는 것이 충돌 방지책이다(600건 검수도 같은 방식이었다).
    #:   ★축이 한 파트에 몰리지 않게 **번갈아** 나눈다 — 그래야 파트마다 A·B·C 가 다 들어간다.
    parts: list[tuple[int, list[dict]]] = []
    for pi in range(args.parts):
        #: ★얕은 복사로도 충분하다 — 파트마다 다르게 쓰는 것은 `triage` 뿐이고
        #:   위에서 그 키를 **새 dict 로 갈아 끼운다.**
        parts.append((pi + 1, [dict(r) for n, r in enumerate(rows) if n % args.parts == pi]))

    #: ★★`group_size` 는 후보 300건 전체를 기준으로 세어져 있다. 그대로 쓰면
    #:   화면이 「같은 문장 12건」이라 해놓고 이 파트엔 3건뿐인 일이 생긴다
    #:   (2026-08-26 실측 — 12 이라 적고 3 건만 적용됐다).
    #:   **사람이 보는 단위는 파트**이므로 파트 안에서 다시 센다.
    for _pi, mine in parts:
        counts: dict[str, int] = {}
        for r in mine:
            tg = r.get("triage")
            if tg and tg.get("group_size", 1) > 1:
                counts[tg["group_key"]] = counts.get(tg["group_key"], 0) + 1
        for r in mine:
            tg = r.get("triage")
            if tg and tg.get("group_size", 1) > 1:
                #: 후보를 파트별로 복사해 쓰므로 원본을 건드리지 않도록 새 dict 로 바꾼다.
                r["triage"] = dict(tg, group_size=counts.get(tg["group_key"], 1))

    made = []
    for pi, mine in parts:
        page = (_PART_HTML
                .replace("@@CSS@@", _CSS)
                .replace("@@JS@@", _PART_JS)
                .replace("@@DATA@@", json.dumps(mine, ensure_ascii=False))
                .replace("@@PART@@", str(pi))
                .replace("@@N@@", str(len(mine))))
        p = out / f"part{pi}.html"
        p.write_text(page, encoding="utf-8", newline="\n")
        made.append((p, len(mine)))

    guide = _guide(parts)
    (out / "00_먼저읽기.html").write_text(guide, encoding="utf-8", newline="\n")
    #: index 는 먼저읽기와 같은 내용이다 — 폴더를 열었을 때 무엇부터 볼지 헷갈리지 않게.
    (out / "index.html").write_text(guide, encoding="utf-8", newline="\n")
    (out / "01_모범선택사례.html").write_text(_examples(), encoding="utf-8", newline="\n")

    import collections
    (out / "manifest.json").write_text(json.dumps({
        "생성": "scripts/review/build_qa_review_html.py",
        "근거": "docs/submission/05D_파인튜닝_모델_설계.md §3-2-1",
        "후보파일": str(src.relative_to(ROOT)).replace(chr(92), "/"),
        "총건수": len(rows),
        "파트": {str(pi): len(m) for pi, m in parts},
        "축별": dict(collections.Counter(r["axis"] for r in rows)),
        "층별": dict(collections.Counter(r["stratum"] for r in rows)),
        "주의": "약관 원문이 포함되어 있다. 외부 공개·재배포 금지.",
    }, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8", newline="\n")

    (out / "README_먼저읽기.txt").write_text(chr(10).join([
        "승인 QA 파일럿 검수 꾸러미",
        "",
        "1) index.html (= 00_먼저읽기.html) 을 먼저 엽니다.",
        "2) 01_모범선택사례.html 로 판단 기준을 익힙니다.",
        "3) 자기 파트(part1~5.html)를 열어 검수합니다.",
        "4) 끝나면 「결과 JSONL 내려받기」로 받은 파일 하나만 보냅니다.",
        "",
        "★ 이 폴더에는 보험 약관 원문이 들어 있습니다. 외부에 올리지 마세요.",
        "★ 확신이 없으면 승인하지 말고 「판단 보류(S)」를 누르세요.",
    ]) + chr(10), encoding="utf-8", newline="\n")

    (out / "검수결과_반환체크리스트.txt").write_text(chr(10).join([
        "결과를 보내기 전에 확인하세요",
        "",
        "[ ] 화면 위 「검수자」 칸에 내 이름이 적혀 있다",
        "[ ] 필터 「미완료」가 0 이다",
        "[ ] 헤더에 「미완성 n」 표시가 없다",
        "[ ] 반려·보류에 메모를 한 줄씩 남겼다 (없으면 내려받기가 막힌다)",
        "[ ] 「결과 JSONL 내려받기」로 받은 파일이 qa_pilot_review_part<번호>.jsonl 이다",
        "[ ] 파일을 열어 첫 줄에 내 이름(reviewer)이 들어 있다",
        "[ ] 다른 파트 결과와 합치지 않고 그대로 보낸다",
    ]) + chr(10), encoding="utf-8", newline="\n")

    #: ★★압축은 **생성기가 쓴 파일만** 담는다. 폴더를 통째로 담았더니
    #:   다른 작업이 같은 폴더에 둔 **검수 결과 파일 4개**가 딸려 들어갔다
    #:   (2026-08-26). 그대로 배포하면 팀원이 답을 먼저 본다.
    written = [q for q, _ in made] + [
        out / n for n in ("index.html", "00_먼저읽기.html", "01_모범선택사례.html",
                          "manifest.json", "README_먼저읽기.txt",
                          "검수결과_반환체크리스트.txt")]
    if args.zip:
        import zipfile
        zp = pathlib.Path(args.zip)
        zp.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for q in written:
                z.write(q, f"{zp.stem}/{q.name}")
        with zipfile.ZipFile(zp) as z:
            if z.testzip() is not None:
                raise SystemExit(f"압축이 손상됐습니다: {zp}")
        stray = sorted(q.name for q in out.iterdir()
                       if q.is_file() and q not in written)
        print(f"압축: {zp}  {zp.stat().st_size // 1024} KB · 파일 {len(written)}개")
        if stray:
            print("  ★폴더에 있지만 **압축에 안 넣은** 파일(생성기가 쓴 것이 아니다):")
            for s in stray:
                print(f"     {s}")

    print(f"꾸러미: {out}")
    for p, n in made:
        print(f"  {p.name:16s} {n:3d}건  ({p.stat().st_size//1024} KB)")
    for name in ("index.html", "00_먼저읽기.html", "01_모범선택사례.html",
                 "manifest.json", "README_먼저읽기.txt", "검수결과_반환체크리스트.txt"):
        print(f"  {name}")
    print(chr(10) + "★결정은 브라우저에 저장된다. 끝나면 「결과 JSONL 내려받기」로 받아 넘긴다.")
    print("★약관 원문이 들어 있다 — 외부 공개 금지.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
