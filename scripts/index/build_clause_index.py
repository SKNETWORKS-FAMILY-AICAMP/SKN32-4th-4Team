"""인덱스 A 적재 — 약관 조항을 pgvector 에 올린다.

    python -m scripts.index.build_clause_index            # 전량(재개 가능)
    python -m scripts.index.build_clause_index --limit 500   # 맛보기
    python -m scripts.index.build_clause_index --stats       # 현황만

★고유 내용만 임베딩한다

    실측(s5 전량): 조항 등장 **211,131** / 고유 **73,031** — 중복 65.4%.
    등장마다 임베딩하면 같은 계산을 3배 한다.
    `parse_status == "ok"` 문서의 고유 조항 **52,899** 가 대상이다.

★재개 가능하다

    이미 들어간 `content_hash` 는 건너뛴다. 중간에 끊겨도 처음부터 다시 하지 않는다.
    끊긴 것을 모르고 "다 됐다"고 하지 않기 위해 **끝에 현황을 다시 세어 출력한다.**

    ★**조항 단위로 넣는다.** 처음엔 조각 256개씩 묶었는데, 한 조항의 조각이
      배치 경계에 걸치면 중간에 죽었을 때 **반쪽이 남고**
      다음 실행이 "이미 있다"고 건너뛴다. 실측(중단 지점): 내용 12,507개 중
      2개가 그렇게 잘려 있었다. 이제 `n_chunks` 로 개수를 맞춰 본다.

    ★긴 작업이다. 실측(2026-08-02, 이 기계 CPU 8스레드):
      조항당 조각 **3.19** → 전량 약 **168,600조각** · 초당 8개 → **약 6시간**.
      토큰 기준으로 바꾸면서 조각이 27% 늘었다(구 800자 방식 132,535).
      GPU 라면 14~47분이다. 부풀려 말하지 않는다 —
      "곧 끝난다"고 하면 다음 사람이 중간 결과를 완성본으로 오해한다.

    ★**`nohup` 으로 띄우지 마라.** 실제로 사고가 났다(2026-08-02) —
      셸을 죽였는데 파이썬이 살아남아 **옛 코드로 계속 DB 에 썼다.**
      스키마를 바꾼 뒤라 `n_chunks=0` 인 고아 조각 1,803개가 쌓였다.
      끝낼 때는 프로세스를 직접 확인하고 죽인다.

★건너뛴 것을 **센다**

    조용한 스킵을 만들지 않는다(CLAUDE.md §3). 세지 않으면 분모가 줄어
    커버리지가 실제보다 좋아 보인다.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_STRUCT = _ROOT / "data" / "structured"

#: 한 번에 임베딩할 조각 수. 크게 잡아도 처리량은 비슷하고 메모리만 는다.
_BATCH = 256


#: ★★**읽을 조항 버전을 여기서 정하지 않는다.**
#:
#:   전에는 `_CLAUSE_TAG = "s6_"` 였다. 승인 릴리스는 `s5` 였는데 적재만 `s6` 를 읽어
#:   **판정 경로와 벡터 경로가 다른 세대를 보고 있었다**(실측 2026-08-03).
#:
#:   ★단, 적재는 **shadow 세대**로도 돌 수 있어야 한다 — 승인 전에 미리 만들어 두고
#:     평가를 통과하면 승인 포인터만 바꾸는 방식이다(코덱스). 그래서 `--clause-tag` 로
#:     **명시**할 수 있게 두되, 기본값은 승인 릴리스다. 명시하면 출력에 남긴다.


def _clause_tag(override: str | None = None) -> str:
    if override:
        return override
    from app.core import release

    return release.load().clause_tag


def _iter_docs(limit: int | None, tag: str):
    files = sorted(_STRUCT.glob(f"*/{tag}/*.clauses.json"))
    if limit:
        files = files[:limit]
    for p in files:
        yield p, json.loads(p.read_text(encoding="utf-8"))


def _token_counter(profile: dict | None = None):
    """임베딩 모델의 **실제 토크나이저**로 센다.

    ★글자 수로 세면 안 된다. `ko-sroberta` 의 한계는 **512토큰**인데
      800자 조각의 1.4%가 그걸 넘어 뒤가 조용히 잘렸다(실측 2026-08-02).
    """
    from functools import lru_cache

    from transformers import AutoTokenizer

    from app.adapters.clause_document_embedder import accepted_profile

    profile = profile or accepted_profile()
    tok = AutoTokenizer.from_pretrained(
        profile["model"],
        revision=profile["revision"],
    )

    @lru_cache(maxsize=200_000)
    def count(text: str) -> int:
        return len(tok.encode(text, add_special_tokens=True))

    return count


def main(argv: list[str] | None = None) -> int:
    from app.adapters import pgvector_clause_index as ix
    from app.adapters.pgvector_index import get_conn

    ap = argparse.ArgumentParser(description="인덱스 A 적재")
    ap.add_argument("--limit", type=int, default=0, help="문서 수 제한(맛보기)")
    ap.add_argument("--stats", action="store_true", help="현황만 출력")
    #: ★GPU 상자와 나눠 돌릴 때 쓴다. 해시 정렬 순의 나머지 연산이라 **결정적**이다 —
    #:   두 기계가 같은 조각을 두 번 하지 않고, 빠지지도 않는다.
    ap.add_argument("--clause-tag", default="",
                    help="★shadow 적재용. 승인 릴리스가 아닌 세대를 읽는다")
    ap.add_argument("--shards", type=int, default=1, help="전체 조각 수")
    ap.add_argument("--index", type=int, default=0, help="내가 맡을 몫(0부터)")
    ap.add_argument("--ignore-citation-gate", action="store_true",
                    help="★구조 모순 문서도 색인한다. 끈 사실이 출력에 남는다")
    args = ap.parse_args(argv)

    conn = get_conn()
    ix.ensure_schema(conn)

    if args.stats:
        print(json.dumps(ix.stats(conn), ensure_ascii=False, indent=2))
        return 0

    tag = _clause_tag(args.clause_tag)
    #: 적재 한 판 안에서 읽기·쓰기가 **같은 태그**를 쓰도록 굳혀 둔다.
    args.resolved_tag = tag
    note = "  ★승인 릴리스가 아니라 지정한 세대를 읽는다(--clause-tag)" if args.clause_tag else ""
    print(f"[세대] 조항 산출 {tag}{note}", flush=True)
    texts, occurrences, report = _collect(args.limit or None, args.ignore_citation_gate, tag)
    print(report, flush=True)
    return _load(conn, texts, occurrences, args)


def _collect(limit, ignore_gate: bool, tag: str):
    """조항 JSON → `(내용 dict, 발생 list, 보고 문자열)`.

    ★**한 곳에서만 모은다.** 분산 적재(`shard_embed`)도 이 함수를 쓴다 —
      수집 규칙을 두 벌 두면 게이트 하나가 달라져도 아무도 모른다.
    """
    #: ★먼저 **문서에서 모은다.** 임베딩은 그다음이다 —
    #:   중복 제거를 하기 전에 임베딩하면 3배를 계산한다.
    texts: dict[str, str] = {}
    occurrences: list[tuple] = []
    n_docs = n_skip_doc = n_clause = n_skip_clause = 0
    n_skip_cite = 0     # citation_eligible=false 로 건너뛴 **조항**
    n_annex = n_skip_annex = 0   # 부록(별표·붙임·분류표)

    from app.core.domain import eligibility

    for p, doc in _iter_docs(limit, tag):
        status = doc.get("parse_status") or "unknown"
        if status != "ok":
            #: ★추출이 의심스러운 문서의 조항은 판정 근거가 될 수 없다.
            n_skip_doc += 1
            continue
        #: ★★`parse_status` 만으로는 부족하다. **축이 다르다.**
        #:
        #:   `parse_status` — 파싱이 됐나(길이·개수가 말이 되나)
        #:   `citation_eligible` — **인용해도 되나**(조 경계가 서로 모순이 아닌가)
        #:
        #:   실측 반례 `16b227ff95b8`: `parse_status=ok` 인데
        #:     · 조 번호가 `제4 → 제5 → 제4` 로 되돌아온다(본문이 앞 조로 오귀속)
        #:     · `제27조(준용규정)` 이 붙임·질병분류표를 삼켰다
        #:   그대로 색인하면 **KCD 코드가 잘못된 조항에 인용된다.**
        #:
        #:   ★신호의 precision 은 아직 검증되지 않았다(정답셋 없음). 그래서 이 게이트는
        #:     **보수적**이다 — 지금 통과하는 문서는 161건뿐이다.
        #:     `--ignore-citation-gate` 로 끌 수 있게 두되, **끈 사실을 출력에 남긴다.**
        #: ★★게이트는 **조항 단위**다. 문서 전체를 건너뛰면 안 된다 —
        #:   결함 4개 때문에 그 문서의 조항 155개를 통째로 버린다.
        #:   실측: 문서 게이트 897조항(0.42%) → 조항 게이트 168,523(93.95%),
        #:   「보상하지 않는 사항」 조항이 0 → 2,224개.
        n_docs += 1
        src = doc.get("source") or {}
        sha = src.get("sha256") or ""
        insurer = src.get("insurer") or ""
        for c in doc.get("clauses") or []:
            #: ★조항 단위 게이트. 구조 모순이 걸린 조항만 뺀다.
            #: ★★`is False` 가 아니라 **`is not True`** 다.
            #:   전에는 필드가 **없으면 통과**했다(fail-open · 코덱스 지적).
            #:   옛 스키마 산출물이 조용히 근거가 된다. 모르면 못 쓴다(§0).
            #:   ★법령 조문(`is_statute`)도 뺀다 — 약관 조항이 아니다.
            #:     그대로 인용하면 "특별약관 제651조(고지의무위반…)" 같은 근거가 나간다.
            #: ★★**공통 게이트 하나로 판정한다.** 부분 조건을 여기 또 쓰면
            #:   저장소와 규칙이 갈린다 — 그게 애초의 문제였다(코덱스).
            if not ignore_gate and not eligibility.check(c, parse_status=status).usable:
                n_skip_cite += 1
                continue
            h = c.get("content_hash") or ""
            body = c.get("text") or ""
            if not h or not body.strip():
                n_skip_clause += 1
                continue
            n_clause += 1
            texts.setdefault(h, body)
            loc = c.get("locator") or {}
            occurrences.append(
                (
                    h,
                    sha,
                    insurer,
                    c.get("qualified_no") or "",
                    c.get("section") or "",
                    c.get("title") or "",
                    int(loc.get("page_from") or c.get("page_from") or 0),
                    int(loc.get("page_to") or c.get("page_to") or 0),
                    "clause",
                )
            )

        #: ★★**부록도 넣는다.** s6 부터 별표·붙임·분류표가 `annexes[]` 로 빠졌는데
        #:   여기서 안 읽으면 **질병분류표가 검색에 아예 없어진다.**
        #:   KCD 코드 대조의 근거가 대부분 거기 있다 — 조항만 넣으면
        #:   판정이 "확인 불가"만 내거나, 더 나쁘게는 표를 삼킨 옛 조항을 인용한다.
        #:
        #:   ★`qualified_no` 자리에 `label`(`[별표1] 특정질병 분류표`)을 넣는다.
        #:     조 번호를 지어내지 않는다 — 부록은 조가 아니다.
        #:     `owner_clause_ordinal` 이 `None` 인 것도 같은 이유다.
        for a in doc.get("annexes") or []:
            h = a.get("content_hash") or ""
            body = a.get("text") or ""
            if not h or not body.strip():
                n_skip_annex += 1
                continue
            n_annex += 1
            texts.setdefault(h, body)
            loc = a.get("locator") or {}
            occurrences.append((
                h, sha, insurer,
                a.get("label") or "부록",
                a.get("section") or "",
                a.get("label") or "",
                int(loc.get("page_from") or 0),
                int(loc.get("page_to") or 0),
                "annex",
            ))

    return texts, occurrences, (
        f"[모음] 적재 대상 문서 {n_docs:,} · "
        f"건너뜀: 문서 parse_status {n_skip_doc:,} · 조항 citation_eligible {n_skip_cite:,} · "
        f"조항 등장 {n_clause:,} + 부록 {n_annex:,} → 고유 {len(texts):,} "
        f"(내용/해시 없음: 조항 {n_skip_clause:,} · 부록 {n_skip_annex:,})"
        + ("  ★인용 게이트를 껐다(--ignore-citation-gate)" if ignore_gate else "")
    )


def _load(conn, texts, occurrences, args):
    """모은 것을 임베딩해 넣는다."""
    from app.adapters import pgvector_clause_index as ix
    import json, time

    #: ★★**읽은 세대를 그대로 기록한다.** 안 넘기면 `--clause-tag=s6…` 로 읽고도
    #:   발생행은 **승인 세대(s5)** 로 박힌다 — shadow 적재가 그대로 혼입이 된다
    #:   (코덱스 라운드2 지적). 읽은 곳과 쓰는 곳의 세대는 **같은 값**이어야 한다.
    #: ★★**읽은 태그에서 곧바로 세대를 낸다.** `current_generation()` 을 따로 부르면
    #:   읽는 사이에 승인 포인터가 바뀌었을 때 **읽은 세대와 기록할 세대가 달라진다**
    #:   (코덱스 라운드3 지적). 같은 값에서 파생해야 경쟁이 없다.
    n_occ = ix.upsert_occurrences(conn, occurrences,
                                  generation=ix.generation_of(args.resolved_tag))
    print(f"[발생] {n_occ:,}행 새로 기록 (총 {len(occurrences):,}건 시도)", flush=True)

    #: ★반쪽으로 남은 것을 먼저 지운다. 남겨 두면 검색에 잘린 본문이 올라온다.
    dropped = ix.drop_incomplete(conn)
    if dropped:
        print(f"[정리] 미완성 조항 {dropped:,}개를 지우고 다시 넣는다", flush=True)

    done = ix.existing_hashes(conn)
    #: ★해시로 **정렬**한 뒤 가른다. dict 순서에 기대면 재실행 때 몫이 달라져
    #:   이미 한 것을 또 하고 안 한 것이 남는다.
    rest = sorted((h, t) for h, t in texts.items() if h not in done)
    todo = [x for n, x in enumerate(rest) if n % args.shards == args.index]
    note = f" · 내 몫 {args.index}/{args.shards}" if args.shards > 1 else ""
    print(f"[임베딩] 이미 있음 {len(done):,} · 남은 것 {len(rest):,} · 할 것 {len(todo):,}{note}",
          flush=True)
    if not todo:
        print(json.dumps(ix.stats(conn), ensure_ascii=False, indent=2))
        return 0

    #: ★스레드를 다 쓴다. torch 기본값은 **물리 코어 수**라 8코어 기계에서 4개만 썼다.
    #:   임베딩이 이 작업의 전부이므로 여기서 20~30%가 갈린다.
    try:
        import os as _os

        import torch

        n = _os.cpu_count() or 1
        if torch.get_num_threads() < n:
            torch.set_num_threads(n)
            print(f"[임베딩] torch 스레드 {n}개로 올림", flush=True)
    except Exception as exc:  # noqa: BLE001
        #: ★조용히 넘어가지 않는다. 느린 이유를 나중에 못 찾게 된다.
        print(f"[임베딩] 스레드 조정 실패(그대로 진행): {exc}", flush=True)

    from app.adapters.clause_document_embedder import build as build_document_embedder

    embed = build_document_embedder()
    profile = embed.profile
    if int(profile["chunk_budget"]) != ix.MAX_TOKENS:
        raise RuntimeError(
            "승인 임베딩 프로필의 chunk_budget 과 인덱스 청킹 예산이 다르다: "
            f"{profile['chunk_budget']} != {ix.MAX_TOKENS}"
        )
    if int(profile["overlap"]) != ix.OVERLAP_TOKENS:
        raise RuntimeError(
            "승인 임베딩 프로필의 overlap 과 인덱스 겹침 예산이 다르다: "
            f"{profile['overlap']} != {ix.OVERLAP_TOKENS}"
        )

    #: ★**조항 단위**로 묶는다. 한 조항의 조각이 배치 경계에 걸치면
    #:   중간에 죽었을 때 반쪽이 남는다.
    count = _token_counter(profile)
    plan: list[tuple[str, str, list[str]]] = []
    n_chunks_total = n_empty = 0
    for h, body in todo:
        parts = ix.chunk_clause(body, count)
        if not parts:
            #: ★조각이 0인 조항. 조용히 넘기지 않는다(CLAUDE.md §3).
            n_empty += 1
            continue
        plan.append((h, body, parts))
        n_chunks_total += len(parts)
    print(
        f"[임베딩] 조항 {len(plan):,}개 → 조각 {n_chunks_total:,}개 "
        f"(토큰 예산 {ix.MAX_TOKENS}, 겹침 {ix.OVERLAP_TOKENS}"
        + (f" · 조각 0인 조항 {n_empty:,}" if n_empty else "") + ")",
        flush=True,
    )

    t0 = time.time()
    written = 0
    done_chunks = 0
    i = 0
    while i < len(plan):
        #: 배치를 조각 수로 채우되 **조항을 쪼개지 않는다.**
        batch: list[tuple[str, str, list[str]]] = []
        size = 0
        while i < len(plan) and (not batch or size + len(plan[i][2]) <= _BATCH):
            batch.append(plan[i])
            size += len(plan[i][2])
            i += 1

        flat = [(h, ci, len(parts), part)
                for h, _, parts in batch
                for ci, part in enumerate(parts)]
        vecs = embed.embed_documents([f[3] for f in flat])
        ix.upsert_content(conn, [(h, body, len(parts)) for h, body, parts in batch])
        written += ix.upsert_chunks(
            conn, [(f[0], f[1], f[2], f[3], v) for f, v in zip(flat, vecs)]
        )
        done_chunks += len(flat)
        el = time.time() - t0
        rate = done_chunks / el if el else 0
        left = (n_chunks_total - done_chunks) / rate if rate else 0
        print(
            f"  {done_chunks:,}/{n_chunks_total:,} 조각 · {rate:.0f}/s · 남은 시간 {left/60:.1f}분",
            flush=True,
        )

    print(f"[완료] {written:,}조각 기록 · {(time.time()-t0)/60:.1f}분", flush=True)
    #: ★끝에 **다시 세어** 출력한다. 중간에 끊겼는지 여기서 드러난다.
    print(json.dumps(ix.stats(conn), ensure_ascii=False, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
