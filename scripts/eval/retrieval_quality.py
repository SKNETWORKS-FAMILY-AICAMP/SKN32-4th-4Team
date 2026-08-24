"""검색 품질 검증셋 — **목차 오염 · 면책 recall · 페이지 해소.**

    python -m scripts.eval.retrieval_quality build            # 검증셋 생성
    python -m scripts.eval.retrieval_quality score            # baseline 측정

★왜 이 세 가지인가

    이 서비스는 **"보장됩니다"라고 잘못 말하면 사람이 손해를 본다**(CLAUDE.md §0).
    그 사고가 나는 경로가 정확히 셋이다.

      ① 목차를 조항으로 착각해 인용한다 → 근거가 근거가 아니다
      ② 「보상하지 않는 사항」을 못 찾는다 → **없는 면책을 없다고 답한다**
      ③ 쪽수를 틀리게 붙인다 → 사람이 원문을 펴 보면 그 자리에 없다

    ②가 가장 무겁다. ①·③은 사람이 확인하면 드러나지만
    ②는 **드러나지 않는다** — 안 나온 조항은 화면에 없기 때문이다.

★질의를 **지어내지 않는다**(`build_retrieval_set.py` 머리말과 같은 이유)

    LLM 으로 질문을 만들면 그 LLM 의 표현 습관을 잘 맞히는 모델이 이긴다.
    평가가 모델이 아니라 **질문 생성기**를 재게 된다.
    그래서 질의는 **문서에 이미 적힌 조항 제목 원문**만 쓴다.

    ★그래서 한 가지를 더 한다 — **타사 표현으로 묻기.**
      제 문서에 적힌 제목을 그대로 물으면 낱말 검색이 글자만 겹쳐 봐도 맞는다.
      그건 검색 품질이 아니라 문자열 일치를 재는 것이다.
      그래서 **다른 보험사 문서에 적힌 다른 표기**(「보험금을 지급하지 아니하는 사유」)로
      묻는다. 여전히 지어낸 문장이 아니고, 표기가 달라도 찾아내는지를 잰다.

★지금은 임베딩 벡터가 없다

    승인 릴리스의 `embed_profile` 이 비어 있다(모델 미확정).
    그래서 **점수는 낱말 포함 검색**(`app/adapters/file_clause_store.search`)으로 잰다.
    이 값은 임베딩 검색의 점수가 **아니다.** 검색기가 붙으면 같은 검증셋으로 다시 잰다.

★세대(tag)를 자동으로 고르지 않는다

    `app/core/release.py` 의 승인 포인터를 쓰되, 평가용으로 다른 세대를 보려면
    `--tag` 로 **명시**한다. 검증셋 파일에 어느 세대로 만들었는지 박아 두고,
    다른 세대로 채점하려 하면 **멈춘다**(조용히 섞으면 gold 가 안 맞는다).

산출물: data/eval/retrieval_probes.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import sys
from collections import Counter, defaultdict

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_STRUCT = _ROOT / "data" / "structured"
_EXTRACTED = _ROOT / "data" / "extracted"
_OUT = _ROOT / "data" / "eval" / "retrieval_probes.json"

#: 검증셋에 담을 문서 수. 보험사별로 고르게 뽑는다.
#: ★작다. 1,367건 중 120건이면 8.8% 다. 리포트에 그대로 적는다.
DOC_N = 120
#: 문서당 페이지 해소를 검사할 조항 수 상한. 큰 문서가 표본을 삼키지 않게 자른다.
PAGE_PROBE_PER_DOC = 20
SEED = 20260803

#: ── 지표 ① 목차형 판정 규칙 ──────────────────────────────────────
#:
#: "무엇이 목차인가"를 먼저 정해야 오염을 셀 수 있다. 신호 셋을 쓴다.
#:
#:   A 시작쪽이 목차쪽 — `locator.page_from` 이 문서 `toc_pages` 안에 있다
#:   B 점선줄 비율    — 「제12조 ……… 27」 처럼 점선 리더가 깔린 줄
#:   C 쪽번호줄 비율  — 줄 끝이 맨 숫자로 끝나는 줄(목차·색인의 모양)
#:
#: ★★A 를 처음엔 **「page_from~page_to 가 목차쪽을 지나면」** 으로 썼다. 틀렸다.
#:   그러면 표본 119문서에서 89건이 걸렸는데, 전수 확인하니 81건이
#:   **목차를 가로질러 뻗은 정상 조항**이었다(예: 롯데손보 `제35조` page 2→34).
#:   목차 쪽은 이미 조립에서 빠지므로 본문에 목차가 들어오지 않는다.
#:   즉 이건 목차 오염이 아니라 **조 경계 실패**이고, 다른 지표(③)에서 세야 한다.
#:   좁혀서 `page_from ∈ toc_pages` 로 바꾼 뒤 실측 **0건**이다.
#: ★그래서 A 는 지금 아무것도 잡지 않는다. **그래도 남긴다** — 전처리가 목차 배제를
#:   느슨하게 바꾸면 여기서 먼저 터져야 한다(회귀 감시).
#: ★남은 오염은 **목차로 판정되지 못한 쪽**에 있다. B·C 만 그걸 잡는다.
#: ★임계값 0.30 의 근거: 위 표본에서 목차 밖 조항의 점선줄 비율 p99 가 0.0,
#:   0.30 을 넘는 것이 14,036개 중 **7개**뿐이었고 그 7개를 전수 확인한 결과
#:   7개 모두 실제 색인·법규목록이었다(precision 7/7).
#:   ★rule of three — 오탐 0/7 이므로 오탐률 95% 상한은 3/7 = **43%** 다.
#:     "오탐이 없다"고 말할 수 있는 표본이 아니다.
#: ★재현율(recall)은 **모른다.** 목차 정답셋이 없다. 놓친 오염은 못 센다.
_TOC_DOT = re.compile(r"[.·․‧∙•⋅…‥]{4,}|[ㆍ]{4,}")
_TOC_PAGENUM = re.compile(r"^\s*\S.*?\s\d{1,4}\s*$")
TOC_MIN_LINES = 4
TOC_DOT_RATIO = 0.30
TOC_PAGENUM_RATIO = 0.50

#: ── 지표 ② 면책 조항 제목 ────────────────────────────────────────
#:
#: ★표기가 여러 가지다. 표본 200문서에서 실제로 관측된 것만 쓴다(지어내지 않는다).
#:     보상하지 않는 사항 / 보상하지않는사항 / 보상하지 아니하는 손해 / 보상하지 않는 손해
#:     보험금을 지급하지 않는 사유 / 보험금을 지급하지 아니하는 사유
#:     보험금을 지급하지 아니하는 보험사고 / 특별면책조건의 내용 / 특약면책조건의 내용
#: ★일부러 **뺀 것**: 「약관상 보장하지 않는 원인으로 사망시 지급금」.
#:   이건 면책 조항이 아니라 **지급금 조항**이다. 이름에 '보장하지 않는'이 들어갔다고
#:   면책으로 세면 gold 가 오염되고, 그러면 recall 이 실제보다 낮게 나온다.
_EXCL_TITLE = re.compile(
    r"보상하지(?:않는|아니하는)(?:사항|손해)"
    r"|보험금을지급하지(?:않는|아니하는)(?:사유|보험사고)"
    r"|면책조건의내용"
)

#: ── 지표 ③ 페이지 해소 ───────────────────────────────────────────
#:
#: 조항 본문 앞 N글자가 `locator.page_from` 쪽 원문에 실제로 있는지 본다.
#: ★`citation`("제26조(회사의 손해배상책임)")으로 맞추면 안 된다 —
#:   그건 조립된 문자열이라 원문의 「제26조 **[**회사의 손해배상책임**]**」과 안 맞는다.
#:   실측: citation 기준 적중 83.97%, 본문 앞 30자 기준 **98.58%**.
#:   앞의 14%는 검색 결함이 아니라 **내 측정 도구의 결함**이었다.
#: ★N 을 늘리면 앵커가 쪽 경계를 넘어가 "검증불가"가 는다.
#:   실측(표본 25문서 2,4xx조항): N=30 → 적중 0.9858 / 검증불가 0.0142
#:                                N=40 → 0.9732 / 0.0268
#:                                N=60 → 0.9534 / 0.0466
#:   측정 도구가 만든 실패를 검색 실패로 세지 않으려고 **30** 을 쓴다.
HEAD_ANCHOR = 30
TAIL_ANCHOR = 30

#: 원문 쪽 텍스트와 조항 본문의 괄호 표기가 다르다(`[...]` ↔ `(...)`). 맞춰 준다.
_BRACKETS = str.maketrans(
    {
        "[": "(", "]": ")",
        "【": "(", "】": ")",
        "（": "(", "）": ")",
        "［": "(", "］": ")",
    }
)


def _sq(s: str) -> str:
    """공백을 전부 없앤다. 원문은 줄바꿈이 아무 데나 들어간다."""
    return re.sub(r"\s+", "", s or "")


def _npage(s: str) -> str:
    """쪽 텍스트 대조용 정규화 — 공백 제거 + 괄호 통일."""
    return _sq((s or "").translate(_BRACKETS))


def _is_exclusion_title(title: str) -> bool:
    return bool(_EXCL_TITLE.search(_sq(title)))


def _toc_signals(text: str) -> dict:
    """조항 본문의 목차 모양 신호. 판정 근거를 **숫자로 남긴다.**"""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < TOC_MIN_LINES:
        return {"lines": len(lines), "dot_ratio": 0.0, "pagenum_ratio": 0.0}
    dot = sum(1 for ln in lines if _TOC_DOT.search(ln))
    pn = sum(1 for ln in lines if _TOC_PAGENUM.match(ln))
    return {
        "lines": len(lines),
        "dot_ratio": round(dot / len(lines), 4),
        "pagenum_ratio": round(pn / len(lines), 4),
    }


def _toc_reasons(clause: dict, toc_pages: set[int]) -> list[str]:
    """이 조항을 목차형으로 볼 이유들. 비면 목차형이 아니다."""
    loc = clause.get("locator") or {}
    pf = loc.get("page_from") or 0
    out: list[str] = []
    if toc_pages and pf in toc_pages:
        out.append("A_시작쪽이_목차쪽")
    sig = _toc_signals(clause.get("text") or "")
    if sig["dot_ratio"] >= TOC_DOT_RATIO:
        out.append("B_점선줄")
    if sig["pagenum_ratio"] >= TOC_PAGENUM_RATIO:
        out.append("C_쪽번호줄")
    return out


#: ── 세대 고정 ────────────────────────────────────────────────────


def _release_for(tag: str):
    """평가용 릴리스 한 벌. `release.pinned()` 에 넣어 쓴다.

    ★`file_clause_store` 를 고쳐서 세대를 바꾸지 않는다. `release.pinned(rel)` 이
      **이미 제공되는 방법**이다(app/core/release.py). 평가가 서빙 코드를 건드리면
      평가한 것과 서빙되는 것이 달라진다.
    """
    from app.core import release

    base = release.load()
    n = sum(1 for _ in _STRUCT.glob(f"*/{tag}/*.clauses.json"))
    if not n:
        raise SystemExit(f"조항 산출물이 없습니다: data/structured/*/{tag}/")
    return release.AcceptedRelease(
        release_id=f"eval-{tag}",
        page_tag=base.page_tag,
        clause_tag=tag,
        #: 디스크에서 센 값을 그대로 넣는다 — `ensure_ready()` 가 이 값과 대조한다.
        document_count=n,
        embed_profile=base.embed_profile,
    )


def _default_tag() -> str:
    from app.core import release

    return release.load().clause_tag


#: ── 검증셋 생성 ──────────────────────────────────────────────────


def build(tag: str, doc_n: int, seed: int) -> int:
    files = sorted(_STRUCT.glob(f"*/{tag}/*.clauses.json"))
    if not files:
        print(f"조항 산출물이 없습니다: data/structured/*/{tag}/")
        return 2

    #: 보험사별로 고르게 뽑는다. 한 회사가 표본을 삼키면 그 회사 조판만 재게 된다.
    rng = random.Random(seed)
    by_insurer: dict[str, list[pathlib.Path]] = defaultdict(list)
    for p in files:
        by_insurer[p.parent.parent.name].append(p)
    per = max(1, doc_n // max(1, len(by_insurer)))
    picked: list[pathlib.Path] = []
    for ins in sorted(by_insurer):
        pool = sorted(by_insurer[ins])
        rng.shuffle(pool)
        picked.extend(pool[:per])
    rng.shuffle(picked)
    picked = picked[:doc_n]

    docs: list[dict] = []
    surface: Counter = Counter()
    #: (sha12, content_hash) → 목차형 사유
    toc_flags: dict[str, list[str]] = {}
    excl_by_doc: dict[str, list[dict]] = {}
    page_probes: list[dict] = []
    skipped_no_pages: list[str] = []
    #: ★목차 쪽을 **가로질러** 뻗은 조항. 목차 오염이 아니라 조 경계 실패다.
    #:   인용에 `page_from~page_to` 를 그대로 붙이면 사람이 목차 쪽을 펴 보게 된다.
    span_across_toc: list[dict] = []

    for p in picked:
        d = json.loads(p.read_text(encoding="utf-8"))
        insurer_slug = p.parent.parent.name
        sha12 = p.name.split(".")[0]
        src = d.get("source") or {}
        sha256 = src.get("sha256") or ""
        toc_pages = set(d.get("toc_pages") or [])
        clauses = d.get("clauses") or []

        #: 원문 쪽 텍스트가 있어야 지표 ③ 을 잰다. 없으면 **뺀 사실을 남긴다.**
        #: ★조용히 건너뛰면 분모가 줄어 정확도가 실제보다 좋아 보인다(CLAUDE.md §3).
        page_dirs = sorted((_EXTRACTED / insurer_slug).glob(f"s*/{sha12}.json"))
        if not page_dirs:
            skipped_no_pages.append(f"{insurer_slug}/{sha12}")

        excls: list[dict] = []
        for c in clauses:
            ch = c.get("content_hash") or ""
            reasons = _toc_reasons(c, toc_pages)
            if reasons and ch:
                toc_flags[f"{sha12}:{ch}"] = reasons
            loc = c.get("locator") or {}
            pf, pt = loc.get("page_from") or 0, loc.get("page_to") or 0
            if toc_pages and pf not in toc_pages and any(pf < t <= pt for t in toc_pages):
                span_across_toc.append(
                    {
                        "sha12": sha12,
                        "qualified_no": c.get("qualified_no") or "",
                        "page_from": pf,
                        "page_to": pt,
                        "toc_pages_crossed": sorted(t for t in toc_pages if pf < t <= pt),
                    }
                )
            if _is_exclusion_title(c.get("title") or ""):
                excls.append(
                    {
                        "content_hash": ch,
                        "qualified_no": c.get("qualified_no") or "",
                        "title": (c.get("title") or "").strip(),
                        #: ★인용 게이트에 걸린 면책 조항은 **검색해도 못 나온다.**
                        #:   recall 의 천장이 여기서 정해진다. 따로 세어 둔다.
                        "citation_eligible": c.get("citation_eligible"),
                    }
                )
                surface[(c.get("title") or "").strip()] += 1

        #: 페이지 해소 검사 대상 — 조항을 고르게 훑는다(앞쪽만 보면 편향된다).
        step = max(1, len(clauses) // PAGE_PROBE_PER_DOC)
        for c in clauses[::step][:PAGE_PROBE_PER_DOC]:
            body = _npage(c.get("text") or "")
            loc = c.get("locator") or {}
            if len(body) < HEAD_ANCHOR + TAIL_ANCHOR:
                continue
            page_probes.append(
                {
                    "sha12": sha12,
                    "insurer_slug": insurer_slug,
                    "qualified_no": c.get("qualified_no") or "",
                    "content_hash": c.get("content_hash") or "",
                    "page_from": loc.get("page_from") or 0,
                    "page_to": loc.get("page_to") or 0,
                    "head_anchor": body[:HEAD_ANCHOR],
                    "tail_anchor": body[-TAIL_ANCHOR:],
                }
            )

        excl_by_doc[sha12] = excls
        docs.append(
            {
                "sha12": sha12,
                "sha256": sha256,
                "insurer_slug": insurer_slug,
                "insurer": src.get("insurer") or "",
                "parse_status": d.get("parse_status") or "unknown",
                "pages": (d.get("stats") or {}).get("pages") or 0,
                "clause_count": len(clauses),
                "toc_pages": sorted(toc_pages),
                "has_page_layer": bool(page_dirs),
                "exclusion_clause_count": len(excls),
            }
        )

    #: ── 면책 질의 ────────────────────────────────────────────────
    #:
    #: ★두 종류를 만든다. 하나만 쓰면 낱말 검색이 유리하게 나온다.
    #:   `동일표현` — 제 문서에 적힌 제목 그대로. 이게 **못 맞히면 그건 고장**이다.
    #:   `타사표현` — 다른 문서에 적힌 다른 표기. 그 문서 본문에 **글자로 없는 것**만 고른다.
    forms = [f for f, _ in surface.most_common()]
    queries: list[dict] = []
    for doc in docs:
        sha12 = doc["sha12"]
        golds = excl_by_doc.get(sha12) or []
        gold_ids = sorted({g["content_hash"] for g in golds if g["content_hash"]})
        if not gold_ids:
            continue
        own = Counter(g["title"] for g in golds).most_common(1)[0][0]
        body_all = _sq(
            " ".join(
                (c.get("text") or "")
                for c in json.loads(
                    (_STRUCT / doc["insurer_slug"] / tag / f"{sha12}.clauses.json").read_text(
                        encoding="utf-8"
                    )
                ).get("clauses", [])
            )
        )
        queries.append(
            {
                "probe_id": f"{sha12}:own",
                "sha12": sha12,
                "sha256": doc["sha256"],
                "kind": "동일표현",
                "query": own,
                "query_from": sha12,
                "gold_ids": gold_ids,
                "gold_eligible_ids": sorted(
                    {g["content_hash"] for g in golds if g.get("citation_eligible") is not False}
                ),
            }
        )
        #: 이 문서 본문에 **글자 그대로 없는** 표기를 고른다.
        alt = next((f for f in forms if _sq(f) and _sq(f) not in body_all), "")
        if alt:
            queries.append(
                {
                    "probe_id": f"{sha12}:alt",
                    "sha12": sha12,
                    "sha256": doc["sha256"],
                    "kind": "타사표현",
                    "query": alt,
                    "query_from": "다른 문서 제목 원문",
                    "gold_ids": gold_ids,
                    "gold_eligible_ids": sorted(
                        {
                            g["content_hash"]
                            for g in golds
                            if g.get("citation_eligible") is not False
                        }
                    ),
                }
            )

    out = {
        "built_at": "2026-08-03",
        "built_from_tag": tag,
        "seed": seed,
        "document_count": len(docs),
        "document_universe": len(files),
        "exclusion_query_count": len(queries),
        "page_probe_count": len(page_probes),
        "toc_flagged_count": len(toc_flags),
        "skipped_no_page_layer": skipped_no_pages,
        "span_across_toc_count": len(span_across_toc),
        "toc_rule": {
            "A_시작쪽이_목차쪽": "locator.page_from 이 문서 toc_pages 안에 있다",
            "B_점선줄": f"점선 리더 줄 비율 >= {TOC_DOT_RATIO} (줄 {TOC_MIN_LINES}개 이상)",
            "C_쪽번호줄": f"끝이 숫자인 줄 비율 >= {TOC_PAGENUM_RATIO}",
            "★한계": (
                "이 규칙의 precision 은 표본 7건 전수확인으로 7/7 이었으나 "
                "rule of three 상 오탐률 95% 상한은 3/7=43% 다. "
                "recall 은 목차 정답셋이 없어 **못 쟀다**."
            ),
        },
        "exclusion_title_forms": [{"title": t, "count": n} for t, n in surface.most_common()],
        "note": [
            "질의는 문서에 이미 적힌 조항 제목 원문이다. 지어내지 않았다.",
            "★동일표현 질의는 gold 본문에 질의 문자열이 그대로 들어 있다. "
            "낱말 포함 검색에 유리하므로 **품질 추정치가 아니라 하한 점검**으로 읽는다.",
            "★타사표현 질의는 그 문서 본문에 글자로 없는 표기만 골랐다. 이쪽이 진짜 시험이다.",
            f"★표본이 작다 — 문서 {len(docs)} / 전체 {len(files)}.",
            "gold 는 조항 제목이 면책 표기와 맞는 조항 전부다. gold 수가 k 보다 크면 "
            "recall@k 는 정의상 1.0 에 못 닿는다. hit@k 를 함께 본다.",
        ],
        "documents": docs,
        "exclusion_queries": queries,
        "toc_flagged": toc_flags,
        "span_across_toc": span_across_toc,
        "page_probes": page_probes,
    }
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")

    print(
        f"문서 {len(docs)} (전체 {len(files)}) · 면책질의 {len(queries)} "
        f"(동일 {sum(1 for q in queries if q['kind'] == '동일표현')} / "
        f"타사 {sum(1 for q in queries if q['kind'] == '타사표현')}) · "
        f"페이지탐침 {len(page_probes)} · 목차형표시 {len(toc_flags)}"
    )
    if skipped_no_pages:
        print(f"★원문 쪽 텍스트 없음 {len(skipped_no_pages)}건 — 지표③ 분모에서 빠진다")
    print(f"→ {_OUT.relative_to(_ROOT)}")
    return 0


#: ── 채점 ─────────────────────────────────────────────────────────


def _rule_of_three(n: int) -> float:
    """실패 0건일 때 95% 상한. `3/n`."""
    return 3.0 / n if n else float("nan")


def _score_retrieval(probes: dict, tag: str, ks: list[int]) -> dict:
    """지표 ①·② — 검색기를 실제로 돌린다."""
    from app.core import release
    from app.adapters import file_clause_store as fcs

    kmax = max(ks)
    toc_flagged = probes["toc_flagged"]
    rows: list[dict] = []
    failed: list[str] = []

    with release.pinned(_release_for(tag)):
        for q in probes["exclusion_queries"]:
            try:
                hits = fcs.search(q["sha256"], q["query"], limit=kmax)
            except Exception as exc:  # noqa: BLE001
                #: ★조용히 넘기지 않는다. 실패는 세어서 보고한다(CLAUDE.md §3).
                failed.append(f"{q['probe_id']}: {exc}")
                continue
            rows.append(
                {
                    "kind": q["kind"],
                    "gold": set(q["gold_ids"]),
                    "gold_eligible": set(q["gold_eligible_ids"]),
                    "hits": [h.content_hash for h in hits],
                    "toc": [
                        bool(toc_flagged.get(f"{q['sha12']}:{h.content_hash}")) for h in hits
                    ],
                }
            )

    def agg(sel) -> dict:
        sub = [r for r in rows if sel(r)]
        out: dict = {"질의수": len(sub)}
        if not sub:
            return out
        for k in ks:
            #: recall@k = |top-k ∩ gold| / |gold| — 질의별로 내고 평균한다(macro).
            rec = [len(set(r["hits"][:k]) & r["gold"]) / len(r["gold"]) for r in sub if r["gold"]]
            #: hit@k = top-k 안에 gold 가 하나라도 있는 질의 비율. **recall 과 다른 지표다.**
            hit = [1.0 if set(r["hits"][:k]) & r["gold"] else 0.0 for r in sub if r["gold"]]
            #: 인용 게이트를 통과한 gold 만으로 다시 — 검색기가 도달 가능한 천장.
            rec_e = [
                len(set(r["hits"][:k]) & r["gold_eligible"]) / len(r["gold_eligible"])
                for r in sub
                if r["gold_eligible"]
            ]
            n_item = sum(min(k, len(r["hits"])) for r in sub)
            n_toc = sum(sum(r["toc"][:k]) for r in sub)
            q_toc = sum(1 for r in sub if any(r["toc"][:k]))
            out[f"k={k}"] = {
                "면책_recall@k": round(sum(rec) / len(rec), 4) if rec else None,
                "면책_hit@k": round(sum(hit) / len(hit), 4) if hit else None,
                "면책_recall@k_인용가능gold만": round(sum(rec_e) / len(rec_e), 4)
                if rec_e
                else None,
                "목차형_contamination@k_항목기준": round(n_toc / n_item, 4) if n_item else None,
                "목차형_contamination@k_질의기준": round(q_toc / len(sub), 4),
                "_검색결과_항목수": n_item,
                "_목차형_항목수": n_toc,
                #: ★0건이면 "없다"가 아니라 "이 표본으로는 못 봤다"이다. rule of three.
                "★오염0건일때_95%상한": round(_rule_of_three(n_item), 5)
                if n_item and n_toc == 0
                else None,
            }
        return out

    #: ★코퍼스에 목차형이 애초에 몇 개나 있는지. 이걸 안 적으면 contamination 0 을
    #:   "검색기가 잘한다"로 읽는다. 실제로는 **표적이 거의 없어서** 0 일 수 있다.
    n_clause = sum(d["clause_count"] for d in probes["documents"])
    return {
        "코퍼스_목차형_사전비율": {
            "목차형": len(toc_flagged),
            "조항총수": n_clause,
            "비율": round(len(toc_flagged) / n_clause, 6) if n_clause else None,
            "★": "표적이 희소하면 contamination@k 는 낮게 나올 수밖에 없다. 검정력이 약하다.",
        },
        "전체": agg(lambda r: True),
        "동일표현": agg(lambda r: r["kind"] == "동일표현"),
        "타사표현": agg(lambda r: r["kind"] == "타사표현"),
        "검색실패": failed,
        "gold_크기_분포": dict(Counter(len(r["gold"]) for r in rows)),
    }


def _score_pages(probes: dict, page_tag: str) -> dict:
    """지표 ③ — 인용에 붙은 쪽수가 원문 위치와 맞는가.

    ★검색기가 없어도 **지금 잴 수 있다.** 조항 본문 앞 30자가 `page_from` 쪽
      원문에 실제로 있는지 보면 된다.
    ★세 갈래로 나눈다 — 적중 / 불일치 / **검증불가.**
      "검증불가"를 불일치에 섞으면 없는 결함을 있다고 말하게 된다.
    """
    cache: dict[str, dict[int, str]] = {}
    n = hit_f = wrong_f = unver_f = hit_t = 0
    off_by = Counter()
    missing_pagefile: set[str] = set()

    for pr in probes["page_probes"]:
        key = f"{pr['insurer_slug']}/{pr['sha12']}"
        if key not in cache:
            f = _EXTRACTED / pr["insurer_slug"] / page_tag / f"{pr['sha12']}.json"
            if not f.exists():
                cache[key] = {}
                missing_pagefile.add(key)
            else:
                ex = json.loads(f.read_text(encoding="utf-8"))
                cache[key] = {p["page"]: _npage(p.get("text") or "") for p in ex.get("pages", [])}
        pages = cache[key]
        if not pages:
            continue
        n += 1
        pf, pt = pr["page_from"], pr["page_to"]
        head, tail = pr["head_anchor"], pr["tail_anchor"]
        if pf in pages and head in pages[pf]:
            hit_f += 1
        else:
            where = [pg for pg, t in pages.items() if head in t]
            if where:
                wrong_f += 1
                off_by[min((w - pf for w in where), key=abs)] += 1
            else:
                #: 앵커가 쪽 경계를 넘어갔거나 원문 재현이 다르다 → **못 쟀다.**
                unver_f += 1
        if pt in pages and tail in pages[pt]:
            hit_t += 1

    #: ★목차 쪽을 가로지르는 조항 — 인용 쪽 범위가 사람을 목차로 보낸다.
    #:   목차 오염 지표(①)가 아니라 **쪽 해소 신뢰성** 문제라 여기서 센다.
    span = probes.get("span_across_toc") or []
    span_width = Counter(s["page_to"] - s["page_from"] for s in span)

    return {
        "page_tag": page_tag,
        "탐침수": n,
        "쪽파일없음_문서": sorted(missing_pagefile),
        "목차쪽_가로지르는_조항": {
            "건수": len(span),
            "문서수": len({s["sha12"] for s in span}),
            "쪽폭_분포": dict(sorted(span_width.items())),
            "★뜻": "조 경계 실패로 보인다. 인용에 이 범위를 그대로 쓰면 목차 쪽이 근거로 붙는다.",
        },
        "page_from_적중": round(hit_f / n, 4) if n else None,
        "page_from_불일치": round(wrong_f / n, 4) if n else None,
        "page_from_검증불가": round(unver_f / n, 4) if n else None,
        "page_to_적중": round(hit_t / n, 4) if n else None,
        "불일치_쪽차이_분포": dict(sorted(off_by.items())),
        "★불일치0건일때_95%상한": round(_rule_of_three(n), 5) if n and wrong_f == 0 else None,
    }


def score(tag: str, page_tag: str, ks: list[int]) -> int:
    if not _OUT.exists():
        print(f"검증셋이 없습니다: {_OUT}. 먼저 `build` 를 돌리세요.")
        return 2
    probes = json.loads(_OUT.read_text(encoding="utf-8"))
    #: ★세대가 다르면 gold(content_hash)가 안 맞는다. 조용히 섞지 않고 **멈춘다.**
    if probes.get("built_from_tag") != tag:
        print(
            f"검증셋은 {probes.get('built_from_tag')} 로 만들었는데 채점은 {tag} 입니다.\n"
            "★gold 가 세대별 content_hash 라 섞으면 recall 이 0 으로 나옵니다. "
            "같은 tag 로 다시 build 하거나 --tag 를 맞추세요."
        )
        return 2

    result = {
        "검증셋": {
            "tag": probes["built_from_tag"],
            "문서": probes["document_count"],
            "전체문서": probes["document_universe"],
            "면책질의": probes["exclusion_query_count"],
            "페이지탐침": probes["page_probe_count"],
        },
        "baseline_검색기": "app/adapters/file_clause_store.search (낱말 포함)",
        "★주의": [
            "이 점수는 **임베딩 검색의 점수가 아니다.** 승인 릴리스에 임베딩 프로필이 없다.",
            "동일표현 질의는 gold 본문에 질의가 그대로 들어 있어 낱말 검색에 유리하다.",
        ],
        "지표①②_검색": _score_retrieval(probes, tag, ks),
        "지표③_페이지해소": _score_pages(probes, page_tag),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="검색 품질 검증셋 — 목차오염·면책recall·페이지해소")
    ap.add_argument("cmd", choices=["build", "score"])
    #: ★기본값을 코드에 박지 않는다. 승인 릴리스(config/accepted_extraction.json)에서 읽는다.
    ap.add_argument("--tag", default="", help="조항 산출 세대. 비우면 승인 릴리스의 clause_tag")
    ap.add_argument("--page-tag", default="", help="원문 쪽 텍스트 세대. 비우면 승인 릴리스의 page_tag")
    ap.add_argument("--docs", type=int, default=DOC_N)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--k", default="1,3,5,10")
    a = ap.parse_args()

    from app.core import release

    rel = release.load()
    tag = a.tag or rel.clause_tag
    if a.cmd == "build":
        return build(tag, a.docs, a.seed)
    page_tag = a.page_tag or rel.page_tag
    ks = sorted({int(x) for x in a.k.split(",") if x.strip()})
    return score(tag, page_tag, ks)


if __name__ == "__main__":
    sys.exit(main())
