"""임베딩을 **GPU 상자와 나눠서** 돌린다.

★기준 장비는 CPU 8스레드로 **초당 9조각**이다. 전량 175,217조각 = 약 5.4시간.
  별도 GPU 장비(RTX 4070 SUPER 12GB)를 같이 쓰면 그만큼 줄어든다.

★★**조각내기는 여기서 한다.** GPU 쪽은 임베딩만 시킨다.

    `chunk_clause` 는 토크나이저로 토큰을 세어 문장 경계에서 끊는다.
    양쪽에서 따로 돌리면 transformers 판이 조금만 달라도 **경계가 어긋나고**,
    그러면 같은 조항이 이쪽 3조각·저쪽 4조각이 되어
    `n_chunks` 검사(반쪽 적재 탐지)가 무너진다.
    조각은 한 곳에서 만들고 **텍스트를 그대로 보낸다.**

★DB 는 원격에 열지 않는다. GPU 는 벡터만 돌려주고, 적재는 이 기계가 한다.
  원격에 DB 자격증명을 두지 않기 위해서다.

흐름:

    export  이 기계  아직 안 된 조항 → 조각 → `shard{i}.jsonl`
    (scp)            → GPU 상자
    embed   GPU      `shard{i}.jsonl` → `shard{i}.f32`  (768차원 float32 연속)
    (scp)            ← 이 기계
    load    이 기계  `upsert_content` + `upsert_chunks`

사용:

    python -m scripts.index.shard_embed export --shards 2 --index 1 --out C:/tmp/s1.jsonl
    python -m scripts.index.shard_embed load   --jsonl C:/tmp/s1.jsonl --vecs C:/tmp/s1.f32

★`--index` 는 **해시 정렬 순의 나머지 연산**이다. 결정적이라 두 기계가
  같은 조각을 두 번 하지 않는다. 겹치면 낭비고, 비면 **조용히 빠진다.**
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 임베딩 차원. 바뀌면 `.f32` 파일 해석이 통째로 어긋나므로 여기서 못박는다.
DIM = 768


def _plan(shards: int, index: int):
    """아직 임베딩 안 된 조항을 조각까지 만들어 돌려준다."""
    from app.adapters import pgvector_clause_index as ix
    from app.adapters.pgvector_index import get_conn

    from scripts.index.build_clause_index import _clause_tag, _collect, _token_counter

    conn = get_conn()
    ix.ensure_schema(conn)
    #: ★세대를 명시해 넘긴다. 안 넘기면 `TypeError` 다(코덱스 라운드2 지적) —
    #:   `_collect` 가 태그를 필수 인자로 받게 바뀐 뒤 여기만 안 따라왔다.
    texts, _occ, _report = _collect(None, False, _clause_tag())
    done = ix.existing_hashes(conn)
    #: ★해시로 정렬해 **결정적**으로 가른다. dict 순서에 기대면 재실행 때 달라진다.
    todo = sorted((h, t) for h, t in texts.items() if h not in done)
    mine = [(h, t) for n, (h, t) in enumerate(todo) if n % shards == index]
    count = _token_counter()
    out = []
    for h, body in mine:
        parts = ix.chunk_clause(body, count)
        if parts:
            out.append((h, body, parts))
    conn.close()
    return out, len(todo)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="임베딩 분산")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="내 몫의 조각을 JSONL 로 뽑는다")
    e.add_argument("--shards", type=int, required=True)
    e.add_argument("--index", type=int, required=True)
    e.add_argument("--out", required=True)

    l = sub.add_parser("load", help="돌아온 벡터를 DB 에 넣는다")
    l.add_argument("--jsonl", required=True)
    l.add_argument("--vecs", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "export":
        plan, total = _plan(args.shards, args.index)
        n = 0
        with open(args.out, "w", encoding="utf-8") as f:
            for h, body, parts in plan:
                for ci, part in enumerate(parts):
                    f.write(json.dumps(
                        {"h": h, "ci": ci, "n": len(parts), "t": part, "body": body if ci == 0 else ""},
                        ensure_ascii=False) + "\n")
                    n += 1
        print(f"[내보냄] 남은 조항 {total:,} 중 내 몫 {len(plan):,} → 조각 {n:,} → {args.out}")
        return 0

    #: ── load ──
    import numpy as np

    from app.adapters import pgvector_clause_index as ix
    from app.adapters.pgvector_index import get_conn

    rows = [json.loads(x) for x in open(args.jsonl, encoding="utf-8")]
    vecs = np.fromfile(args.vecs, dtype=np.float32).reshape(-1, DIM)
    #: ★개수가 안 맞으면 **멈춘다.** 짧은 쪽에 맞춰 넣으면 조용히 어긋난 벡터가 박힌다.
    if len(vecs) != len(rows):
        raise SystemExit(
            f"조각 {len(rows):,} 인데 벡터 {len(vecs):,} — 짝이 안 맞습니다. 적재하지 않습니다."
        )

    conn = get_conn()
    ix.ensure_schema(conn)
    bodies = {r["h"]: (r["body"], r["n"]) for r in rows if r["ci"] == 0}
    ix.upsert_content(conn, [(h, b, n) for h, (b, n) in bodies.items()])
    w = ix.upsert_chunks(
        conn, [(r["h"], r["ci"], r["n"], r["t"], v) for r, v in zip(rows, vecs)]
    )
    print(f"[적재] 조항 {len(bodies):,} · 조각 {w:,}")
    print(json.dumps(ix.stats(conn), ensure_ascii=False, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))
    raise SystemExit(main())
