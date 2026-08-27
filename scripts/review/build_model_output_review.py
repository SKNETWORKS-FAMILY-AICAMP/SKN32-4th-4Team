# -*- coding: utf-8 -*-
"""모델이 낸 답변을 **사람이 검수**하는 화면 — 05D §7-2 `groundedness`.

    python -m scripts.review.build_model_output_review
    python -m scripts.review.build_model_output_review --pred data/finetune/results_run2/predictions.jsonl

★★왜 이게 필요한가

    파인튜닝 결과를 규칙으로 재 봤더니 규칙 위반 27.6% → 0%, 사람답 유사도 0.199 → 0.541
    이 나왔다. 그런데 **그걸로 「정확도가 좋아졌다」고 말할 수 없다.**
    05D §7-2 의 네 지표 중 진짜 지표인 **`groundedness`**
    — 답변의 각 주장이 **제공된 근거로 검증되는가** — 는 기계가 못 잰다.

    유사도는 사람 답과 **얼마나 닮았나**이지 **맞나**가 아니다.
    규칙 위반율은 **글자**를 볼 뿐 주장이 근거에 있는지는 모른다.
    이 화면이 그 마지막 한 칸을 채운다.

★★**블라인드로 보여 준다.**

    어느 쪽이 파인튜닝 결과인지 알면 그쪽을 편들게 된다. 그래서
    두 답변을 **무작위로 좌·우에 배치하고 라벨을 숨긴다.**
    배치는 `item_id` 해시로 정해 **다시 열어도 같은 자리**에 온다(재현).
    사람 답(정답)도 **결정한 뒤에** 펼쳐진다 — 먼저 보면 따라 찍는다.

★한 항목에 **두 번** 누른다. 왼손이 왼쪽, 오른손이 오른쪽이다.

    왼쪽   A 근거로 검증됨 · S 일부만 · D 근거에 없는 주장 · F 판단 불가
    오른쪽 J 근거로 검증됨 · K 일부만 · L 근거에 없는 주장 · ; 판단 불가
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRED_DEFAULT = ROOT / "data" / "finetune" / "results_run2" / "predictions.jsonl"
GOLD_DEFAULT = ROOT / "data" / "finetune" / "sft" / "gold.jsonl"
OUT_DEFAULT = ROOT / "docs" / "review" / "model_output_pkg"

_CSS = """
 :root{--fg:#1a1a1a;--mut:#666;--bd:#e2e2e2;--bg:#fff;--acc:#0b5fa5;--warn:#a54b0b;
       --dang:#a51b1b;--ok:#0b7a3b;--card:#fafafa;--hl:#fff3c4}
 @media(prefers-color-scheme:dark){:root{--fg:#e8e8e8;--mut:#a0a0a0;--bd:#333;--bg:#161616;
       --acc:#6fb3ec;--warn:#e8a05a;--dang:#e88;--ok:#6cc48f;--card:#1e1e1e;--hl:#4a4020}}
 *{box-sizing:border-box}
 body{margin:0;font:15px/1.7 -apple-system,"Segoe UI","Malgun Gothic",sans-serif;
      color:var(--fg);background:var(--bg)}
 a{color:var(--acc)} h1{font-size:1.45rem;margin:0 0 .3rem}
 h2{font-size:1.1rem;margin:1.8rem 0 .5rem;padding-top:.6rem;border-top:1px solid var(--bd)}
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
"""

_PAGE = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>모델 답변 검수 · 파트@@PART@@ — @@N@@건</title>
<style>@@CSS@@
 header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--bd);
        padding:.55rem 1rem;display:flex;gap:.6rem;align-items:center;flex-wrap:wrap}
 .bar{flex:1;min-width:140px;height:8px;background:var(--card);border-radius:4px;overflow:hidden}
 .bar>i{display:block;height:100%;background:var(--ok);width:0}
 main{max-width:1180px;margin:0 auto;padding:1rem 1rem 8rem}
 .q{font-size:1.1rem;font-weight:600;margin:.2rem 0}
 .meta{font:12.5px ui-monospace,Consolas,monospace;color:var(--mut);word-break:break-all}
 .ev{border:1px solid var(--bd);border-radius:8px;padding:.7rem .9rem;margin:.5rem 0;
     background:var(--card);white-space:pre-wrap;max-height:300px;overflow:auto}
 .pair{display:grid;grid-template-columns:1fr 1fr;gap:.8rem;margin:.8rem 0}
 @media(max-width:820px){.pair{grid-template-columns:1fr}}
 .side{border:2px solid var(--bd);border-radius:10px;padding:.7rem .9rem;background:var(--card)}
 .side h3{margin:0 0 .4rem;font-size:.95rem;color:var(--mut)}
 .side.picked{border-color:var(--ok)}
 .side.bad{border-color:var(--dang)}
 .ans{white-space:pre-wrap;min-height:4em}
 .btns{position:fixed;left:0;right:0;bottom:0;background:var(--bg);border-top:1px solid var(--bd);
       padding:.5rem 1rem;display:grid;grid-template-columns:1fr 1fr;gap:.5rem}
 .btns>div{display:flex;gap:.35rem;justify-content:center;flex-wrap:wrap}
 button{font:inherit;padding:.4rem .7rem;border:1px solid var(--bd);border-radius:7px;
        background:var(--card);color:var(--fg);cursor:pointer}
 button.on{outline:2px solid var(--acc)}
 button.g{border-color:var(--ok)} button.p{border-color:var(--warn)} button.n{border-color:var(--dang)}
 details{margin:.5rem 0} summary{cursor:pointer;color:var(--mut);font-size:.9rem}
 input[type=text]{width:100%;padding:.4rem .55rem;border:1px solid var(--bd);border-radius:6px;
                  background:var(--bg);color:var(--fg);font:inherit}
 .chip{display:inline-block;font-size:12px;padding:.1rem .5rem;border:1px solid var(--bd);
       border-radius:99px;color:var(--mut);margin-right:.3rem}
 .done{color:var(--ok);font-weight:600}
</style></head><body>
<header>
  <b>모델 답변 검수 · 파트@@PART@@</b>
  <label class="chip">검수자 <input type="text" id="who" size="8" placeholder="이름"></label>
  <span class="chip" id="pos"></span>
  <div class="bar"><i id="prog"></i></div>
  <span class="chip" id="cnt"></span>
  <a href="00_먼저읽기.html">쉬운 설명</a>
  <button id="nextTodo">다음 미완료</button>
  <button onclick="exportJsonl()">결과 내려받기</button>
  <button onclick="if(confirm('이 파트의 결정을 모두 지웁니다. 계속?'))reset()">초기화</button>
</header>
<div id="nosave" style="display:none;background:#a51b1b;color:#fff;padding:.6rem 1rem;font-weight:600"></div>
<main id="app"></main>
<div class="btns" id="btns"></div>
<script>
const DATA = @@DATA@@;
const PART = "@@PART@@";
const KEY  = "model_output_review::" + PART;
@@JS@@
</script></body></html>
"""

_JS = r"""
//: ★판정 넷. **「근거에 없는 주장」이 이 검수의 핵심**이다 —
//:   그게 곧 05D §7-2 의 `groundedness` 이고, 서비스가 사람을 손해 보게 하는 지점이다.
const VERDICTS = [
  ["ok",      "근거로 검증됨",     "답변의 모든 주장이 위 인용으로 확인된다"],
  ["partial", "일부만",           "일부는 확인되고 일부는 근거가 없다"],
  ["unsup",   "근거에 없는 주장",  "인용에 없는 것을 말한다 — 고객이 손해 볼 수 있다"],
  ["unknown", "판단 불가",         "인용이 잘렸거나 전문 지식이 필요하다"],
];
const LKEYS = ["A","S","D","F"];   // 왼쪽 — 왼손
const RKEYS = ["J","K","L",";"];   // 오른쪽 — 오른손

const STORAGE_OK = (() => {
  try { localStorage.setItem(KEY+"::p","1"); localStorage.removeItem(KEY+"::p"); return true; }
  catch { return false; }
})();
let state = load(), idx = state.cursor || 0, undo = [];

function load(){
  if (!STORAGE_OK) return {v:{}, cursor:0, who:"", log:[]};
  try { return JSON.parse(localStorage.getItem(KEY)) || {v:{}, cursor:0, who:"", log:[]}; }
  catch { return {v:{}, cursor:0, who:"", log:[]}; }
}
function save(){
  if (!STORAGE_OK) return;
  try { localStorage.setItem(KEY, JSON.stringify(state)); }
  catch(e){ const b=document.getElementById("nosave"); if(b){ b.style.display="block";
    b.textContent="⚠ 자동저장 실패 ("+e.message+") — 지금 「결과 내려받기」를 누르세요."; } }
}
function esc(s){ return String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function label(k){ const v=VERDICTS.find(x=>x[0]===k); return v?v[1]:k; }

function incomplete(it){
  const d = state.v[it.item_id];
  if (!d) return "아직 안 봄";
  if (!d.left)  return "왼쪽 판정이 없습니다";
  if (!d.right) return "오른쪽 판정이 없습니다";
  //: ★「근거에 없는 주장」은 **어디가 문제인지** 적어야 다음 사람이 확인할 수 있다.
  if ((d.left === "unsup" || d.right === "unsup") && !(d.note||"").trim())
    return "「근거에 없는 주장」은 메모가 필요합니다";
  return "";
}
function incompleteList(){ return DATA.filter(it => incomplete(it)); }
function decided(it){ const d=state.v[it.item_id]; return d && d.left && d.right; }

function render(){
  const it = DATA[idx]; if(!it) return;
  const d = state.v[it.item_id] || {};
  document.getElementById("pos").textContent = `${idx+1} / ${DATA.length}`;
  document.getElementById("who").value = state.who || "";
  const done = DATA.filter(x => decided(x) && !incomplete(x)).length;
  const inc = incompleteList().length - DATA.filter(x=>!decided(x)).length;
  document.getElementById("cnt").innerHTML =
    `완료 <span class="done">${done}</span> / ${DATA.length}`
    + (inc>0 ? ` · <span style="color:var(--dang)">미완성 ${inc}</span>` : "");
  document.getElementById("prog").style.width = (done/DATA.length*100)+"%";

  const ev = it.evidence;
  const evHtml = ev
    ? `<div class="meta">${esc(ev.insurer||"")} · ${esc(ev.qualified_no||"")} · p.${esc(ev.page_from)}</div>
       <div class="ev">${esc(ev.text||"")}</div>`
    : `<div class="warnbox">인용 <b>0건</b> — 근거가 없는 상태입니다.
         <b>답변이 무언가를 단정하면 그건 「근거에 없는 주장」</b>입니다</div>`;

  //: ★결정 전에는 **어느 쪽이 무엇인지 안 보여준다.** 결정 뒤에 펼쳐진다.
  const after = (d.left && d.right) ? `
    <details open><summary>정답·정체 (결정 후 공개)</summary>
      <div class="box"><b>사람이 쓴 답</b><br>${esc(it.human)}</div>
      <div class="meta">왼쪽 = ${esc(it.leftIs)} · 오른쪽 = ${esc(it.rightIs)}</div>
    </details>` : "";

  document.getElementById("app").innerHTML = `
    <span class="chip">${esc(it.axis)}축</span><span class="chip">${esc(it.stratum)}</span>
    <span class="chip">${esc(it.item_id)}</span>
    <div class="q">${esc(it.question)}</div>
    ${it.engine ? `<div class="box"><b>엔진 판정</b> —
       <code>${esc(it.engine.verdict)}</code> · <code>${esc(it.engine.reason_code)}</code>
       · 기권 ${it.engine.abstained ? "예" : "아니오"} · 인용 ${esc(it.engine.citations)}건</div>` : ""}
    <div class="lead">아래 <b>인용 조항</b>만이 근거입니다. 여기 없는 것을 답변이 말하면 「근거에 없는 주장」입니다.</div>
    ${evHtml}
    <div class="pair">
      <div class="side ${d.left==="ok"?"picked":(d.left==="unsup"?"bad":"")}">
        <h3>왼쪽 <span class="lead">(<kbd>A</kbd><kbd>S</kbd><kbd>D</kbd><kbd>F</kbd>)</span>
          ${d.left?`— <b>${esc(label(d.left))}</b>`:""}</h3>
        <div class="ans">${esc(it.left)}</div></div>
      <div class="side ${d.right==="ok"?"picked":(d.right==="unsup"?"bad":"")}">
        <h3>오른쪽 <span class="lead">(<kbd>J</kbd><kbd>K</kbd><kbd>L</kbd><kbd>;</kbd>)</span>
          ${d.right?`— <b>${esc(label(d.right))}</b>`:""}</h3>
        <div class="ans">${esc(it.right)}</div></div>
    </div>
    <input type="text" id="note" placeholder="메모 — 근거에 없는 주장이면 어느 대목인지 적어 주세요"
           value="${esc(d.note||"")}" onchange="setNote(this.value)">
    ${incomplete(it) && decided(it) ? `<div class="warnbox">⚠ ${esc(incomplete(it))}</div>` : ""}
    ${after}`;

  const row = (keys, side) => VERDICTS.map((v,i) =>
    `<button class="${v[0]==="ok"?"g":(v[0]==="unsup"?"n":"p")} ${d[side]===v[0]?"on":""}"
       title="${esc(v[2])}" onclick="pick('${side}','${v[0]}')">
       <kbd>${keys[i]}</kbd> ${v[1]}</button>`).join("");
  document.getElementById("btns").innerHTML =
    `<div>${row(LKEYS,"left")}</div><div>${row(RKEYS,"right")}</div>`;
  state.cursor = idx; save();
}

function pick(side, val){
  const it = DATA[idx];
  undo.push(JSON.stringify(state.v));
  state.v[it.item_id] = Object.assign({}, state.v[it.item_id],
    {[side]: val, at: new Date().toISOString()});
  save();
  const d = state.v[it.item_id];
  //: 양쪽이 다 채워지고 미완성이 아니면 자동으로 다음으로 — 이게 속도를 만든다.
  if (d.left && d.right && !incomplete(it)) { go(1); return; }
  render();
  if (incomplete(it) === "「근거에 없는 주장」은 메모가 필요합니다") {
    const el = document.getElementById("note"); if (el) el.focus();
  }
}
function setNote(v){
  const it = DATA[idx];
  state.v[it.item_id] = Object.assign({}, state.v[it.item_id], {note: v});
  save(); render();
}
function go(n){ idx = Math.max(0, Math.min(DATA.length-1, idx+n)); render(); }
function nextTodo(){
  for (let s=1; s<=DATA.length; s++){
    const i=(idx+s)%DATA.length;
    if (incomplete(DATA[i])) { idx=i; render(); return; }
  }
  alert("미완료 항목이 없습니다.");
}
function undoLast(){ if(!undo.length) return; state.v=JSON.parse(undo.pop()); save(); render(); }
function reset(){ state={v:{},cursor:0,who:"",log:[]}; idx=0; undo=[]; save(); render(); }

function exportJsonl(){
  const inc = incompleteList();
  if (inc.length) {
    idx = DATA.indexOf(inc[0]); render();
    alert("끝나지 않은 항목이 " + inc.length + "건 있어 내려받을 수 없습니다."
      + String.fromCharCode(10) + String.fromCharCode(10)
      + inc.slice(0,5).map(x => "· " + x.item_id + " — " + incomplete(x)).join(String.fromCharCode(10)));
    return;
  }
  const lines = DATA.map(it => {
    const d = state.v[it.item_id] || {};
    //: ★저장은 **좌우가 아니라 정체로** 남긴다. 좌우는 화면 배치일 뿐이다.
    const m = {}; m[it.leftIs] = d.left; m[it.rightIs] = d.right;
    return JSON.stringify({
      item_id: it.item_id, axis: it.axis, stratum: it.stratum,
      baseline: m.baseline || "", adapter: m.adapter || "",
      note: d.note || "", reviewer: state.who || "", at: d.at || "", part: PART,
    });
  }).join(String.fromCharCode(10));
  try {
    const a=document.createElement("a");
    a.href=URL.createObjectURL(new Blob([lines],{type:"application/x-ndjson"}));
    a.download=`model_output_review_part${PART}.jsonl`;
    document.body.appendChild(a); a.click(); a.remove();
  } catch(e){ const w=window.open("","_blank"); w.document.write("<pre>"+lines.replace(/</g,"&lt;")+"</pre>"); }
}

document.getElementById("who").addEventListener("input", e=>{ state.who=e.target.value; save(); });
document.getElementById("nextTodo").addEventListener("click", nextTodo);
document.addEventListener("keydown", e=>{
  if (e.target.tagName==="INPUT" || e.target.tagName==="TEXTAREA") return;
  if (e.ctrlKey && e.key.toLowerCase()==="z"){ e.preventDefault(); undoLast(); return; }
  const k = e.key.toUpperCase();
  let i = LKEYS.indexOf(k); if (i>=0){ e.preventDefault(); pick("left", VERDICTS[i][0]); return; }
  i = RKEYS.indexOf(e.key===";"?";":k); if (i>=0){ e.preventDefault(); pick("right", VERDICTS[i][0]); return; }
  if (e.key==="ArrowLeft"){ e.preventDefault(); go(-1); }
  if (e.key==="ArrowRight"){ e.preventDefault(); go(1); }
});
if (!STORAGE_OK){
  const b=document.getElementById("nosave"); b.style.display="block";
  b.textContent="⚠ 이 브라우저는 자동저장이 안 됩니다 — 끝내기 전에 반드시 「결과 내려받기」를 누르세요.";
}
render();
"""


def _guide(parts) -> str:
    rows = "".join(
        f'<tr><td>파트 {pi}</td><td>{len(m)}</td><td>약 {len(m)*35//60}분</td>'
        f'<td><a href="part{pi}.html">검수 시작</a></td></tr>' for pi, m in parts)
    body = f"""
<h1>먼저 읽기 · 모델 답변 검수</h1>
<p class="lead">파인튜닝한 모델과 원래 모델이 같은 질문에 낸 답을 나란히 놓고,
<b>각 답이 인용 조항으로 검증되는가</b>를 봅니다.</p>

<div class="warnbox"><b>이 검수만이 답할 수 있는 것이 있습니다.</b>
기계로 잰 지표(유사도·규칙 위반)는 <b>글자</b>를 볼 뿐입니다.
「답변이 인용에 없는 것을 말하고 있는가」는 사람만 압니다.
그리고 그게 이 서비스에서 <b>사람이 손해를 보는 지점</b>입니다.</div>

<h2>1. 나누는 방법</h2>
<table><tr><th>담당</th><th>항목</th><th>예상</th><th>시작</th></tr>{rows}</table>

<h2>2. 화면 보는 법</h2>
<ol>
<li><b>질문</b>과 <b>엔진 판정</b>을 읽습니다.</li>
<li><b>인용 조항</b>을 읽습니다. <b>여기 적힌 것만이 근거입니다.</b></li>
<li>아래 두 답변을 각각 판정합니다. <b>어느 쪽이 어느 모델인지는 가려 뒀습니다.</b></li>
</ol>
<div class="box"><b>왜 가리나</b> — 어느 쪽이 파인튜닝 결과인지 알면 그쪽을 편들게 됩니다.
좌우 배치는 항목마다 무작위이고, <b>결정한 뒤에</b> 정체와 사람이 쓴 답이 공개됩니다.</div>

<h2>3. 네 가지 판정</h2>
<table>
<tr><th>왼쪽</th><th>오른쪽</th><th>뜻</th></tr>
<tr><td><kbd>A</kbd></td><td><kbd>J</kbd></td>
    <td><b>근거로 검증됨</b> — 답변의 <b>모든</b> 주장이 인용으로 확인된다</td></tr>
<tr><td><kbd>S</kbd></td><td><kbd>K</kbd></td>
    <td><b>일부만</b> — 일부는 확인되고 일부는 근거가 없다</td></tr>
<tr><td><kbd>D</kbd></td><td><kbd>L</kbd></td>
    <td><b>근거에 없는 주장</b> — 인용에 없는 것을 말한다.
        <b>이게 이 검수의 핵심입니다.</b> 메모에 어느 대목인지 적어 주세요</td></tr>
<tr><td><kbd>F</kbd></td><td><kbd>;</kbd></td>
    <td><b>판단 불가</b> — 인용이 잘렸거나 전문 지식이 필요하다</td></tr>
</table>
<p>왼손이 왼쪽, 오른손이 오른쪽입니다. <b>양쪽을 다 누르면 자동으로 다음</b>으로 넘어갑니다.</p>

<h2>4. 헷갈리는 자리</h2>
<div class="box"><b>인용이 0건인 항목</b>이 있습니다. 그때 답변이 무언가를
<b>단정하면 그건 「근거에 없는 주장」</b>입니다. 「확인하지 못했다」까지만 말해야 맞습니다.</div>
<div class="box"><b>사람이 쓴 답과 달라도 틀린 것이 아닙니다.</b>
보는 것은 <b>인용으로 검증되는가</b>이지 사람 답과 같은가가 아닙니다.</div>
<div class="box"><b>판정(verdict)은 바꾸지 않습니다.</b> 엔진이 이미 정한 것이고,
보는 것은 그 위에 얹힌 문장입니다.</div>

<h2>5. 저장과 제출</h2>
<p>누를 때마다 자동 저장되고 창을 닫아도 같은 자리로 돌아옵니다.
<b>「근거에 없는 주장」에 메모가 없으면 내려받기가 막힙니다.</b>
끝나면 <b>결과 내려받기</b>로 받은 파일 하나만 보내 주세요.</p>
"""
    return ('<!DOCTYPE html>' + chr(10) + '<html lang="ko"><head><meta charset="utf-8">' + chr(10)
            + '<meta name="viewport" content="width=device-width,initial-scale=1">' + chr(10)
            + '<title>먼저 읽기 · 모델 답변 검수</title>' + chr(10) + '<style>' + _CSS
            + ' main{max-width:860px;margin:0 auto;padding:1.5rem 1rem 4rem}' + chr(10)
            + '</style></head><body><main>' + chr(10) + body + chr(10) + '</main></body></html>' + chr(10))


def main() -> int:
    ap = argparse.ArgumentParser(description="모델 답변 검수 화면 생성")
    ap.add_argument("--pred", default=str(PRED_DEFAULT))
    ap.add_argument("--gold", default=str(GOLD_DEFAULT))
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--parts", type=int, default=5)
    args = ap.parse_args()

    pred_p = pathlib.Path(args.pred)
    if not pred_p.exists():
        raise SystemExit(f"예측 파일이 없습니다: {pred_p}")
    preds = [json.loads(l) for l in pred_p.read_text(encoding="utf-8").splitlines() if l.strip()]

    #: gold 에서 **질문·엔진 판정·인용 조항**을 가져온다 — 근거 없이 검수시킬 수 없다.
    gold = {}
    gp = pathlib.Path(args.gold)
    if gp.exists():
        for l in gp.read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l)
                gold[r["item_id"]] = r

    rows, missing = [], 0
    for p in preds:
        g = gold.get(p["item_id"])
        if not g:
            missing += 1
            continue
        user = g["messages"][1]["content"]
        #: 프롬프트에서 질문·판정·인용을 되꺼낸다(SFT 형식이 고정이라 안전하다).
        q = user.split("[질문]" + chr(10), 1)[-1].split(chr(10) + chr(10), 1)[0]
        ev_txt = ""
        if "[인용 조항]" in user:
            tail = user.split("[인용 조항]", 1)[1]
            ev_txt = tail.split(chr(10), 1)[1] if chr(10) in tail else ""
        has_ev = ev_txt.strip() and ev_txt.strip() != "없음"
        eng = None
        if "[판정]" in user:
            seg = user.split("[판정]" + chr(10), 1)[1].split(chr(10), 1)[0]
            kv = dict(x.split("=", 1) for x in seg.split() if "=" in x)
            eng = {"verdict": kv.get("verdict"), "reason_code": kv.get("reason_code"),
                   "abstained": kv.get("기권") == "예", "citations": 1 if has_ev else 0}

        #: ★좌우를 **item_id 해시로** 정한다 — 무작위이되 다시 열어도 같은 자리다.
        flip = int(hashlib.sha256(p["item_id"].encode("utf-8")).hexdigest(), 16) % 2 == 1
        left, right = (p["adapter"], p["baseline"]) if flip else (p["baseline"], p["adapter"])
        rows.append({
            "item_id": p["item_id"], "axis": p.get("axis"), "stratum": p.get("stratum"),
            "question": q.strip(), "engine": eng,
            "evidence": ({"text": ev_txt.strip()} if has_ev else None),
            "left": left, "right": right,
            "leftIs": "adapter" if flip else "baseline",
            "rightIs": "baseline" if flip else "adapter",
            "human": p.get("사람답", ""),
        })
    if missing:
        #: ★조용히 버리지 않는다.
        print(f"[경고] gold 에 없어 건너뛴 예측 {missing}건")

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    parts = [(i + 1, [r for n, r in enumerate(rows) if n % args.parts == i])
             for i in range(args.parts)]
    for pi, mine in parts:
        page = (_PAGE.replace("@@CSS@@", _CSS).replace("@@JS@@", _JS)
                .replace("@@DATA@@", json.dumps(mine, ensure_ascii=False))
                .replace("@@PART@@", str(pi)).replace("@@N@@", str(len(mine))))
        (out / f"part{pi}.html").write_text(page, encoding="utf-8", newline="\n")
    guide = _guide(parts)
    (out / "00_먼저읽기.html").write_text(guide, encoding="utf-8", newline="\n")
    (out / "index.html").write_text(guide, encoding="utf-8", newline="\n")

    import collections
    (out / "manifest.json").write_text(json.dumps({
        "만든날": "2026-08-27",
        #: 상대·절대 경로가 둘 다 들어올 수 있다. 저장소 밖이면 그대로 적는다.
        "예측파일": (str(pred_p.resolve().relative_to(ROOT)).replace(chr(92), "/")
                  if ROOT in pred_p.resolve().parents else str(pred_p)),
        "건수": len(rows), "파트": {str(pi): len(m) for pi, m in parts},
        "축별": dict(collections.Counter(r["axis"] for r in rows)),
        "★좌우": "item_id 해시로 무작위 배치. 결정 뒤에만 정체가 보인다",
        "★재는 것": "groundedness — 답변의 주장이 인용으로 검증되는가(05D §7-2)",
        "주의": "약관 원문이 포함되어 있다. 외부 공개 금지.",
    }, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8", newline="\n")

    print(f"꾸러미: {out}  {len(rows)}건")
    for pi, m in parts:
        print(f"  part{pi}.html  {len(m):3d}건  ({(out / f'part{pi}.html').stat().st_size//1024} KB)")
    print("  00_먼저읽기.html · index.html · manifest.json")
    print(chr(10) + "★좌우는 가려져 있다. 결정한 뒤에만 어느 쪽이 어댑터인지 보인다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
