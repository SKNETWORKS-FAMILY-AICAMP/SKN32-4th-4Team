"""**이미 계산된 벡터**를 인덱스 A 에 넣는다.

★왜 별도 경로인가 — `build_clause_index` 는 **여기서 임베딩까지 한다.**

    그건 GPU 가 있는 기계에서만 돌고, 이 노트북에서는 몇 시간 걸린다.
    벡터는 이미 RunPod 에서 만들어 왔다(`data/external/ours_s6_arctic_ko/`).
    같은 것을 두 번 계산하지 않는다.

★조건이 **승인 릴리스와 맞는지 먼저 본다.**

    `meta.json` 의 모델·차원·청킹이 `accepted_extraction.json` 의
    `embed_profile` 과 다르면 **적재하지 않는다.** 섞이면 표에 든 벡터가
    무엇으로 만든 것인지 아무도 모르게 된다(실측 2026-08-03 — 승인 세대와
    DB 값이 어긋난 채 검색이 조용히 0건을 돌려주고 있었다).

★세 층을 **각각** 넣는다 (CLAUDE.md §1 정체성/발생 분리).

    policy_clause_content     본문 한 벌
    policy_clause_chunk       조각 + 벡터 (`embed_model` = 프로필 키)
    policy_clause_occurrence  어느 문서 어디에 실렸나 (`index_generation`)

쓰는 법:
    python -m scripts.index.load_precomputed --src data/external/ours_s6_arctic_ko
    python -m scripts.index.load_precomputed --src ... --recreate-chunks
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _fail(msg: str) -> None:
    raise SystemExit(f"★{msg}")


def check_meta(meta: dict) -> str:
    """전달물 설정이 **승인 프로필과 같은가.** 다르면 멈춘다."""
    from app.core import release

    prof = release.current().embed_profile
    if not prof.is_set:
        _fail("승인된 임베딩 프로필이 없습니다. "
              "`config/accepted_extraction.json` 의 `embed_profile` 을 채우세요.")
    bad = []
    for key, got, want in (
        ("model", meta.get("model"), prof.model),
        ("dim", meta.get("dim"), prof.dim),
        ("chunk_budget", meta.get("chunk_budget"), prof.chunk_budget),
        ("overlap", meta.get("overlap"), prof.overlap),
    ):
        if got != want:
            bad.append(f"  {key}: 전달물 {got!r} · 승인 {want!r}")
    if not meta.get("normalized"):
        bad.append("  normalized 가 true 가 아닙니다 — 거리 계산이 뜻을 잃습니다")
    if bad:
        _fail("전달물이 승인 프로필과 다릅니다:\n" + "\n".join(bad))
    return prof.key


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="meta.json·chunks.jsonl·vectors.npz 가 든 폴더")
    ap.add_argument("--batch", type=int, default=2000)
    ap.add_argument("--recreate-chunks", action="store_true",
                    help="★조각 테이블을 **지우고 다시 만든다**(차원이 바뀔 때 필요)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    import numpy as np

    from app.adapters import pgvector_clause_index as ix
    from app.adapters.pgvector_index import get_conn

    src = pathlib.Path(a.src)
    meta = json.loads((src / "meta.json").read_text(encoding="utf-8"))
    model_key = check_meta(meta)
    print(f"승인 프로필과 일치 · 프로필 키 = {model_key}", flush=True)

    z = np.load(src / "vectors.npz")
    vecs = z["vectors"]
    hashes = [h.item() if hasattr(h, "item") else h for h in z["content_hash"]]
    seqs = [int(s) for s in z["seq"]]
    nchunks = [int(s) for s in z["n_chunks"]]
    if not (len(hashes) == len(seqs) == len(nchunks) == vecs.shape[0]):
        _fail("npz 안의 배열 길이가 서로 다릅니다")

    texts: list[str] = []
    with (src / "chunks.jsonl").open(encoding="utf-8") as f:
        for line in f:
            texts.append(json.loads(line)["text"])
    if len(texts) != vecs.shape[0]:
        _fail(f"조각 본문 {len(texts):,} 과 벡터 {vecs.shape[0]:,} 이 다릅니다")
    print(f"조각 {len(texts):,} · 차원 {vecs.shape[1]} · 조항 {len(set(hashes)):,}", flush=True)

    #: 본문 한 벌 — 조각을 이어 붙이지 않는다. 원본을 산출물에서 다시 읽는다.
    gen = ix.current_generation()
    #: ★★**sha256 은 매니페스트에서 온다.** 산출물 JSON 에는 그 필드가 없다.
    #:
    #:   앞서 `p.stem` 으로 때웠다가 `"fd36cc4d66b2.clauses"` 가 들어갔다 —
    #:   `.clauses.json` 은 확장자가 둘이라 `stem` 이 하나만 뗀다.
    #:   그 결과 발생 186,094행의 `sha256` 이 20자가 되어
    #:   **파일 저장소(64자 sha 를 받아 앞 12자로 찾는다)와 짝이 안 맞았다.**
    #:   PG 경로로 조회하면 전부 "색인에 없습니다" 가 나왔다(실측 2026-08-03).
    #:
    #:   ★길이로 검사한다. 12자 sha 로 매니페스트를 찾아 **전체 64자**를 쓴다.
    #: ★전처리 매니페스트가 **전량(1,367)** 을 덮는다. 수집 카탈로그(573)만 보면 모자란다.
    #:   실측 2026-08-03 — 카탈로그만 쓰다가 `0149a994930a` 에서 멈췄다.
    sha_by12: dict[str, str] = {}
    for mani, key, holder in (
        (_ROOT / "data" / "manifests" / "preprocess" / "manifest_s6.json",
         "input_sha256", "documents"),
        (_ROOT / "data" / "catalog" / "2026-07-31_document_manifest.jsonl",
         "sha256", None),
    ):
        if not mani.exists():
            continue
        try:
            if holder:
                rows = json.loads(mani.read_text(encoding="utf-8")).get(holder) or []
            else:
                rows = [json.loads(x) for x in mani.open(encoding="utf-8") if x.strip()]
        except Exception as exc:  # noqa: BLE001
            print(f"  ? 매니페스트를 못 읽음 {mani.name}: {exc}", flush=True)
            continue
        for row in rows:
            full = (row or {}).get(key) or ""
            if len(full) == 64:
                sha_by12.setdefault(full[:12], full)
    print(f"매니페스트 sha {len(sha_by12):,}", flush=True)
    bodies: dict[str, str] = {}
    occ: list[tuple] = []
    tag = __import__("app.core.release", fromlist=["x"]).current().clause_tag
    want = set(hashes)
    for p in sorted((_ROOT / "data" / "structured").glob(f"*/{tag}/*.clauses.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  ? 읽지 못함 {p.name}: {exc}", flush=True)
            continue
        if d.get("parse_status") != "ok":
            continue
        sha12 = p.name.split(".")[0]
        sha = d.get("sha256") or sha_by12.get(sha12) or ""
        if len(sha) != 64:
            #: ★조용히 넘기지 않는다. 짝이 안 맞는 sha 를 넣으면
            #:   파일 경로와 PG 경로가 **다른 답**을 준다(§0).
            _fail(f"64자 sha256 을 찾지 못했습니다: {sha12} "
                  f"(매니페스트에 없습니다). 매니페스트를 갱신하세요.")
        insurer = d.get("insurer") or p.parent.parent.name
        for c in (d.get("clauses") or []):
            h = c.get("content_hash")
            if not h or h not in want:
                continue
            bodies.setdefault(h, (c.get("text") or "").strip())
            #: ★★**인용 게이트 값을 함께 넣는다.**
            #:   안 넣으면 조회 쪽이 "모른다 → 못 씀"으로 판정해
            #:   `load_clauses()` 가 **0건**을 돌려준다(실측 2026-08-04).
            #:   데이터를 다 갖춰 두고 못 쓰는 상태가 된다.
            #:   ★없는 값은 `None` 그대로 둔다. `True` 로 때우지 않는다(§0).
            #: ★★**페이지는 `locator` 안에 있다.** 최상위 `page_from` 은 `None` 이다.
            #:   파일 저장소는 `loc.get("page_from", 0)` 로 읽는다
            #:   (`file_clause_store.py:133`). 여기서 최상위만 보다가 전부 0 이 되어
            #:   같은 조항이 **두 경로에서 다른 자리**로 나왔다(실측 2026-08-04).
            #:   `page_from` 은 기본키의 일부라 0 이 되면 행도 갈린다.
            loc = c.get("locator") or {}
            occ.append((h, sha, insurer, c.get("qualified_no") or "",
                        c.get("section") or "", c.get("title") or "",
                        int(loc.get("page_from") or 0), int(loc.get("page_to") or 0),
                        "clause",
                        {"citation_eligible": c.get("citation_eligible"),
                         "chunk_type": c.get("chunk_type"),
                         #: ★★**산출물의 키는 `statute` 다.** `is_statute` 로 읽으면
                         #:   `None` 이 되어 게이트가 "참/거짓이 아니다"로 막는다.
                         #:   `eligibility.py:80` 에 같은 함정이 이미 적혀 있었다 —
                         #:   적어 둔 것을 안 읽고 되풀이했다(실측 2026-08-04).
                         "is_statute": c.get("statute", c.get("is_statute")),
                         "parse_status": d.get("parse_status")}))
    print(f"본문 {len(bodies):,} · 발생 {len(occ):,} (세대 {gen})", flush=True)
    missing = want - set(bodies)
    if missing:
        #: ★조용히 넘기지 않는다. 본문 없는 조각은 인용할 수 없다.
        _fail(f"벡터는 있는데 본문을 못 찾은 조항 {len(missing):,}건. "
              f"산출물 태그({tag})가 벡터를 만든 것과 다릅니까?")

    if a.dry_run:
        print("dry-run — 쓰지 않았습니다.", flush=True)
        return 0

    t0 = time.time()
    with get_conn() as conn:
        if a.recreate_chunks:
            #: ★차원이 바뀌면 `CREATE TABLE IF NOT EXISTS` 로는 안 바뀐다.
            #:   옛 조각은 **버리기로 한 모델**(ko-sroberta@128)이라 되살릴 이유가 없다.
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS policy_clause_chunk")
            conn.commit()
            print("옛 조각 테이블 드롭", flush=True)
        ix.ensure_schema(conn)
        conn.commit()

        n = 0
        for i in range(0, len(texts), a.batch):
            sl = slice(i, i + a.batch)
            ix.upsert_content(conn, [(h, bodies[h], nc)
                                     for h, nc in zip(hashes[sl], nchunks[sl])])
            ix.upsert_chunks(
                conn,
                [(h, s, nc, t, np.asarray(v, dtype=np.float32))
                 for h, s, nc, t, v in zip(hashes[sl], seqs[sl], nchunks[sl],
                                           texts[sl], vecs[sl])],
                model=model_key)
            conn.commit()
            n += len(texts[sl])
            if (i // a.batch) % 10 == 0:
                print(f"  조각 {n:,}/{len(texts):,} · {time.time()-t0:.0f}초", flush=True)
        print(f"조각 적재 {n:,} · {time.time()-t0:.0f}초", flush=True)

        got = ix.upsert_occurrences(conn, occ, generation=gen)
        conn.commit()
        print(f"발생 적재 {got:,}", flush=True)

        st = ix.index_state(conn)
        print(json.dumps(st, ensure_ascii=False, indent=1), flush=True)
        if not st["ready"]:
            _fail("적재했는데 색인이 승인 릴리스와 여전히 안 맞습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
