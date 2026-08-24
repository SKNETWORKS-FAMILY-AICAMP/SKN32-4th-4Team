"""표 열 수 검수 화면을 만든다 — 사람이 원본을 보고 직접 찍는다.

    python -m scripts.eval.build_table_review --docs 12 --limit 40 --min-quality 0.3

★왜 필요한가

    `find_multi_col` 의 열 수 선택을 **확인할 정답이 없다**(계획서 L2).
    그리고 실측해 보니 문턱 0.6 에서는 984쪽에 3열이 **1건**만 나온다
    (`docs/reports/debugs/2026-08-03_1500_find_multi_col이_3열을_거의_못만든다.md`).
    문턱을 낮춰 **후보를 만들어 내고**, 사람이 찍은 결과로 문턱과 점수 식을 맞춘다.

★★문턱을 낮추는 것은 **고치는 것이 아니라 재료를 만드는 것**이다.
  이 상태로 파이프라인에 연결하면 안 된다.

★화면이 물어보는 것 넷 — 라벨 하나가 네 가지를 알려준다

    1. 어느 후보가 맞나 (또는 **"후보에 정답이 없음"**)
    2. **정답 열 수는 몇인가** — 숫자를 직접 적는다. ★상한을 두지 않는다
       (`kmax=5` 는 6열까지만 만든다. 그런데 7열 표가 실재한다 — 약관보관형식 결정 §2-1)
    3. **병합 셀이 있나** — `_grid_quality` 의 `mode/ncol` 벌점이 여기서 온다는 가설을 가린다
    4. 메모

★산출물은 자기완결 HTML 이다. 이미지를 base64 로 넣어 파일 하나로 연다.
  ★약관 원문 이미지가 들어간다 — **팀 내부 전용.** 외부 배포·공개 저장소 업로드 금지(CLAUDE.md §2).
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import pathlib
import random
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_OUT_DIR = _ROOT / "docs" / "review"


def _cards(n_docs: int, limit: int, min_quality: float, dpi: int, seed: int) -> list[dict]:
    """후보를 모은다. 한 쪽에서 **여러 열 수의 격자를 모두** 만들어 나란히 보여준다."""
    import fitz

    from scripts.crawl.split_manifest import load_all
    from scripts.extract import table_coords as tc

    rows = [r for r in load_all()
            if not (r.get("excluded_reason") or "").strip() and r.get("sha256")]
    by_sha: dict[str, dict] = {}
    for r in rows:
        by_sha.setdefault(r["sha256"][:12], r)
    keys = sorted(by_sha)
    random.Random(seed).shuffle(keys)

    cards: list[dict] = []
    scanned_docs = scanned_pages = 0
    for sha in keys:
        if len(cards) >= limit or scanned_docs >= n_docs:
            break
        meta = by_sha[sha]
        pdf = _ROOT / meta["saved_as"]
        if not pdf.exists():
            continue
        try:
            doc = fitz.open(str(pdf))
        except Exception as e:  # noqa: BLE001
            print(f"  [SKIP] {sha}: {type(e).__name__}", file=sys.stderr)
            continue
        scanned_docs += 1
        for pno in range(doc.page_count):
            if len(cards) >= limit:
                break
            page = doc[pno]
            words = tc.words_of(page)
            if len(words) < 40:
                continue
            scanned_pages += 1
            h = tc.median_h(words)
            tol = h * 0.6
            bounds = tc.corridors(words, h)
            if len(bounds) < 2:      # 경계 2개 = 3열부터가 관심사
                continue

            #: ★열 수를 고정하지 않는다. 만들 수 있는 격자를 **전부** 만들어 점수순으로 준다.
            grids = []
            for k in range(1, min(len(bounds), 8) + 1):
                g = tc.build_grid(words, sorted(bounds[:k]), tol)
                q = tc._grid_quality(g)
                grids.append((q, g))
            grids.sort(key=lambda x: -x[0])
            if not grids or grids[0][0] < min_quality:
                continue

            top = grids[:3]
            pix = page.get_pixmap(dpi=dpi)
            cards.append({
                "id": f"{sha}_p{pno + 1}",
                "sha12": sha,
                "insurer": meta.get("insurer") or pathlib.Path(meta["saved_as"]).parent.name,
                "page": pno + 1,
                "png_b64": base64.b64encode(pix.tobytes("png")).decode(),
                "n_corridors": len(bounds),
                "candidates": [
                    {"cols": g["cols"], "rows": g["rows"], "quality": round(q, 3),
                     "word_coverage": g.get("word_coverage"),
                     #: 화면이 무거워지지 않게 앞 14행만 보여준다. 판단에는 충분하다.
                     "grid": g["grid"][:14], "truncated": len(g["grid"]) > 14}
                    for q, g in top
                ],
            })
        doc.close()
    print(f"문서 {scanned_docs} · 쪽 {scanned_pages} → 후보 {len(cards)}", file=sys.stderr)
    return cards


def _render(cards: list[dict], meta: dict) -> str:
    def grid_html(c: dict) -> str:
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>"
            for row in c["grid"]
        )
        more = '<div class="more">… 이하 생략</div>' if c["truncated"] else ""
        return (f'<div class="cand" data-cols="{c["cols"]}">'
                f'<div class="ch"><b>{c["cols"]}열</b> · {c["rows"]}행 '
                f'· 품질 {c["quality"]} · 단어포함 {c["word_coverage"]}</div>'
                f'<table>{body}</table>{more}</div>')

    items = []
    for i, k in enumerate(cards):
        cands = "".join(grid_html(c) for c in k["candidates"])
        items.append(f"""
<section class="card" data-id="{html.escape(k['id'])}" id="card{i}">
  <header>
    <span class="idx">{i + 1} / {len(cards)}</span>
    <b>{html.escape(k['insurer'])}</b> · {k['sha12']} · <b>p{k['page']}</b>
    · 통로 {k['n_corridors']}개
    <span class="done" hidden>✓ 완료</span>
  </header>
  <div class="body">
    <div class="img"><img src="data:image/png;base64,{k['png_b64']}" loading="lazy"></div>
    <div class="right">
      <div class="cands">{cands}</div>
      <div class="form">
        <div class="row"><label>어느 후보가 맞나</label>
          <div class="pick" data-role="pick"></div>
        </div>
        <div class="row"><label>★정답 열 수 <small>(후보에 없어도 직접 적는다)</small></label>
          <input type="number" min="1" max="20" data-role="truecols" placeholder="예: 7">
        </div>
        <div class="row chk">
          <label><input type="checkbox" data-role="merged"> 병합 셀이 있다</label>
          <label><input type="checkbox" data-role="nottable"> 표가 아니다(본문)</label>
        </div>
        <div class="row"><label>메모</label>
          <input type="text" data-role="note" placeholder="선택">
        </div>
        <div class="row btns">
          <button data-act="ok">✓ 후보가 맞다</button>
          <button data-act="none" class="warn">✗ 후보에 정답이 없다</button>
          <button data-act="unsure" class="mute">? 애매하다</button>
        </div>
      </div>
    </div>
  </div>
</section>""")

    meta_js = json.dumps(meta, ensure_ascii=False)
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>표 열 수 검수 — {meta['built_at']}</title>
<style>
:root{{--fg:#1a1a18;--mute:#6b6b66;--line:#e3e2dd;--ok:#2f5d50;--warn:#8a5a00;--bg:#fbfbfa}}
*{{box-sizing:border-box}}
body{{margin:0;font:15px/1.6 system-ui,'Malgun Gothic',sans-serif;color:var(--fg);background:var(--bg)}}
.top{{position:sticky;top:0;z-index:9;background:#fff;border-bottom:1px solid var(--line);
 padding:10px 16px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}}
.top b{{font-size:17px}} .grow{{flex:1}}
button{{font:inherit;padding:7px 14px;border:1px solid var(--line);background:#fff;border-radius:6px;cursor:pointer}}
button.warn{{border-color:var(--warn);color:var(--warn)}} button.mute{{color:var(--mute)}}
button.primary{{background:var(--ok);color:#fff;border-color:var(--ok)}}
.card{{margin:18px;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden}}
.card.done{{opacity:.55}} .card.done .done{{display:inline!important}}
header{{padding:10px 14px;border-bottom:1px solid var(--line);display:flex;gap:10px;align-items:center}}
.idx{{color:var(--mute);font-variant-numeric:tabular-nums}}
.done{{color:var(--ok);font-weight:700;margin-left:auto}}
.body{{display:grid;grid-template-columns:minmax(320px,1fr) minmax(360px,1fr);gap:14px;padding:14px}}
.img img{{width:100%;border:1px solid var(--line);border-radius:6px}}
.cands{{display:flex;flex-direction:column;gap:10px;max-height:520px;overflow:auto}}
.cand{{border:1px solid var(--line);border-radius:6px;padding:8px}}
.cand.sel{{border-color:var(--ok);box-shadow:0 0 0 2px rgba(47,93,80,.15)}}
.ch{{font-size:13px;color:var(--mute);margin-bottom:6px}}
.cand table{{border-collapse:collapse;width:100%;font-size:12px;table-layout:fixed}}
.cand td{{border:1px solid var(--line);padding:3px 5px;vertical-align:top;
 word-break:break-all;max-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.cand td:hover{{white-space:normal;overflow:visible;background:#fffbe6}}
.more{{font-size:12px;color:var(--mute);padding-top:4px}}
.form{{margin-top:12px;border-top:1px solid var(--line);padding-top:10px}}
.row{{margin-bottom:9px}} .row label{{display:block;font-size:13px;color:var(--mute);margin-bottom:3px}}
.row.chk{{display:flex;gap:16px}} .row.chk label{{display:inline;color:var(--fg)}}
.row input[type=number],.row input[type=text]{{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:5px;width:100%}}
.row input[type=number]{{width:120px}}
.pick{{display:flex;gap:8px;flex-wrap:wrap}}
.pick button.on{{background:var(--ok);color:#fff;border-color:var(--ok)}}
.btns{{display:flex;gap:8px}}
</style></head><body>
<div class="top">
  <b>표 열 수 검수</b>
  <span id="prog" class="idx">0 / {len(cards)}</span>
  <span class="grow"></span>
  <button id="jump">다음 미완료로</button>
  <button id="save" class="primary">결과 내려받기 (JSON)</button>
  <button id="reset" class="mute">초기화</button>
</div>
<div id="cards">{''.join(items)}</div>
<script>
const META = {meta_js};
const KEY = 'table_review_' + META.built_at;
let R = JSON.parse(localStorage.getItem(KEY) || '{{}}');

function paint(sec){{
  const id = sec.dataset.id, r = R[id];
  sec.classList.toggle('done', !!r);
  if(!r) return;
  sec.querySelectorAll('.cand').forEach(c=>c.classList.toggle('sel', +c.dataset.cols===r.picked_cols));
  const q=(k)=>sec.querySelector(`[data-role="${{k}}"]`);
  if(r.true_cols) q('truecols').value = r.true_cols;
  q('merged').checked = !!r.merged; q('nottable').checked = !!r.not_table;
  if(r.note) q('note').value = r.note;
  sec.querySelectorAll('.pick button').forEach(b=>b.classList.toggle('on', +b.dataset.cols===r.picked_cols));
}}
function progress(){{
  document.getElementById('prog').textContent = Object.keys(R).length + ' / ' + META.total;
}}
document.querySelectorAll('.card').forEach(sec=>{{
  // 후보 선택 버튼을 후보 수만큼 만든다
  const pick = sec.querySelector('[data-role="pick"]');
  sec.querySelectorAll('.cand').forEach(c=>{{
    const b=document.createElement('button'); b.textContent=c.dataset.cols+'열';
    b.dataset.cols=c.dataset.cols;
    b.onclick=()=>{{
      pick.querySelectorAll('button').forEach(x=>x.classList.remove('on'));
      b.classList.add('on');
      sec.querySelectorAll('.cand').forEach(x=>x.classList.toggle('sel',x===c));
      const t=sec.querySelector('[data-role="truecols"]');
      if(!t.value) t.value=c.dataset.cols;   // 기본값으로 채워 준다. 고칠 수 있다
    }};
    pick.appendChild(b);
  }});
  sec.querySelectorAll('[data-act]').forEach(btn=>{{
    btn.onclick=()=>{{
      const q=(k)=>sec.querySelector(`[data-role="${{k}}"]`);
      const on=pick.querySelector('button.on');
      R[sec.dataset.id]={{
        verdict: btn.dataset.act,                       // ok | none | unsure
        picked_cols: on ? +on.dataset.cols : null,
        true_cols: q('truecols').value ? +q('truecols').value : null,
        merged: q('merged').checked,
        not_table: q('nottable').checked,
        note: q('note').value || '',
        at: new Date().toISOString()
      }};
      localStorage.setItem(KEY, JSON.stringify(R));
      paint(sec); progress();
      const next=[...document.querySelectorAll('.card')].find(s=>!R[s.dataset.id]);
      if(next) next.scrollIntoView({{behavior:'smooth',block:'start'}});
    }};
  }});
  paint(sec);
}});
progress();
document.getElementById('jump').onclick=()=>{{
  const n=[...document.querySelectorAll('.card')].find(s=>!R[s.dataset.id]);
  if(n) n.scrollIntoView({{behavior:'smooth',block:'start'}}); else alert('전부 찍었습니다.');
}};
document.getElementById('reset').onclick=()=>{{
  if(!confirm('찍은 결과를 모두 지웁니다.')) return;
  R={{}}; localStorage.removeItem(KEY);
  document.querySelectorAll('.card').forEach(s=>{{s.classList.remove('done');paint(s);}}); progress();
}};
document.getElementById('save').onclick=()=>{{
  const cards=[...document.querySelectorAll('.card')].map(s=>({{
    id:s.dataset.id, candidates: META.cards[s.dataset.id]
  }}));
  const out={{...META, labeled_at:new Date().toISOString(),
    labeled: Object.keys(R).length, labels: R}};
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,1)],{{type:'application/json'}}));
  a.download='table_review_labels_'+META.built_at+'.json'; a.click();
}};
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description="표 열 수 검수 화면 생성")
    ap.add_argument("--docs", type=int, default=12, help="스캔할 문서 수")
    ap.add_argument("--limit", type=int, default=40, help="후보(카드) 수 상한")
    ap.add_argument("--min-quality", type=float, default=0.3,
                    help="★후보 문턱. 파이프라인 기본값(0.6)보다 낮춰 재료를 만든다")
    ap.add_argument("--dpi", type=int, default=110)
    ap.add_argument("--seed", type=int, default=20260803)
    a = ap.parse_args()

    cards = _cards(a.docs, a.limit, a.min_quality, a.dpi, a.seed)
    if not cards:
        print("후보가 없습니다. --min-quality 를 더 낮추거나 --docs 를 늘리세요.")
        return 2

    built = "20260803"
    meta = {
        "built_at": built,
        "total": len(cards),
        "params": {"docs": a.docs, "limit": a.limit,
                   "min_quality": a.min_quality, "dpi": a.dpi, "seed": a.seed},
        "_주의": [
            "★이 문턱은 파이프라인 기본값(0.6)보다 낮다. 재료를 만들려고 낮춘 것이지 고친 것이 아니다.",
            "★약관 원문 이미지가 들어 있다 — 팀 내부 전용. 외부 배포 금지(CLAUDE.md §2).",
            "★kmax 상한 때문에 후보는 최대 9열까지만 만들어진다. 정답이 더 많으면 '후보에 정답이 없다'로 찍는다.",
        ],
        "cards": {c["id"]: c["candidates"] for c in cards},
    }
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = _OUT_DIR / f"table_review_{built}.html"
    out.write_text(_render(cards, meta), encoding="utf-8")
    mb = out.stat().st_size / 1e6
    print(f"카드 {len(cards)}개 · {mb:.1f} MB\n→ {out.relative_to(_ROOT)}")
    print("브라우저로 열어 찍고, 다 되면 '결과 내려받기' 를 누르세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
