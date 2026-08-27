"""Build a 29-signature human approval screen for high-confidence S7 OCR facts."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import defaultdict
from pathlib import Path

from scripts.eval.build_human_table_review import ROOT, _manifest, _page_image


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _signature(row: dict) -> tuple:
    return (
        _norm(row.get("plan") or ""),
        tuple(sorted(row.get("service") or [])),
        _norm(row.get("institution") or ""),
        tuple(sorted(row.get("coverage") or [])),
        _norm(row.get("amount_formula") or ""),
        tuple(_norm(x) for x in row.get("amount_tokens") or []),
        tuple(_norm(x) for x in row.get("rate_tokens") or []),
    )


def _eligible(row: dict) -> bool:
    source = row.get("source") or {}
    grid = source.get("grid_integrity") or {}
    return (
        (row.get("validation") or {}).get("status") == "shadow_pass"
        and bool(row.get("plan"))
        and bool(row.get("service"))
        and bool(row.get("institution"))
        and not source.get("continuation_suspected")
        and not row.get("inferred")
        and not grid.get("ragged_rows")
        and not grid.get("span_mismatch_cells")
    )


def _groups(min_documents: int) -> list[dict]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for path in sorted((ROOT / "data/candidates/s7_selfpay").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("candidates") or []:
            if _eligible(row):
                grouped[_signature(row)].append(row)
    output = []
    for signature, rows in grouped.items():
        documents = sorted({row["document_sha12"] for row in rows})
        if len(documents) < min_documents:
            continue
        representative = sorted(rows, key=lambda r: (r["document_sha12"], r["page_1based"], r["candidate_id"]))[0]
        signature_json = json.dumps(signature, ensure_ascii=False, separators=(",", ":"))
        output.append({
            "signature_id": "sha256:" + hashlib.sha256(signature_json.encode("utf-8")).hexdigest(),
            "facts": len(rows),
            "documents": len(documents),
            "candidate_ids": sorted(row["candidate_id"] for row in rows),
            "representative": representative,
        })
    return sorted(output, key=lambda g: (-g["documents"], -g["facts"], g["signature_id"]))


def _render(groups: list[dict], images: dict[tuple[str, int], str]) -> str:
    cards = []
    metadata = []
    for index, group in enumerate(groups, 1):
        row = group["representative"]
        key = (row["document_sha12"], row["page_1based"])
        source = row.get("source") or {}
        grid = source.get("grid_integrity") or {}
        cards.append(f'''<section class="card" data-id="{group['signature_id']}"><header>{index}/{len(groups)} · <b>{html.escape(row['insurer'])}</b> · {row['document_sha12']} p{row['page_1based']} <span>{group['documents']}문서 / {group['facts']}facts</span></header><div class="body"><img src="{images[key]}" loading="lazy"><div>
<dl><dt>가입형</dt><dd>{html.escape(row['plan'])}</dd><dt>서비스</dt><dd>{html.escape(', '.join(row['service']))}</dd><dt>기관</dt><dd>{html.escape(row['institution'])}</dd><dt>급여축</dt><dd>{html.escape(', '.join(row['coverage']) or '-')}</dd><dt>금액식</dt><dd><b>{html.escape(row['amount_formula'])}</b></dd><dt>값 토큰</dt><dd>{html.escape(', '.join((row['amount_tokens'] or []) + (row['rate_tokens'] or [])))}</dd><dt>구조</dt><dd>{grid.get('raw_rows')}행 × {grid.get('expanded_columns')}열 · 경계연속={source.get('continuation_suspected')} · 값지어냄={source.get('axis_binding',{}).get('value_invention')}</dd></dl>
<p class="rule">승인 기준: 원문 이미지에서 가입형·서비스·기관·금액식이 같은 행/열 관계로 읽혀야 합니다.</p><div class="choices"><button data-label="approve">승인</button><button data-label="reject">거절</button><button data-label="fix">수정 필요</button><button data-label="unsure">애매함</button></div><textarea placeholder="거절/수정 근거"></textarea></div></div></section>''')
        metadata.append({
            "signature_id": group["signature_id"], "facts": group["facts"],
            "documents": group["documents"], "candidate_ids": group["candidate_ids"],
            "representative": {"sha12": row["document_sha12"], "page": row["page_1based"]},
        })
    payload = json.dumps(metadata, ensure_ascii=False)
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>S7 OCR fact 29패턴 승인</title><style>body{{margin:0;background:#f5f5f2;font:14px/1.5 system-ui,'Malgun Gothic'}}.top{{position:sticky;top:0;z-index:5;background:#fff;padding:10px 16px;border-bottom:1px solid #ccc;display:flex;gap:12px;align-items:center;flex-wrap:wrap}}.save-rule{{color:#7a4b00;font-size:12px}}.save-rule code{{user-select:all;background:#fff4cf;padding:3px 6px;border-radius:4px}}.grow{{flex:1}}.card{{margin:15px;background:#fff;border:1px solid #ccc;border-radius:8px;overflow:hidden}}.card.done{{border:2px solid #35715d}}header{{padding:9px 12px;border-bottom:1px solid #ddd}}header span{{float:right}}.body{{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:12px}}img{{width:100%;max-height:760px;object-fit:contain;background:#eee}}dt{{font-weight:bold;color:#555}}dd{{margin:0 0 8px}}button{{padding:7px 12px;margin:3px;border:1px solid #aaa;background:#fff;border-radius:6px}}button.on{{background:#275c4b;color:#fff}}textarea{{width:100%;height:70px;box-sizing:border-box;margin-top:8px}}.rule{{background:#fff9dc;padding:8px}}@media(max-width:900px){{.body{{grid-template-columns:1fr}}}}</style></head><body><div class="top"><b>S7 OCR fact 대표 29패턴 승인</b><span id="progress"></span><span class="save-rule">다운로드 후 저장 권장: <code>data/eval/s7_fact_signature_labels_20260804.jsonl</code></span><span class="grow"></span><button id="next">다음 미완료</button><button id="save">결과 JSONL 내려받기</button></div>{''.join(cards)}<script>
const META={payload}, KEY='s7-fact-signature-review-v1';let state=JSON.parse(localStorage.getItem(KEY)||'{{}}');function sync(){{document.querySelectorAll('.card').forEach(c=>{{let s=state[c.dataset.id]||{{}};c.classList.toggle('done',!!s.label);c.querySelectorAll('[data-label]').forEach(b=>b.classList.toggle('on',b.dataset.label===s.label));c.querySelector('textarea').value=s.note||'';}});document.getElementById('progress').textContent=Object.values(state).filter(x=>x.label).length+' / '+META.length;localStorage.setItem(KEY,JSON.stringify(state));}}document.querySelectorAll('[data-label]').forEach(b=>b.onclick=()=>{{let c=b.closest('.card');state[c.dataset.id]={{label:b.dataset.label,note:c.querySelector('textarea').value}};sync();}});document.querySelectorAll('textarea').forEach(t=>t.onchange=()=>{{let c=t.closest('.card');state[c.dataset.id]={{...(state[c.dataset.id]||{{}}),note:t.value}};sync();}});document.getElementById('next').onclick=()=>{{let c=[...document.querySelectorAll('.card')].find(x=>!state[x.dataset.id]?.label);if(c)c.scrollIntoView({{behavior:'smooth'}})}};document.getElementById('save').onclick=()=>{{let lines=META.map(m=>JSON.stringify({{...m,...(state[m.signature_id]||{{label:'',note:''}})}}));let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([lines.join('\\n')+'\\n'],{{type:'application/jsonl'}}));a.download='s7_fact_signature_labels_20260804.jsonl';a.click();}};sync();</script></body></html>'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-documents", type=int, default=2)
    ap.add_argument("--dpi", type=int, default=100)
    ap.add_argument("--output", default=str(ROOT / "docs/review/s7_fact_signature_review_20260804.html"))
    args = ap.parse_args()
    groups = _groups(args.min_documents)
    manifest = _manifest()
    images: dict[tuple[str, int], str] = {}
    for group in groups:
        row = group["representative"]
        key = (row["document_sha12"], row["page_1based"])
        images[key] = _page_image(manifest, *key, args.dpi)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render(groups, images), encoding="utf-8")
    print(json.dumps({"output": str(output), "signatures": len(groups),
                      "facts": sum(g["facts"] for g in groups)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
