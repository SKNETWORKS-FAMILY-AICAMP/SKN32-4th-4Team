"""s5(페이지 JSON) ↔ s6(조항 JSON) **참조 무결성** 검사 — 계획서 D1.

★왜 필요한가 — 두 층이 **따로 재생성될 수 있다**

    data/extracted/{보험사}/s5_pymupdf-1.28.0/{sha12}.json          ← 페이지
    data/structured/{보험사}/s6_pymupdf-1.28.0/{sha12}.clauses.json ← 조항

    s6 은 s5 를 읽어 만든다. 그런데 **한쪽만 다시 돌리는 일이 실제로 일어난다** —
    이 저장소는 여러 세션이 동시에 쓰고(계획서 §0 경고), 원격 GPU 상자에 페이지
    추출만 나눠 넘긴 전례가 있다(RULE §3.5). 그때 s5 가 갱신되고 s6 이 옛 판으로
    남으면 조항은 **자기가 가리키는 페이지가 이제 무엇인지 모른 채** 살아 있다.

    이 서비스에서 그건 조용한 오답이다. 판정문은 "약관 몇 쪽 제몇조"를 대는데,
    그 쪽 번호가 원문 밖이거나 다른 내용을 가리켜도 **아무 데서도 걸리지 않는다.**
    지금까지 이 정합을 **아무도 검사하지 않았다.**

★두 등급으로 나눈다 (struct_audit 의 확정 신호 / 검수 신호와 같은 원칙)

    확정 위반  구조상 있을 수 없는 것. 하나라도 나오면 재생성이 어긋난 것이다
    검수 신호  오탐이 섞인다. 세어서 보고하되 자동 실패로 쓰지 않는다

    ★단일 점수를 만들지 않는다. 정답셋 없이 가중치를 정당화할 수 없다.

★검사가 도는지 스스로 증명한다 — `--selftest`

    위반 0건이라는 보고는 **검사가 죽어 있어도 똑같이 나온다.** 실제로 이 저장소에서
    그런 일이 있었다 — 판별 신호를 만들어 놓고 페이지 JSON 에 키를 안 실어
    `is_table` 이 항상 `None` 이었다(계획서 §9). 신호가 아무 일도 하지 않고 있었다.
    그래서 `--selftest` 는 성한 문서 한 쌍을 메모리에서 **일부러 망가뜨려**
    각 검사가 실제로 걸리는지 확인한다. 걸리지 않으면 실패로 끝난다.

실행:
    python -m scripts.eval.consistency_check                  # 전량
    python -m scripts.eval.consistency_check --limit 50       # 앞 50건만
    python -m scripts.eval.consistency_check --selftest       # 검사가 도는지 증명
    python -m scripts.eval.consistency_check --show A2 --max-show 20
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path

from scripts.extract.table_signals import attachment_verdict

_ROOT = Path(__file__).resolve().parents[2]
_EXTRACTED = _ROOT / "data" / "extracted"
_STRUCTURED = _ROOT / "data" / "structured"

#: ★판을 여기 박는다. 경로에 추출기 판이 들어 있어 **다른 판이 같은 폴더에 섞이면**
#:   서로 다른 추출기 결과를 짝지어 채점하게 된다(RULE §3.5-4).
S5_DIR = "s5_pymupdf-1.28.0"
S6_DIR = "s6_pymupdf-1.28.0"
S5_SCHEMA = "5"
S6_SCHEMA = "6"
#: 추출기 문자열 안에 반드시 있어야 하는 판. 실제 값은 `pymupdf/1.28.0:` 이다.
EXTRACTOR_MARK = "1.28.0"

#: ★조항에 실을 수 있는 표 경로. `to_clauses.py` 의 게이트와 **같은 값이어야 한다.**
#:   `2열짝짓기` 는 전량에서 통과분의 80.0% 가 본문이라 다시 닫혀 있다(계획서 §8).
#:   여기 `선` 아닌 것이 실려 있으면 s6 이 **게이트가 열려 있던 옛 판**이라는 뜻이다.
TRUSTED_TABLE_METHODS = {"선"}

_WS = re.compile(r"\s+")
#: `table_id` 는 `p{페이지}-{패널}-{방법}` 꼴이다. 앞의 페이지 번호를 뽑는다.
_TID_PAGE = re.compile(r"^p(\d+)-")

#: 조항 본문이 정말 그 페이지에서 나왔는지 볼 때 쓰는 **앵커 길이**(공백 제거 후).
#:
#: ★길수록 오탐이 는다. 조항 첫머리가 페이지 경계에서 잘리면 뒤쪽이 다음 페이지로
#:   넘어가기 때문이다. 표본 25문서 4,307조항으로 실측:
#:     16자 → 1.18% · 20자 → 1.43% · 24자 → 1.57% 가 page_from 에서 안 잡힘.
#:   그중 **문서 어디에도 없는 것은 1건**뿐이었다. 그래서 16 을 쓰고,
#:   "문서에 없다"(A1·확정)와 "그 쪽에 없다"(A2·검수)를 **나눠서 센다.**
ANCHOR = 16

# ────────────────────────────────────────────────────────────────
# 검사 목록 — 코드 · 등급 · 설명
#   등급 "확정" : 구조상 있을 수 없다. 나오면 재생성이 어긋난 것
#   등급 "검수" : 오탐이 섞인다. 자동 실패로 쓰지 않는다
# ────────────────────────────────────────────────────────────────
CHECKS: dict[str, tuple[str, str]] = {
    # ── 1) 짝 맞음 ──
    "P1": ("확정", "s5 는 있는데 s6 이 없다 (조항이 아직 안 만들어짐)"),
    "P2": ("확정", "s6 은 있는데 s5 가 없다 (근거 페이지가 사라짐)"),
    "P3": ("확정", "같은 sha12 가 s5·s6 에서 **다른 보험사 폴더**에 있다"),
    # ── 2) 원본 대조 ──
    "H1": ("확정", "s6.source.sha256 ≠ s5.source.sha256 (다른 PDF 를 가리킨다)"),
    "H2": ("확정", "파일명 sha12 ≠ source.sha256 앞 12자"),
    # ── 3) 스키마·추출기 판 ──
    "V1": ("확정", f"s5.schema_version ≠ '{S5_SCHEMA}'"),
    "V2": ("확정", f"s6.schema_version ≠ '{S6_SCHEMA}'"),
    "V3": ("확정", f"extractor 문자열에 '{EXTRACTOR_MARK}' 가 없다"),
    "V4": ("확정", "s5.extractor ≠ s6.extractor (다른 판에서 만들어졌다)"),
    # ── 4) 페이지 수 ──
    "G1": ("확정", "s5.stats.pages ≠ len(s5.pages)"),
    "G2": ("확정", "s6.stats.pages ≠ s5.stats.pages"),
    "G3": ("확정", "s5 페이지 번호가 1..N 연속이 아니다 (결번·중복)"),
    # ── 5) locator 페이지 범위 ──
    "L1": ("확정", "locator 결측 또는 page_from/page_to 가 정수가 아니다"),
    "L2": ("확정", "locator 페이지가 원문 밖 (1..N 을 벗어남)"),
    "L3": ("확정", "page_to < page_from (역전)"),
    "L4": ("확정", "toc_pages 가 원문 페이지 범위 밖"),
    # ── 6) 표 참조 ──
    "T1": ("확정", "tables[].page 가 원문 페이지 범위 밖"),
    "T2": ("확정", "tables[].table_id 가 s5 그 페이지에 없다"),
    "T3": ("확정", "table_id 접두 페이지 ≠ tables[].page"),
    "T4": ("확정", "tables[].page 가 자기 조항의 locator 범위 밖"),
    "T5": ("확정", "실린 표가 현재 조항 부착 게이트를 통과하지 않는다"),
    "T6": ("확정", "s6 표 내용이 s5 원본과 다르다 (rows·cols·word_coverage·레코드 수)"),
    "T7": ("확정", "tables_on_pages 의 페이지 키가 원문 범위 밖"),
    "T8": ("확정", "tables_on_pages[p] ≠ s5 그 페이지의 tables 개수"),
    "T9": ("확정", "s5 의 `선` 표가 조항·부록 페이지 범위 **안**인데 s6 에 안 실렸다"),
    "W1": ("확정", "stats.tables_withheld_unverified ≠ s5 실측 보류 수"),
    # ── 7) 본문 앵커 ──
    "A1": ("확정", f"조항 본문 앞 {ANCHOR}자가 s5 문서 **어디에도** 없다"),
    "A2": ("검수", f"조항 본문 앞 {ANCHOR}자가 page_from 페이지에 없다 (문서 안엔 있다)"),
}


def _norm(t: str) -> str:
    return _WS.sub("", t or "")


def _fmt(v: object) -> str:
    s = str(v)
    return s if len(s) <= 90 else s[:87] + "…"


# ────────────────────────────────────────────────────────────────
# 한 쌍 검사 — ★순수 함수로 둔다. `--selftest` 가 메모리에서 망가뜨려 부를 수 있어야 한다
# ────────────────────────────────────────────────────────────────
def check_pair(sha12: str, d5: dict, d6: dict, *, anchor: bool = True,
               seen: collections.Counter | None = None) -> list[tuple[str, str]]:
    """성한 한 쌍이면 빈 리스트. 반환: [(검사코드, 사람이 읽을 상세)]

    `seen` — ★**분모**를 센다. "0건"은 검사가 아무것도 안 봤을 때도 나온다.
      무엇을 몇 개 보고 0건인지 함께 적어야 그 0이 의미를 갖는다(CLAUDE.md §4).
    """
    out: list[tuple[str, str]] = []
    cnt = seen if seen is not None else collections.Counter()
    cnt["문서쌍"] += 1

    def bad(code: str, detail: str) -> None:
        out.append((code, detail))

    # ── 원본 대조 ────────────────────────────────────────────────
    #: ★이게 가장 먼저다. 해시가 다르면 **아래 모든 비교가 무의미**하다 —
    #:   서로 다른 PDF 의 페이지 수와 조항을 대조하게 된다.
    h5 = (d5.get("source") or {}).get("sha256") or ""
    h6 = (d6.get("source") or {}).get("sha256") or ""
    if h5 != h6:
        bad("H1", f"s5={h5[:16]}… s6={h6[:16]}…")
    for tag, h in (("s5", h5), ("s6", h6)):
        if not h.startswith(sha12):
            bad("H2", f"{tag} 파일명 {sha12} ≠ sha256 {h[:12]}")

    # ── 스키마·추출기 판 ─────────────────────────────────────────
    if d5.get("schema_version") != S5_SCHEMA:
        bad("V1", f"s5.schema_version={d5.get('schema_version')!r}")
    if d6.get("schema_version") != S6_SCHEMA:
        bad("V2", f"s6.schema_version={d6.get('schema_version')!r}")
    e5, e6 = d5.get("extractor") or "", d6.get("extractor") or ""
    for tag, e in (("s5", e5), ("s6", e6)):
        if EXTRACTOR_MARK not in e:
            bad("V3", f"{tag}.extractor={e!r}")
    if e5 != e6:
        bad("V4", f"s5={e5!r} s6={e6!r}")

    # ── 페이지 수 ────────────────────────────────────────────────
    pages = d5.get("pages") or []
    n_pages = len(pages)
    st5 = d5.get("stats") or {}
    st6 = d6.get("stats") or {}
    if st5.get("pages") != n_pages:
        bad("G1", f"stats.pages={st5.get('pages')} · 실제 {n_pages}")
    if st6.get("pages") != st5.get("pages"):
        bad("G2", f"s6={st6.get('pages')} · s5={st5.get('pages')}")
    nums = [p.get("page") for p in pages]
    if nums != list(range(1, n_pages + 1)):
        #: 결번·중복·0-based 혼입. ★`tables[].page` 를 1-based 로 믿고 인덱싱하므로
        #:   여기가 깨지면 아래 표 검사가 **엉뚱한 쪽을 본다.**
        dup = [k for k, v in collections.Counter(nums).items() if v > 1]
        bad("G3", f"n={n_pages} 중복={dup[:5]} 처음={nums[:3]} 끝={nums[-3:]}")

    #: s5 를 페이지 번호로 색인해 둔다. 아래 표·앵커 검사가 전부 여기를 본다.
    by_page = {p.get("page"): p for p in pages}
    #: 페이지별 `table_id → 표` (좌표 복원 표). s6 이 가리키는 표가 실재하는지 본다.
    tid_of: dict[int, dict[str, dict]] = {}
    n_withheld = 0
    for p in pages:
        m: dict[str, dict] = {}
        for t in p.get("tables_coords") or []:
            if t.get("table_id"):
                m[t["table_id"]] = t
            attachable, _ = attachment_verdict(t)
            if not attachable:
                n_withheld += 1
        tid_of[p.get("page")] = m

    # ── 보류 수 대조 ─────────────────────────────────────────────
    #: ★★s5 가 조용히 갈렸는지 잡는 **가장 값싼 전면 지문**이다.
    #:   s6 은 만들 때 "게이트에서 거른 표 수"를 stats 에 적어 뒀다. 그 수는
    #:   s5 의 `tables_coords` 전량에서 계산된 값이라 s5 가 한 장이라도 다시
    #:   추출되면 어긋난다. locator·표 참조가 다 맞아도 여기서 걸린다.
    w6 = st6.get("tables_withheld_unverified")
    if w6 is not None and w6 != n_withheld:
        bad("W1", f"s6 기록 {w6} · s5 실측 {n_withheld}")

    # ── 목차 페이지 ──────────────────────────────────────────────
    cnt["페이지"] += n_pages
    cnt["s5 좌표표"] += sum(len(tid_of[k]) for k in tid_of)
    for tp in d6.get("toc_pages") or []:
        cnt["목차쪽"] += 1
        if not isinstance(tp, int) or not (1 <= tp <= n_pages):
            bad("L4", f"toc_page={tp!r} · 원문 1..{n_pages}")

    # ── 조항·부록 ────────────────────────────────────────────────
    #: 앵커 대조용. 문서를 통째로 이어 붙인 정규화 텍스트는 **한 번만** 만든다.
    #:
    #: ★★**목차 페이지를 빼고 이어 붙인다.** `to_clauses.py:928` 이 조항 본문을 모을 때
    #:   `if p in toc_pages: continue` 로 건너뛰기 때문이다. 빼지 않으면 이어붙임 **지점이
    #:   달라져** 목차 바로 앞 조항이 통째로 "원문에 없다"로 찍힌다.
    #:   실제로 그렇게 재서 A1 이 124건 나왔고, 전부 같은 모양이었다 —
    #:   `제34조(보험계약대출)` 다음이 s5 에서는 목차 쪽, s6 에서는 그 다음 쪽이었다.
    #:   ★**검사 쪽 오진이었다.** 위반이 나오면 데이터를 의심하기 전에
    #:     생성기와 같은 규칙으로 재고 있는지 먼저 본다.
    page_txt: dict[int, str] = {}
    doc_txt = ""
    if anchor:
        toc = {p for p in (d6.get("toc_pages") or []) if isinstance(p, int)}
        page_txt = {p.get("page"): _norm(p.get("text") or "") for p in pages}
        doc_txt = "".join(page_txt[k] for k in sorted(page_txt, key=lambda x: (x is None, x))
                          if k not in toc)

    #: T9 용 — s6 이 실제로 실은 표와, 조항·부록이 덮는 페이지.
    carried: set[str] = set()
    covered: set[int] = set()

    for key in ("clauses", "annexes"):
        for c in d6.get(key) or []:
            where = f"{key}[{c.get('ordinal')}] {c.get('citation') or c.get('label') or c.get('clause_no') or ''}"
            cnt[key] += 1

            # locator ------------------------------------------------
            loc = c.get("locator") or {}
            pf, pt = loc.get("page_from"), loc.get("page_to")
            if not isinstance(pf, int) or not isinstance(pt, int):
                bad("L1", f"{where} locator={loc!r}")
                continue
            locator_in_bounds = (1 <= pf <= n_pages) and (1 <= pt <= n_pages)
            if not locator_in_bounds:
                bad("L2", f"{where} p{pf}~{pt} · 원문 1..{n_pages}")
            if pt < pf:
                bad("L3", f"{where} page_from={pf} > page_to={pt}")
            #: 범위 밖 locator 를 1..N 으로 clamp해 coverage에 넣으면 뒤의
            #: T9가 존재하지 않는 유효 범위를 검사한다. L2/L3는 이미 위반으로
            #: 남기되, 반대방향 표 검사는 유효한 locator만 사용한다.
            if locator_in_bounds and pt >= pf:
                covered |= set(range(pf, pt + 1))

            # tables_on_pages ---------------------------------------
            #: 개수만 적힌 층(PyMuPDF `tables`). 아래 `tables` 와 **다른 것**이다.
            for k, v in (c.get("tables_on_pages") or {}).items():
                cnt["tables_on_pages 항목"] += 1
                try:
                    pn = int(k)
                except (TypeError, ValueError):
                    bad("T7", f"{where} tables_on_pages 키 {k!r} 가 정수가 아니다")
                    continue
                if not (1 <= pn <= n_pages):
                    bad("T7", f"{where} tables_on_pages p{pn} · 원문 1..{n_pages}")
                    continue
                real = len(by_page.get(pn, {}).get("tables") or [])
                if v != real:
                    bad("T8", f"{where} p{pn} s6={v} · s5={real}")

            # tables[] ----------------------------------------------
            for tb in c.get("tables") or []:
                cnt["실린 표 참조"] += 1
                pn, tid = tb.get("page"), tb.get("table_id")
                carried.add(tid or "")
                if not isinstance(pn, int) or not (1 <= pn <= n_pages):
                    bad("T1", f"{where} table page={pn!r} · 원문 1..{n_pages}")
                    continue
                #: ★생성 규칙상 `range(page_from, page_to+1)` 에서만 붙는다.
                #:   벗어났다면 s6 의 locator 와 tables 가 **서로 다른 판**이다.
                if not (pf <= pn <= pt):
                    bad("T4", f"{where} table p{pn} 이 조항 p{pf}~{pt} 밖")
                m = _TID_PAGE.match(tid or "")
                if m and int(m.group(1)) != pn:
                    bad("T3", f"{where} table_id={tid} · page={pn}")
                attachable, why = attachment_verdict(tb)
                if not attachable:
                    bad("T5", f"{where} {tid} reasons={why!r}")
                src = tid_of.get(pn, {}).get(tid or "")
                if src is None:
                    bad("T2", f"{where} p{pn} 에 table_id={tid!r} 없음")
                    continue
                #: ★내용까지 본다. id 만 맞고 값이 갈리면 **표는 있는데 다른 표**다.
                for f in ("rows", "cols", "word_coverage", "method", "panel"):
                    if tb.get(f) != src.get(f):
                        bad("T6", f"{where} {tid} {f}: s6={tb.get(f)!r} s5={src.get(f)!r}")
                if len(tb.get("records") or []) != len(src.get("records") or []):
                    bad("T6", f"{where} {tid} records: "
                              f"s6={len(tb.get('records') or [])} s5={len(src.get('records') or [])}")

            # 본문 앵커 ---------------------------------------------
            if anchor:
                a = _norm(c.get("text") or "")[:ANCHOR]
                #: 짧은 조항은 건너뛴다. 우연 일치로 무의미해진다.
                if len(a) == ANCHOR:
                    cnt["앵커 대조"] += 1
                    if a not in page_txt.get(pf, ""):
                        bad("A1" if a not in doc_txt else "A2", f"{where} p{pf} · {a!r}")

    # ── 반대 방향: s5 의 표가 s6 에 도달했는가 ────────────────────
    #: ★★위의 T1~T6 은 **s6 → s5** 만 본다. 그것만으로는 "s6 이 아무 표도 안 실었다"가
    #:   만점으로 나온다. 실제로 그런 종류의 착시가 있었다 — `table_coords` 는 F1 1.000
    #:   인데 부르는 곳이 평가 스크립트뿐이었다(`clause_table_check` 머리말).
    #:   그래서 **s5 → s6** 도 본다.
    #:
    #: ★단, 표지·안내문처럼 **어느 조항에도 속하지 않는 쪽**의 표는 위반이 아니다.
    #:   실측(전량): 미실림 200개 중 189개가 조항 범위 밖 앞쪽 페이지,
    #:   11개가 목차쪽이었다. 범위 **안**인데 빠진 것은 0개다.
    #:   그 둘을 섞어 세면 정상 동작을 결함으로 보고하게 된다.
    for p in pages:
        pn = p.get("page")
        for t in p.get("tables_coords") or []:
            attachable, _ = attachment_verdict(t)
            if not attachable:
                continue
            cnt["s5 신뢰 표(선)"] += 1
            if t.get("table_id") in carried:
                continue
            if pn in covered:
                bad("T9", f"p{pn} {t.get('table_id')} (레코드 {len(t.get('records') or [])})")
            else:
                #: 조항 범위 밖 — 판정이 이 표를 근거로 들 길이 없다. 세어만 둔다.
                cnt["s5 신뢰 표·조항 범위 밖"] += 1
    return out


# ────────────────────────────────────────────────────────────────
# 자기 검사 — ★검사가 실제로 도는지 증명한다
# ────────────────────────────────────────────────────────────────
def _mutations() -> list[tuple[str, str, callable]]:
    """(검사코드, 무엇을 망가뜨리나, 망가뜨리는 함수 (d5, d6) -> None)

    ★"위반 0건" 은 검사가 죽어 있어도 나온다. 각 검사가 **자기 위반을 실제로
      잡는지** 여기서 확인한다. 잡지 못하면 이 스크립트는 실패로 끝난다.
      P1·P2·P3 은 파일 배치 문제라 한 쌍 안에서 만들 수 없다 — 아래 별도 처리.
    """
    def first_with_tables(d6):
        for key in ("clauses", "annexes"):
            for c in d6.get(key) or []:
                if c.get("tables"):
                    return c
        return None

    def first_clause(d6):
        for key in ("clauses", "annexes"):
            for c in d6.get(key) or []:
                return c
        return None

    def first_with_top(d6):
        for key in ("clauses", "annexes"):
            for c in d6.get(key) or []:
                if c.get("tables_on_pages"):
                    return c
        return None

    def _set(c, k, v):
        c[k] = v

    return [
        #: ★13번째 글자를 바꾼다. 앞 12자(=파일명)는 그대로 둬야 H2 와 섞이지 않는다.
        #:   초안은 **첫 글자**를 `0` 으로 바꿨는데 하필 그 해시가 `0` 으로 시작해
        #:   아무것도 안 바뀌었고, 자기검사가 "H1 안 걸림"으로 이를 잡아냈다.
        #:   망가뜨리기가 실제로 망가뜨렸는지도 확인해야 한다.
        ("H1", "s6 의 sha256 을 (파일명 뒤에서) 한 글자 바꾼다",
         lambda d5, d6: _set(d6["source"], "sha256", _flip(d6["source"]["sha256"], 12))),
        ("H2", "양쪽 sha256 을 파일명과 어긋나게 한다",
         lambda d5, d6: [_set(d["source"], "sha256", "f" * 64) for d in (d5, d6)]),
        ("V1", "s5.schema_version 을 옛 판으로 되돌린다",
         lambda d5, d6: _set(d5, "schema_version", "4")),
        ("V2", "s6.schema_version 을 옛 판으로 되돌린다",
         lambda d5, d6: _set(d6, "schema_version", "5")),
        ("V3", "extractor 판 문자열을 지운다",
         lambda d5, d6: [_set(d, "extractor", "pymupdf/1.24.0:") for d in (d5, d6)]),
        ("V4", "s6 만 다른 추출기 판으로 만든다",
         lambda d5, d6: _set(d6, "extractor", "pymupdf/1.28.0-alt:")),
        ("G1", "s5 마지막 페이지를 지운다 (stats 는 그대로)",
         lambda d5, d6: d5["pages"].pop()),
        ("G2", "s6 이 기억하는 페이지 수를 늘린다",
         lambda d5, d6: _set(d6["stats"], "pages", d6["stats"]["pages"] + 1)),
        ("G3", "s5 페이지 번호를 0-based 로 밀어 버린다",
         lambda d5, d6: [p.update(page=p["page"] - 1) for p in d5["pages"]]),
        ("L1", "조항의 locator 를 지운다",
         lambda d5, d6: _set(first_clause(d6), "locator", {})),
        ("L2", "조항이 원문 밖 쪽을 가리키게 한다",
         lambda d5, d6: first_clause(d6)["locator"].update(page_from=99_999, page_to=99_999)),
        ("L3", "page_from 과 page_to 를 뒤집는다",
         lambda d5, d6: first_clause(d6)["locator"].update(page_from=2, page_to=1)),
        ("L4", "toc_pages 에 원문 밖 쪽을 넣는다",
         lambda d5, d6: _set(d6, "toc_pages", [99_999])),
        ("T1", "표가 원문 밖 쪽에 붙었다고 한다",
         lambda d5, d6: _set(first_with_tables(d6)["tables"][0], "page", 99_999)),
        ("T2", "s5 에서 그 표를 지운다 (s6 은 계속 가리킨다)",
         lambda d5, d6: [p.update(tables_coords=[]) for p in d5["pages"]]),
        ("T3", "table_id 의 페이지 접두만 바꾼다",
         lambda d5, d6: _set(first_with_tables(d6)["tables"][0], "table_id",
                             "p1-" + (first_with_tables(d6)["tables"][0]["table_id"] or "").split("-", 1)[-1])),
        ("T4", "표를 자기 조항 범위 밖 쪽으로 옮긴다",
         lambda d5, d6: _set(first_with_tables(d6)["tables"][0], "page",
                             first_with_tables(d6)["locator"]["page_from"] - 1)),
        ("T5", "보류 경로(2열짝짓기) 표가 실린 것처럼 만든다",
         lambda d5, d6: _set(first_with_tables(d6)["tables"][0], "method", "2열짝짓기")),
        ("T6", "s6 표의 행 수만 바꾼다 (id 는 그대로)",
         lambda d5, d6: _set(first_with_tables(d6)["tables"][0], "rows",
                             (first_with_tables(d6)["tables"][0].get("rows") or 0) + 1)),
        ("T7", "tables_on_pages 에 원문 밖 쪽을 넣는다",
         lambda d5, d6: first_with_top(d6)["tables_on_pages"].update({"99999": 1})),
        ("T8", "tables_on_pages 개수를 부풀린다",
         lambda d5, d6: first_with_top(d6)["tables_on_pages"].update(
             {k: v + 7 for k, v in first_with_top(d6)["tables_on_pages"].items()})),
        #: ★s6 에서 표를 통째로 떼면 s5 의 `선` 표가 갈 곳을 잃는다.
        #:   조항 범위 **안**의 표가 하나라도 있으면 T9 가 걸려야 한다.
        ("T9", "s6 에서 실린 표를 전부 떼어 낸다 (s5 에는 그대로 있다)",
         lambda d5, d6: [c.update(tables=[]) for k in ("clauses", "annexes")
                         for c in (d6.get(k) or [])]),
        ("W1", "보류 수 기록을 흔든다",
         lambda d5, d6: _set(d6["stats"], "tables_withheld_unverified",
                             (d6["stats"].get("tables_withheld_unverified") or 0) + 1)),
        ("A1", "조항 본문을 원문에 없는 글로 바꾼다",
         lambda d5, d6: _set(first_clause(d6), "text", "이문장은약관원문에없는가짜본문이다" * 3)),
        ("A2", "본문은 그대로 두고 page_from 만 다른 쪽으로 옮긴다",
         lambda d5, d6: first_clause(d6)["locator"].update(
             page_from=_a2_page(d5, d6), page_to=len(d5["pages"]))),
    ]


def _flip(s: str, i: int) -> str:
    """`s` 의 i번째 글자를 **반드시 다른 글자**로 바꾼다."""
    return s[:i] + ("f" if s[i] != "f" else "0") + s[i + 1:]


def _a2_page(d5: dict, d6: dict) -> int:
    """A2 용 — 본문이 **없는** 쪽 번호를 고른다.

    ★아무 쪽이나 고르면 안 된다. 우연히 그 쪽에 본문이 있으면 검사가 안 걸리고,
      그러면 "검사가 죽었다"는 잘못된 결론이 난다. 실제로 안 나오는 쪽을 고른다.
    """
    c = next(x for key in ("clauses", "annexes") for x in (d6.get(key) or []))
    a = _norm(c.get("text") or "")[:ANCHOR]
    for p in d5["pages"]:
        if a not in _norm(p.get("text") or ""):
            return p["page"]
    raise RuntimeError("A2 자기검사용 페이지를 못 찾았다")


def selftest(sha12: str, d5: dict, d6: dict) -> int:
    """성한 한 쌍을 골라 검사별로 망가뜨려 본다. 하나라도 안 걸리면 실패."""
    base = check_pair(sha12, d5, d6)
    print(f"[자기검사] 기준 문서 {sha12} · 성한 상태 위반 {len(base)}건")
    if base:
        print("  ★기준 문서가 이미 위반을 갖고 있다. 다른 문서를 골라야 한다:")
        for code, det in base[:5]:
            print(f"    {code} {det}")
        return 1

    n_fail = 0
    covered = set()
    for code, what, mutate in _mutations():
        m5 = json.loads(json.dumps(d5))
        m6 = json.loads(json.dumps(d6))
        try:
            mutate(m5, m6)
        except Exception as e:                       # noqa: BLE001
            print(f"  ✗ {code}  망가뜨리기 실패: {type(e).__name__} {e}")
            n_fail += 1
            continue
        got = {c for c, _ in check_pair(sha12, m5, m6)}
        ok = code in got
        covered.add(code)
        n_fail += 0 if ok else 1
        others = sorted(got - {code})
        print(f"  {'✓' if ok else '✗'} {code}  {what}"
              + (f"   (함께 걸림: {','.join(others)})" if ok and others else "")
              + ("" if ok else f"   → 걸린 것: {sorted(got) or '없음'}"))

    #: 파일 배치 검사(P1~P3)는 한 쌍 안에서 만들 수 없다. 목록 단계에서 도는 것이라
    #: 여기서는 **덮지 못한다고 정직하게 적는다.**
    missing = sorted(set(CHECKS) - covered)
    if missing:
        print(f"\n  ★자기검사가 덮지 못한 검사: {missing}"
              f"\n    (파일 배치 검사다 — 목록 단계에서 돌며 한 쌍 안에서 재현할 수 없다)")
    print(f"\n[자기검사] {len(covered) - n_fail}/{len(covered)} 통과")
    return 1 if n_fail else 0


# ────────────────────────────────────────────────────────────────
def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="앞 N건만 (0=전량)")
    ap.add_argument("--sha", default="", help="한 문서만")
    ap.add_argument("--no-anchor", action="store_true", help="본문 앵커 대조를 끈다 (빠름)")
    ap.add_argument("--selftest", action="store_true", help="검사가 실제로 도는지 증명")
    ap.add_argument("--show", default="", help="이 검사코드의 사례를 출력 (쉼표 구분)")
    ap.add_argument("--max-show", type=int, default=10)
    args = ap.parse_args()

    #: ── 목록: sha12 → 보험사 폴더 ──
    s5 = {p.stem: p for p in _EXTRACTED.glob(f"*/{S5_DIR}/*.json")}
    s6 = {p.name[:-len(".clauses.json")]: p
          for p in _STRUCTURED.glob(f"*/{S6_DIR}/*.clauses.json")}
    print(f"s5 {len(s5):,}건 · s6 {len(s6):,}건")

    findings: dict[str, list[str]] = collections.defaultdict(list)
    #: 문서 단위로도 센다. 한 문서가 위반 200건을 내면 "건수"만으로는 규모를 오해한다.
    docs_with: dict[str, set[str]] = collections.defaultdict(set)

    # ── 짝 맞음 (P1~P3) ─────────────────────────────────────────
    for sha in sorted(set(s5) - set(s6)):
        findings["P1"].append(f"{sha} ({s5[sha].parts[-3]})")
        docs_with["P1"].add(sha)
    for sha in sorted(set(s6) - set(s5)):
        findings["P2"].append(f"{sha} ({s6[sha].parts[-3]})")
        docs_with["P2"].add(sha)
    both = sorted(set(s5) & set(s6))
    for sha in both:
        i5, i6 = s5[sha].parts[-3], s6[sha].parts[-3]
        if i5 != i6:
            findings["P3"].append(f"{sha} s5={i5} s6={i6}")
            docs_with["P3"].add(sha)

    if args.sha:
        both = [x for x in both if x.startswith(args.sha)]
    if args.limit:
        both = both[:args.limit]

    # ── 자기검사 ────────────────────────────────────────────────
    if args.selftest:
        for sha in both:
            d5, d6 = _load(s5[sha]), _load(s6[sha])
            #: 표가 실린 문서라야 T 계열을 다 시험할 수 있다.
            if any(c.get("tables") for k in ("clauses", "annexes") for c in (d6.get(k) or [])) \
                    and not check_pair(sha, d5, d6):
                return selftest(sha, d5, d6)
        print("★자기검사용 문서를 못 찾았다 (표가 실렸고 위반이 0건인 문서)")
        return 1

    # ── 전량 검사 ───────────────────────────────────────────────
    t0 = time.time()
    status = collections.Counter()
    seen: collections.Counter = collections.Counter()
    for i, sha in enumerate(both, 1):
        d5, d6 = _load(s5[sha]), _load(s6[sha])
        status[d6.get("parse_status")] += 1
        for code, det in check_pair(sha, d5, d6, anchor=not args.no_anchor, seen=seen):
            findings[code].append(f"{sha} {det}")
            docs_with[code].add(sha)
        if i % 200 == 0:
            print(f"  … {i:,}/{len(both):,}  ({time.time() - t0:.0f}s)", flush=True)

    n = len(both)
    print(f"\n대조한 쌍 {n:,} · {time.time() - t0:.0f}초")
    print("parse_status " + " · ".join(f"{k} {v:,}" for k, v in status.most_common()))
    #: ★분모. "0건"이 무엇을 보고 난 0인지 밝힌다.
    print("검사 모수  " + " · ".join(f"{k} {v:,}" for k, v in seen.most_common()))

    for grade, title in (("확정", "확정 위반 — 하나라도 나오면 재생성이 어긋난 것"),
                         ("검수", "검수 신호 — 오탐이 섞인다. 자동 실패로 쓰지 않는다")):
        print(f"\n── {title} ──")
        for code, (g, desc) in CHECKS.items():
            if g != grade:
                continue
            k = len(findings[code])
            docs = len(docs_with[code])
            mark = "  " if k == 0 else "★ "
            print(f"{mark}{code}  {k:>8,} 건 · 문서 {docs:>5,}"
                  f"({100 * docs / max(n, 1):5.1f}%)  {desc}")

    total = sum(len(v) for c, v in findings.items() if CHECKS[c][0] == "확정")
    print(f"\n확정 위반 합계 {total:,}건 · 검수 신호 "
          f"{sum(len(v) for c, v in findings.items() if CHECKS[c][0] == '검수'):,}건")

    for code in [c.strip() for c in args.show.split(",") if c.strip()]:
        print(f"\n── {code} 사례 ({len(findings[code]):,}건 중 최대 {args.max_show}) ──")
        for line in findings[code][:args.max_show]:
            print("  " + _fmt(line))

    print("\n★이 검사는 **s5 와 s6 이 서로 맞는지**만 본다.")
    print("  둘이 사이좋게 같이 틀린 것은 잡지 못한다 — 표 추출 정확도는 계획서 L1·D5 의 몫이다.")
    #: ★확정 위반이 있으면 0 이 아닌 코드로 끝낸다. CI 에 걸 수 있어야 한다.
    return 1 if total else 0


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    raise SystemExit(main())
