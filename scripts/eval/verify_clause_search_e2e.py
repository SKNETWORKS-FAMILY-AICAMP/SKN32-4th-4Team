"""조항 의미검색이 **실제로 도는지** 끝에서 끝까지 확인한다.

    python -m scripts.eval.verify_clause_search_e2e            # 리랭킹 끄고
    python -m scripts.eval.verify_clause_search_e2e --rerank   # 켜고(GPU 권장)

★단위 테스트는 어댑터를 가짜로 바꾼다. 이 스크립트는 **진짜를 쓴다** —
  진짜 PG · 진짜 임베더 · (선택) 진짜 리랭커 · 진짜 라우터.
  「테스트가 통과한다」와 「서비스가 돈다」는 다른 말이다(CLAUDE.md §4).

★관리자 인증은 `dependency_overrides` 로 통과시킨다. 비밀번호를 넣지 않는다 —
  검증하려는 것은 **경로가 도는가**이지 로그인 폼이 아니다.
  다만 **경로가 고객앱에 없는지**는 여기서 함께 확인한다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 문서화된 실패 사례 — 벡터만으로는 「보상하지 않는 사항」이 올라온다(거리 0.941).
QUERY = "치과치료 보철료는 보상하나요"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerank", action="store_true")
    ap.add_argument("--query", default=QUERY)
    ap.add_argument("--final-k", type=int, default=5)
    ap.add_argument("--out", type=pathlib.Path, default=None)
    #: ★GPU 기계에는 커머스 RAG 스택(langchain 등)이 없다. 그걸 깔자고
    #:   레거시 의존성을 GPU 기계에 끌어오지 않는다. HTTP 계층은
    #:   `tests/test_clause_search_route.py` 가 따로 덮는다.
    ap.add_argument("--usecase-only", action="store_true",
                    help="FastAPI 앱을 띄우지 않고 유스케이스를 직접 부른다")
    a = ap.parse_args()

    from app.core.config import get_settings

    http_ok = not a.usecase_only
    if http_ok:
        try:
            from fastapi.testclient import TestClient  # noqa: F401

            from app.auth.roles import require_admin  # noqa: F401
            from app.main import create_app  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            #: ★조용히 건너뛰지 않는다. 무엇을 못 봤는지 보고서에 남긴다.
            print(f"  ※ HTTP 계층을 띄우지 못했다 — {type(exc).__name__}: {str(exc)[:90]}")
            print("     유스케이스 계층으로 내려가 검증한다(라우터는 별도 테스트가 덮는다).")
            http_ok = False

    st = get_settings()
    report: dict = {
        "schema_version": "clause-search-e2e-v1",
        "query": a.query,
        "rerank_requested": a.rerank,
        "settings": {
            "INSURANCE_CLAUSE_RERANK_ENABLED": st.INSURANCE_CLAUSE_RERANK_ENABLED,
            "CLAUSE_RERANK_SCORE_BODY": st.CLAUSE_RERANK_SCORE_BODY,
            "CLAUSE_RERANK_MAX_LENGTH": st.CLAUSE_RERANK_MAX_LENGTH,
            "RERANKER_MODEL": st.RERANKER_MODEL,
        },
        "checks": [],
        "http_layer_exercised": http_ok,
    }

    def check(name: str, ok: bool, detail: str = "") -> None:
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"  {'OK ' if ok else '★실패'} {name}" + (f" — {detail}" if detail else ""))

    # ── 1. 고객앱에 경로가 없어야 한다 ──────────────────────────────
    if http_ok:
        from fastapi.testclient import TestClient
        from app.main import create_app

        r = TestClient(create_app("customer")).post(
            "/api/admin/clause-search", json={"query": a.query})
        check("고객앱(8080)에 경로 없음", r.status_code == 404, f"HTTP {r.status_code}")
    else:
        report["checks"].append({"name": "고객앱에 경로 없음", "ok": None,
                                 "detail": "HTTP 계층 미기동 — 라우터 테스트가 덮는다"})
        print("  --  고객앱 경로 검사는 건너뜀(라우터 테스트가 덮는다)")

    # ── 2. 승인 릴리스 프로필이 완전한가 ────────────────────────────
    from app.adapters import clause_query_embedder

    try:
        emb = clause_query_embedder.build()
        check("질의 임베더 생성", True, emb.profile_key)
    except Exception as exc:  # noqa: BLE001
        check("질의 임베더 생성", False, f"{type(exc).__name__}: {exc}")
        return 1

    # ── 3. 색인이 준비됐는가 ───────────────────────────────────────
    from app.adapters import pgvector_clause_index as ix

    check("색인 세대·모델 확인", bool(ix.current_generation()),
          f"{ix.current_generation()} / {ix.current_embed_model()[:40]}")

    # ── 4. 실제 검색 ──────────────────────────────────────────────
    reranker = None
    if a.rerank:
        from app.adapters.reranker import CrossEncoderReranker

        reranker = CrossEncoderReranker(
            st.RERANKER_MODEL, device=st.RERANKER_DEVICE,
            batch_size=st.RERANKER_BATCH_SIZE, max_length=st.CLAUSE_RERANK_MAX_LENGTH,
            dtype=st.RERANKER_DTYPE, trust_remote_code=st.RERANKER_TRUST_REMOTE_CODE)

    t0 = time.perf_counter()
    if http_ok:
        from fastapi.testclient import TestClient
        from app.auth.roles import require_admin
        from app.main import create_app

        app = create_app("admin")
        app.dependency_overrides[require_admin] = lambda: {"username": "e2e", "role": "ADMIN"}
        res = TestClient(app).post("/api/admin/clause-search", json={
            "query": a.query, "allow_global": True, "final_k": a.final_k, "rerank": a.rerank})
        app.dependency_overrides.clear()
        elapsed = round(time.perf_counter() - t0, 1)
        report["http_status"] = res.status_code
        report["elapsed_seconds"] = elapsed
        if res.status_code != 200:
            check(f"검색 요청(rerank={a.rerank})", False,
                  f"HTTP {res.status_code}: {str(res.json())[:200]}")
            report["body"] = res.json()
            if a.out:
                a.out.parent.mkdir(parents=True, exist_ok=True)
                a.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            return 1
        d = res.json()
    else:
        from app.adapters.pgvector_index import get_conn
        from app.composition import build_clause_search_deps
        from app.core.usecases import clause_search

        with get_conn() as conn:
            r = clause_search.search(
                **build_clause_search_deps(), conn=conn, embedder=emb, query=a.query,
                scope_sha256s=None, allow_global=True, final_k=a.final_k,
                reranker=reranker, max_candidates=st.CLAUSE_RERANK_MAX_CANDIDATES,
                score_body=st.CLAUSE_RERANK_SCORE_BODY,
                score_chars=st.CLAUSE_RERANK_SCORE_CHARS)
        elapsed = round(time.perf_counter() - t0, 1)
        report["elapsed_seconds"] = elapsed
        d = {"reranked": r.reranked, "provenance": r.provenance,
             "dropped_incomplete": r.dropped_incomplete,
             "dropped_unscorable": r.dropped_unscorable,
             "hits": [{"clause_id": h.clause_id, "insurer": h.insurer, "section": h.section,
                       "qualified_no": h.qualified_no, "title": h.title,
                       "page_from": h.page_from, "page_to": h.page_to,
                       "distance": h.distance, "sha256": h.sha256} for h in r.hits]}
    report["body"] = {k: v for k, v in d.items() if k != "hits"}
    report["hits"] = d["hits"]
    check(f"검색 요청(rerank={a.rerank})", True, f"HTTP 200 · {elapsed}초")
    check("근거 후보를 찾음", len(d["hits"]) > 0, f"{len(d['hits'])}건")
    check("재정렬 여부가 요청과 일치", d["reranked"] is a.rerank, f"reranked={d['reranked']}")
    check("어느 색인으로 찾았는지 남김", "index_generation" in (d.get("provenance") or {}),
          str(d.get("provenance", {}).get("index_generation")))
    check("판정처럼 읽히는 필드 없음",
          not ({"verdict", "covered", "abstained"} & set(d)))
    if a.rerank:
        check("채점 본문이 설정대로",
              d["provenance"].get("rerank_score_body") == st.CLAUSE_RERANK_SCORE_BODY,
              str(d["provenance"].get("rerank_score_body")))

    print(f"\n  질의: {a.query}")
    print(f"  후보 {d['provenance'].get('candidates_found')}건 → 상위 {len(d['hits'])}건"
          f" (본문 없어 제외 {d.get('dropped_incomplete')}건)")
    for i, h in enumerate(d["hits"], 1):
        print(f"   {i}. [{h['distance']:.3f}] {h['insurer']} · {h['section']} "
              f"{h['qualified_no']} {h['title'][:28]} p{h['page_from']}–{h['page_to']}")

    #: ★건너뛴 항목(ok=None)은 실패가 아니다. 다만 **건너뛰었다는 사실은 남긴다** —
    #:   조용히 통과로 세면 안 본 것을 봤다고 하게 된다.
    ran = [c for c in report["checks"] if c["ok"] is not None]
    skipped = [c for c in report["checks"] if c["ok"] is None]
    ok = all(c["ok"] for c in ran)
    report["all_ok"] = ok
    report["skipped"] = [c["name"] for c in skipped]
    if skipped:
        print(f"  건너뜀 {len(skipped)}항목: {', '.join(c['name'] for c in skipped)}")
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  기록 → {a.out}")
    print(f"\n  {'전부 통과' if ok else '★실패 있음'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
