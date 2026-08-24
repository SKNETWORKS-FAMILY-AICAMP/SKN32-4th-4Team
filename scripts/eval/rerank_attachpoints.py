"""리랭커가 **붙는 자리를 전부** 같은 후보셋으로 잰다.

    python -m scripts.eval.rerank_attachpoints --list
    python -m scripts.eval.rerank_attachpoints --point clause --model Qwen/Qwen3-Reranker-4B

★왜 새로 만드나 — 기존 `run_s6_reranker.py` 는 채점 루프를 **따로** 갖고 있다.
  그걸로 고른 모델이 서비스에서도 같게 도는지는 보장되지 않는다.
  여기서는 **배포에 실제로 쓰이는 어댑터를 그대로 부른다** —
  `CrossEncoderReranker` · `rerank_hits` · `RerankedRetriever`.
  측정한 것과 배포하는 것이 다르면 그 측정은 근거가 아니다(CLAUDE.md §4).

★붙는 자리(attach point)
    clause   조항 검색  `clause_rerank.rerank_hits(ClauseHit …)`   ← 보험 경로
    rag      커머스 RAG `RerankedRetriever(Evidence …)`            ← /api/rag
  둘은 **자료형이 다르다**(ClauseHit vs Evidence). 한쪽만 재고 "리랭커를 쟀다"고
  말하면 다른 쪽 배선이 깨져도 모른다. 그래서 같은 후보셋으로 둘 다 돌린다.

★DB 를 쓰지 않는다. 후보는 이미 뽑아 둔 파일(`data/eval/*_top20_rerank.json`)을 쓴다.
  GPU 기계에 PG 를 옮기지 않으려는 것이고, 후보가 고정돼야 모델 간 비교가 공정하다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

POINTS = ("clause", "rag")


def _load(path: pathlib.Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    if not d.get("records"):
        raise SystemExit(f"★후보셋에 records 가 없다: {path}")
    return d


def _body(c: dict, field: str) -> str:
    """채점에 넣을 본문을 고른다.

    ★`full_text`(조 전체)와 `text`(조각)는 **다른 것을 재게 된다.**
      서비스는 조 전체를 채점하는데(`citable_text`), 모델 선정 벤치마크는 조각을 썼다.
      실측 2026-08-04: 같은 4B 로 조 전체 hit@1 0.5851 ↔ 조각 0.6379.
      어느 쪽이 맞는지는 실험으로 가린다 — 그래서 **바꿔 끼울 수 있게** 둔다.
    """
    if field == "text":
        return c.get("text") or ""
    return c.get("full_text") or c.get("text") or ""


def _hits_from(rec: dict, field: str = "full_text"):
    """후보 → `ClauseHit`. 서비스가 받는 것과 같은 자료형으로 만든다."""
    from app.adapters.pgvector_clause_index import ClauseHit

    out = []
    for i, c in enumerate(rec["candidates"]):
        text = _body(c, field)
        out.append(ClauseHit(
            content_hash=c["content_hash"],
            chunk_ix=int(c.get("seq") or 0),
            text=text,
            distance=float(c.get("distance") or 0.0),
            sha256=c.get("sha256") or f"unknown-{i}",
            insurer=c.get("insurer") or "",
            qualified_no=c.get("qualified_no") or "",
            section=c.get("section") or "",
            title=c.get("title") or "",
            page_from=int(c.get("page_from") or 0),
            page_to=int(c.get("page_to") or 0),
            #: ★조 전체를 쓴다. 조각만 채점하면 예외가 뒤에 오는 법률문의 뜻이 뒤집힌다.
            full_text=text,
        ))
    return out


def _evidence_from(rec: dict, field: str = "full_text"):
    from app.application.ports import Evidence

    return [
        Evidence(content=_body(c, field),
                 source=c.get("sha256") or "?", locator=c["content_hash"],
                 score=0.0, backend="clause_index")
        for c in rec["candidates"]
    ]


def _gold(rec: dict) -> set[str]:
    #: 정답은 12자 접두사로 적혀 있다(`gold_ids`). 후보의 전체 해시와 접두사로 맞춘다.
    return {g[:16] for g in (rec.get("gold_ids") or [])}


def _rank_of_gold(order: list[str], gold: set[str]) -> int | None:
    for i, h in enumerate(order, 1):
        if h[:16] in gold:
            return i
    return None


def _metrics(ranks: list[int | None], n: int) -> dict:
    def hit(k): return sum(1 for r in ranks if r is not None and r <= k) / n
    mrr = sum(1.0 / r for r in ranks if r is not None) / n
    return {"n": n, "hit@1": round(hit(1), 6), "hit@5": round(hit(5), 6),
            "hit@10": round(hit(10), 6), "hit@20": round(hit(20), 6),
            "mrr@10": round(sum(1.0 / r for r in ranks if r is not None and r <= 10) / n, 6),
            "mrr": round(mrr, 6)}


def run(point: str, data: dict, reranker, limit: int | None, field: str = "full_text") -> dict:
    records = data["records"][:limit] if limit else data["records"]
    dense_ranks, rr_ranks, lat = [], [], []
    failures: list[dict] = []

    from app.adapters.clause_rerank import rerank_hits

    for rec in records:
        gold = _gold(rec)
        order_dense = [c["content_hash"] for c in rec["candidates"]]
        dense_ranks.append(_rank_of_gold(order_dense, gold))

        t0 = time.perf_counter()
        try:
            if point == "clause":
                out = rerank_hits(reranker, rec["query"], _hits_from(rec, field))
                order = [h.content_hash for h in out]
            else:
                out = reranker.rerank(rec["query"], _evidence_from(rec, field))
                order = [e.locator for e in out]
        except Exception as exc:  # noqa: BLE001 — 실패를 세어 보고한다(조용한 스킵 금지)
            failures.append({"query_id": rec.get("query_id"),
                             "error": f"{type(exc).__name__}: {exc}"[:200]})
            rr_ranks.append(None)
            continue
        lat.append((time.perf_counter() - t0) * 1000)
        rr_ranks.append(_rank_of_gold(order, gold))

    n = len(records)
    #: ★질의별 순위를 남긴다. 총합만 남기면 **유형별로 쪼갤 수 없다.**
    #:   면책·예외 질의에서 무너지는데 전체 평균은 좋을 수 있고, 이 서비스에서
    #:   그건 안전 문제다(코덱스 지적 2026-08-05). 앞서 총합만 저장했다가
    #:   다시 돌려야 했다.
    per_query = [
        {"query_id": rec.get("query_id"), "task": rec.get("task"),
         "is_exclusion": bool(rec.get("is_exclusion")),
         "scope_mode": rec.get("scope_mode"),
         "gold_in_candidates": dr is not None,
         "dense_rank": dr, "reranked_rank": rr}
        for rec, dr, rr in zip(records, dense_ranks, rr_ranks, strict=True)
    ]

    def _slice(name, keep) -> dict:
        idx = [i for i, q in enumerate(per_query) if keep(q)]
        if not idx:
            return {}
        return {name: {"n": len(idx),
                       "dense": _metrics([dense_ranks[i] for i in idx], len(idx)),
                       "reranked": _metrics([rr_ranks[i] for i in idx], len(idx))}}

    slices: dict = {}
    slices.update(_slice("exclusion", lambda q: q["is_exclusion"]))
    slices.update(_slice("non_exclusion", lambda q: not q["is_exclusion"]))
    slices.update(_slice("retrievable", lambda q: q["gold_in_candidates"]))
    for t in sorted({q["task"] for q in per_query if q["task"]}):
        slices.update(_slice(f"task:{t}", lambda q, t=t: q["task"] == t))

    return {
        "attach_point": point,
        "score_field": field,
        "n_queries": n,
        "slices": slices,
        "per_query": per_query,
        "dense": _metrics(dense_ranks, n),
        "reranked": _metrics(rr_ranks, n),
        #: ★실패를 감추지 않는다. 실패가 있으면 분모는 그대로 두고 순위를 None 으로 센다 —
        #:   실패한 질의를 빼면 지표가 좋아 보인다.
        "failures": len(failures),
        "failure_examples": failures[:5],
        "latency_ms": ({"p50": round(statistics.median(lat), 1),
                        "p95": round(sorted(lat)[int(len(lat) * 0.95)], 1) if len(lat) > 1 else None,
                        "mean": round(statistics.fmean(lat), 1)} if lat else None),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=pathlib.Path,
                    default=ROOT / "data/eval/s7_1_arctic_ko_top20_rerank.json")
    ap.add_argument("--point", choices=(*POINTS, "all"), default="all")
    ap.add_argument("--model", default="Qwen/Qwen3-Reranker-4B")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--dtype", default="float16")
    ap.add_argument("--max-length", type=int, default=768)
    ap.add_argument("--batch-size", type=int, default=1)
    ap.add_argument("--trust-remote-code", action="store_true")
    ap.add_argument("--score-field", choices=("full_text", "text"), default="full_text",
                    help="채점에 넣을 본문. full_text=조 전체(서비스와 같음) · text=조각(옛 벤치마크)")
    ap.add_argument("--limit", type=int, default=None, help="질의 수 제한(연기 테스트용)")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        print("붙는 자리:")
        print("  clause   조항 검색  rerank_hits(ClauseHit)     — 보험 경로(/api/admin/clause-search)")
        print("  rag      커머스 RAG CrossEncoderReranker(Evidence) — /api/rag/*")
        return 0

    data = _load(a.candidates)
    from app.adapters.reranker import CrossEncoderReranker

    reranker = CrossEncoderReranker(
        a.model, device=a.device, batch_size=a.batch_size,
        max_length=a.max_length, dtype=a.dtype, trust_remote_code=a.trust_remote_code,
    )

    #: ★첫 질의에 무게추 로딩이 섞이면 p95 가 그 한 건에 지배된다(실측: p50 213ms ↔ p95 75초).
    #:   예열을 따로 재서 **로딩 시간과 질의 시간을 나눈다.**
    t0 = time.perf_counter()
    from app.application.ports import Evidence as _Ev

    reranker.rerank("예열", [_Ev(content="예열용 본문", source="w", locator="w1", score=0.0, backend="w"),
                             _Ev(content="다른 본문", source="w", locator="w2", score=0.0, backend="w")])
    warmup_s = round(time.perf_counter() - t0, 1)
    print(f"  모델 적재·예열 {warmup_s}초", flush=True)

    points = POINTS if a.point == "all" else (a.point,)
    report = {
        "schema_version": "rerank-attachpoints-v1",
        "candidates": str(a.candidates.name),
        "candidate_queries": data.get("queries"),
        "retriever_model": data.get("retriever_model"),
        "query_prefix": data.get("query_prefix"),
        "reranker_model": a.model,
        #: 무게추 적재는 질의 지연과 **다른 비용**이다. 섞어 적으면 SLA 판단이 틀린다.
        "warmup_seconds": warmup_s,
        "settings": {"device": a.device, "dtype": a.dtype,
                     "max_length": a.max_length, "batch_size": a.batch_size},
        "results": [],
    }
    for p in points:
        print(f"\n── {p} 측정 중 (모델 {a.model} · 본문 {a.score_field})", flush=True)
        r = run(p, data, reranker, a.limit, a.score_field)
        report["results"].append(r)
        d, k = r["dense"], r["reranked"]
        print(f"   n={r['n_queries']} 실패={r['failures']}")
        print(f"   dense    hit@1={d['hit@1']:.4f} hit@5={d['hit@5']:.4f} mrr@10={d['mrr@10']:.4f}")
        print(f"   reranked hit@1={k['hit@1']:.4f} hit@5={k['hit@5']:.4f} mrr@10={k['mrr@10']:.4f}")
        print(f"   Δhit@1  {k['hit@1'] - d['hit@1']:+.4f}")
        if r["latency_ms"]:
            print(f"   지연(질의당) p50={r['latency_ms']['p50']}ms p95={r['latency_ms']['p95']}ms")
        for f in r["failure_examples"]:
            print(f"   ★실패 {f['query_id']}: {f['error']}")

    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n기록 → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
