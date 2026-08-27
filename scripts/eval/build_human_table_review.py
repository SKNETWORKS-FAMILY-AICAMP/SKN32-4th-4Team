"""Build one self-contained review screen for B6 check3 and B5 candidate65."""

from __future__ import annotations

import argparse
import base64
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("text") or "")
    return str(value or "")


def _grid(rows: list[dict]) -> str:
    body = []
    for row in rows:
        cols = row.get("cols") if isinstance(row, dict) and "cols" in row else row
        if not isinstance(cols, dict):
            continue
        cells = "".join(f"<td>{html.escape(_text(v))}</td>" for _, v in sorted(cols.items()))
        body.append(f"<tr>{cells}</tr>")
    return "<table>" + "".join(body) + "</table>"


def _page_image(manifest: dict[str, dict], sha12: str, page: int, dpi: int) -> str:
    try:
        import fitz

        source = manifest[sha12]
        pdf = ROOT / source["saved_as"]
        doc = fitz.open(str(pdf))
        pix = doc[page - 1].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
        data = pix.tobytes("jpg", jpg_quality=68)
        doc.close()
        return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        return ""


def _manifest() -> dict[str, dict]:
    from scripts.crawl.split_manifest import load_all

    out: dict[str, dict] = {}
    for row in load_all():
        sha = row.get("sha256") or ""
        if sha and not (row.get("excluded_reason") or "").strip():
            out.setdefault(sha[:12], row)
    return out


def _check_cards() -> list[dict]:
    labels = [json.loads(x) for x in (ROOT / "data/eval/table_labels.jsonl").read_text(encoding="utf-8").splitlines()]
    cards = []
    for row in labels:
        if row.get("label") != "check":
            continue
        sha, page = row["sha12"], int(row["page"])
        paths = list((ROOT / "data/extracted").glob(f"*/s5_pymupdf-1.28.0/{sha}.json"))
        page_doc = {}
        if paths:
            doc = json.loads(paths[0].read_text(encoding="utf-8"))
            page_doc = next((p for p in doc.get("pages") or [] if p.get("page") == page), {})
        previews = []
        for table in page_doc.get("tables_coords") or []:
            previews.append({
                "title": f"{table.get('table_id')} · {table.get('method')} · {table.get('cols')}열",
                "html": _grid((table.get("records") or [])[:12]),
            })
        cards.append({
            "id": f"check:{sha}:p{page}", "queue": "B6-check3", "sha12": sha, "page": page,
            "insurer": paths[0].parents[1].name if paths else "?", "why": row.get("why") or "",
            "page_text": (page_doc.get("text") or "")[:5000], "previews": previews,
        })
    return cards


def _candidate_cards() -> list[dict]:
    cards = []
    path = ROOT / "data/eval/table_labelset_candidates.jsonl"
    for row in map(json.loads, path.read_text(encoding="utf-8").splitlines()):
        cards.append({
            "id": f"candidate:{row['sha12']}:p{row['page']}:{row.get('table_id')}",
            "queue": "B5-candidate65", "sha12": row["sha12"], "page": int(row["page"]),
            "insurer": row.get("insurer") or "?",
            "why": f"{row.get('method')} · {row.get('cols')}열 · {row.get('rows')}행",
            "page_text": "", "previews": [{"title": "추출 후보", "html": _grid(row.get("preview") or [])}],
        })
    return cards


def _render(cards: list[dict]) -> str:
    sections = []
    for index, card in enumerate(cards, 1):
        image = f'<img src="{card["image"]}" loading="lazy">' if card.get("image") else "<p>원문 이미지 없음</p>"
        previews = "".join(f'<div class="preview"><b>{html.escape(p["title"])}</b>{p["html"]}</div>' for p in card["previews"])
        sections.append(f'''<section class="card" data-id="{html.escape(card['id'])}">
<header><span>{index}/{len(cards)}</span> <b>{html.escape(card['queue'])}</b> · {html.escape(card['insurer'])} · {card['sha12']} · p{card['page']} <em></em></header>
<div class="body"><div>{image}</div><div><p>{html.escape(card['why'])}</p>{previews}<details><summary>추출 페이지 텍스트</summary><pre>{html.escape(card['page_text'])}</pre></details>
<div class="choices"><button data-label="table">진짜 표</button><button data-label="prose">본문 오탐</button><button data-label="broken">표지만 추출 깨짐</button><button data-label="unsure">애매함</button></div>
<textarea placeholder="근거/수정할 셀/메모"></textarea></div></div></section>''')
    payload = json.dumps([{"id": c["id"], "queue": c["queue"], "sha12": c["sha12"], "page": c["page"]} for c in cards], ensure_ascii=False)
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>S7 사람 표 검수</title><style>
body{{margin:0;background:#f6f6f3;font:14px/1.5 system-ui,'Malgun Gothic';color:#20201d}}.top{{position:sticky;top:0;z-index:9;background:#fff;padding:10px 16px;border-bottom:1px solid #ddd;display:flex;gap:12px;align-items:center;flex-wrap:wrap}}.top .grow{{flex:1}}.save-rule{{color:#7a4b00;font-size:12px}}.save-rule code{{user-select:all;background:#fff4cf;padding:3px 6px;border-radius:4px}}button{{padding:7px 11px;border:1px solid #bbb;background:#fff;border-radius:6px;cursor:pointer}}button.on{{background:#275c4b;color:#fff;border-color:#275c4b}}.card{{margin:16px;background:#fff;border:1px solid #ddd;border-radius:8px;overflow:hidden}}.card.done{{border-color:#679b88}}header{{padding:9px 12px;border-bottom:1px solid #ddd}}header em{{float:right;color:#275c4b}}.body{{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:12px}}img{{width:100%;max-height:720px;object-fit:contain;background:#eee}}table{{border-collapse:collapse;width:100%;table-layout:fixed;font-size:12px;margin:6px 0 14px}}td{{border:1px solid #ccc;padding:4px;vertical-align:top;word-break:break-all}}pre{{white-space:pre-wrap;max-height:260px;overflow:auto}}.choices{{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}}textarea{{width:100%;height:70px;margin-top:8px;box-sizing:border-box}}@media(max-width:900px){{.body{{grid-template-columns:1fr}}}}
</style></head><body><div class="top"><b>S7 표 사람 검수</b><span id="progress"></span><span class="save-rule">다운로드 후 저장 권장: <code>data/eval/human_table_labels_20260804.jsonl</code></span><span class="grow"></span><button id="next">다음 미완료</button><button id="save">결과 JSONL 내려받기</button></div>{''.join(sections)}<script>
const META={payload}; const KEY='s7-human-table-review-v1'; let state=JSON.parse(localStorage.getItem(KEY)||'{{}}');
function sync(){{document.querySelectorAll('.card').forEach(card=>{{const s=state[card.dataset.id]||{{}};card.classList.toggle('done',!!s.label);card.querySelector('em').textContent=s.label?'✓ '+s.label:'';card.querySelectorAll('[data-label]').forEach(b=>b.classList.toggle('on',b.dataset.label===s.label));card.querySelector('textarea').value=s.note||'';}});document.getElementById('progress').textContent=Object.values(state).filter(x=>x.label).length+' / '+META.length;localStorage.setItem(KEY,JSON.stringify(state));}}
document.querySelectorAll('[data-label]').forEach(b=>b.onclick=()=>{{let c=b.closest('.card');state[c.dataset.id]={{...(state[c.dataset.id]||{{}}),label:b.dataset.label,note:c.querySelector('textarea').value}};sync();}});
document.querySelectorAll('textarea').forEach(t=>t.onchange=()=>{{let c=t.closest('.card');state[c.dataset.id]={{...(state[c.dataset.id]||{{}}),note:t.value}};sync();}});
document.getElementById('next').onclick=()=>{{let c=[...document.querySelectorAll('.card')].find(x=>!state[x.dataset.id]?.label);if(c)c.scrollIntoView({{behavior:'smooth'}});}};
document.getElementById('save').onclick=()=>{{let lines=META.map(m=>JSON.stringify({{...m,...(state[m.id]||{{label:'',note:''}})}}));let a=document.createElement('a');a.href=URL.createObjectURL(new Blob([lines.join('\\n')+'\\n'],{{type:'application/jsonl'}}));a.download='human_table_labels_20260804.jsonl';a.click();}};sync();
</script></body></html>'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dpi", type=int, default=92)
    ap.add_argument("--output", default=str(ROOT / "docs/review/table_human_review_20260804.html"))
    args = ap.parse_args()
    manifest = _manifest()
    cards = _check_cards() + _candidate_cards()
    image_cache: dict[tuple[str, int], str] = {}
    for card in cards:
        key = (card["sha12"], card["page"])
        if key not in image_cache:
            image_cache[key] = _page_image(manifest, *key, args.dpi)
        card["image"] = image_cache[key]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render(cards), encoding="utf-8")
    print(json.dumps({"output": str(output), "cards": len(cards), "bytes": output.stat().st_size}, ensure_ascii=False))


if __name__ == "__main__":
    main()
