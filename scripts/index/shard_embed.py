"""임베딩을 **GPU 상자와 나눠서** 돌린다.

★이 기계는 CPU 8스레드로 **초당 9조각**이다. 전량 175,217조각 = 약 5.4시간.
  별도 GPU 작업자(RTX 4070 SUPER 12GB)를 같이 쓰면 그만큼 줄어든다.

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

#: ★★**차원을 여기 박지 않는다** (2026-08-26 정정).
#:
#:   앞서는 `DIM = 768` 이 박혀 있었다. 그런데 현행 승인 프로필은 arctic-ko **1024** 다.
#:   그 상태로 `load` 를 돌리면 —
#:     · `reshape(-1, 768)` 이 **에러를 안 낸다.** 총 원소 수가 768의 배수면 통과한다.
#:     · 그러면 **모든 벡터가 어긋난 채 박히고** 검색이 조용히 망가진다.
#:
#:   차원은 **승인 프로필이 정한다.** 여기서 파생한다 —
#:   그리고 파일 크기를 `4 × dim × 조각수` 와 **정확히** 대조해
#:   reshape 가 조용히 통과할 여지를 없앤다.
#:
#: ★같은 파일 주석에 「분산 적재는 기본 CI 가 안 도는 경로라 시험이 아니라
#:   운영에서 터질 자리」가 이미 두 번 적혀 있다. 이게 **세 번째**다.


def _dim() -> int:
    from db.postgres import pgvector_clause_index as ix

    return int(ix.embed_dim())


def _profile_fingerprint() -> dict:
    """`.f32` 를 **누가 어떤 설정으로** 만들었는지. 적재 때 대조한다."""
    from app.adapters.clause_document_embedder import accepted_profile

    p = accepted_profile()
    return {k: p[k] for k in ("model", "revision", "dim", "doc_prefix",
                              "normalized", "max_seq_length") if k in p}


def _plan(shards: int, index: int):
    """아직 임베딩 안 된 조항을 조각까지 만들어 돌려준다."""
    from db.postgres import pgvector_clause_index as ix
    from db.postgres.pgvector_index import get_conn

    from scripts.index.build_clause_index import _clause_tag, _collect, _token_counter

    conn = get_conn()
    ix.ensure_schema(conn)
    #: ★세대를 명시해 넘긴다. 안 넘기면 `TypeError` 다(코덱스 라운드2 지적) —
    #:   `_collect` 가 태그를 필수 인자로 받게 바뀐 뒤 여기만 안 따라왔다.
    #: ★★**같은 일이 또 났다**(2026-08-26) — `_collect` 가 `demotions` 를 하나 더
    #:   돌려주게 바뀌었는데 여기만 3개로 풀고 있어 `ValueError: too many values to
    #:   unpack` 로 죽었다. 분산 적재는 기본 CI 가 안 도는 경로라 **시험이 아니라
    #:   운영에서 터질 자리였다.**
    #:   → 반환 개수를 세지 않아도 되게 `_collect` 를 NamedTuple 로 바꾸는 것이
    #:     근본 대책이다(§build_clause_index.py 의 반환부 주석 참조).
    texts, _occ, _demotions, _report = _collect(None, False, _clause_tag())
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

    m = sub.add_parser("embed", help="GPU 상자에서 조각을 벡터로 (DB 안 본다)")
    m.add_argument("--jsonl", required=True)
    m.add_argument("--out", required=True, help="`.f32` 경로. 옆에 `.meta.json` 도 쓴다")
    m.add_argument("--batch", type=int, default=64)
    m.add_argument("--profile", required=True,
                   help="`export` 가 남긴 프로필 JSON. GPU 는 DB 를 못 보므로 파일로 받는다")

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
        #: ★★**프로필을 함께 내보낸다.** GPU 는 DB 를 못 보므로 승인 프로필을
        #:   알 방법이 없다. 파일로 건네고, 돌아온 벡터를 그 값으로 대조한다.
        side = pathlib.Path(args.out).with_suffix(".profile.json")
        from app.adapters.clause_document_embedder import accepted_profile

        side.write_text(json.dumps(accepted_profile(), ensure_ascii=False, indent=1),
                        encoding="utf-8")
        print(f"[내보냄] 남은 조항 {total:,} 중 내 몫 {len(plan):,} → 조각 {n:,} → {args.out}")
        print(f"[프로필] {side}  (GPU 로 함께 보낸다)")
        return 0

    if args.cmd == "embed":
        #: ★★**DB 를 안 본다.** 이 갈래는 GPU 상자에서 돈다 —
        #:   원격에 DB 자격증명을 두지 않기 위해서다(모듈 설명 참조).
        import numpy as np
        from sentence_transformers import SentenceTransformer

        prof = json.loads(pathlib.Path(args.profile).read_text(encoding="utf-8"))
        need = ("model", "revision", "dim", "doc_prefix", "normalized", "max_seq_length")
        #: ★**키가 있는지**를 본다. 값이 `""`·`False`·`0` 인 것과 «없는» 것은 다르다 —
        #:   `doc_prefix` 는 arctic-ko 에서 **빈 문자열이 정상**이고,
        #:   `normalized` 는 `False` 가 정상일 수 있다.
        #:   실측(2026-08-26): `in (None, "")` 로 걸렀더니 빈 접두어를 「값 없음」으로 보고
        #:   GPU 쪽이 그 자리에서 멈췄다.
        missing = [k for k in need if k not in prof or prof[k] is None]
        if missing:
            #: ★모르는 값을 기본값으로 때우지 않는다. 그러면 이쪽과 저쪽 설정이 갈린다.
            raise SystemExit(f"프로필에 값이 없습니다: {missing}")
        for k in ("model", "revision"):
            #: 이 둘만은 빈 문자열도 안 된다 — 무엇으로 만들지가 안 정해진다.
            if not str(prof[k]).strip():
                raise SystemExit(f"프로필의 {k} 가 비었습니다.")

        rows = [json.loads(x) for x in open(args.jsonl, encoding="utf-8")]
        model = SentenceTransformer(prof["model"], revision=prof["revision"])
        model.max_seq_length = int(prof["max_seq_length"])
        prefix = prof["doc_prefix"]
        texts = [prefix + r["t"] for r in rows]
        vecs = model.encode(texts, batch_size=args.batch,
                            normalize_embeddings=bool(prof["normalized"]),
                            show_progress_bar=True, convert_to_numpy=True)
        vecs = np.asarray(vecs, dtype=np.float32)
        if vecs.shape != (len(rows), int(prof["dim"])):
            #: ★모양이 다르면 **쓰지 않는다.** 써 두면 나중에 조용히 적재된다.
            raise SystemExit(
                f"벡터 모양이 {vecs.shape} 인데 (조각 {len(rows)}, 차원 {prof['dim']}) 여야 합니다."
            )
        if not np.isfinite(vecs).all():
            raise SystemExit("유한하지 않은 값이 섞였습니다 — 쓰지 않습니다.")
        vecs.tofile(args.out)

        fp = {k: prof[k] for k in need}
        pathlib.Path(args.out).with_suffix(".meta.json").write_text(
            json.dumps({"profile": fp, "chunks": len(rows),
                        "bytes": 4 * int(prof["dim"]) * len(rows)},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[임베딩] 조각 {len(rows):,} → {args.out} "
              f"({4 * int(prof['dim']) * len(rows):,}바이트) · 프로필 기록 함께 남김")
        return 0

    #: ── load ──
    import numpy as np

    from db.postgres import pgvector_clause_index as ix
    from db.postgres.pgvector_index import get_conn

    rows = [json.loads(x) for x in open(args.jsonl, encoding="utf-8")]
    dim = _dim()

    #: ★★**reshape 하기 전에 바이트로 검사한다.**
    #:   `reshape(-1, dim)` 은 총 원소 수만 맞으면 통과한다. 차원이 어긋나도
    #:   우연히 나누어떨어지면 **조용히 잘못된 모양**을 만든다.
    #:   먼저 `4 × dim × 조각수` 와 정확히 같은지 본다 — 여기서 걸리면 못 지나간다.
    want = 4 * dim * len(rows)
    got = pathlib.Path(args.vecs).stat().st_size
    if got != want:
        raise SystemExit(
            f"벡터 파일이 {got:,}바이트인데 조각 {len(rows):,} × 차원 {dim} 이면 "
            f"{want:,}바이트여야 합니다. 차원이나 조각 수가 어긋났습니다 — 적재하지 않습니다."
        )

    #: ★같은 프로필로 만든 벡터인지 대조한다. 모델·revision 이 다르면 섞이면 안 된다.
    side = pathlib.Path(args.vecs).with_suffix(".meta.json")
    if not side.exists():
        #: ★기록이 없다고 그냥 넣지 않는다. 무엇으로 만든 벡터인지 모르는 채 적재하면
        #:   나중에 「어느 모델 것이냐」를 답할 수 없다.
        raise SystemExit(
            f"프로필 기록이 없습니다: {side.name}. `embed` 하위명령으로 만든 벡터여야 합니다."
        )
    meta = json.loads(side.read_text(encoding="utf-8"))
    want_fp = _profile_fingerprint()
    if meta.get("profile") != want_fp:
        #: ★모델·revision 이 다른 벡터를 섞으면 검색 순위가 조용히 갈린다.
        raise SystemExit(
            "벡터를 만든 임베딩 프로필이 지금 승인 프로필과 다릅니다 — 적재하지 않습니다. "
            + f"파일={meta.get('profile')} / 지금={want_fp}"
        )

    vecs = np.fromfile(args.vecs, dtype=np.float32).reshape(-1, dim)
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
