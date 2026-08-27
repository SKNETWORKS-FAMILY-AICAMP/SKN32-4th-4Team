"""**우리 약관에 실제로 등장하는 질병기호** 목록을 만든다.

★이건 KCD 사전이 아니다 — 그렇게 부르지 않는다

    KCD 전체는 약 2만 항목이고 우리는 그 **코드→질병명 표를 갖고 있지 않다**
    (`app/core/domain/kcd_ranges.py` 머리말 참조). 그래서 `F32` 가
    「우울에피소드」라고 말하지 않는다. 근거가 없기 때문이다.

    대신 답할 수 있는 것은 이것이다 —

        **우리가 확정한 약관에서 어떤 질병기호가 · 어떻게 쓰이는가**

    관리자에게는 이쪽이 더 쓸모 있다. 「우리 시스템이 판정할 수 있는 코드가
    무엇인가」에 직접 답하기 때문이다.

★왜 미리 만들어 두나

    확정 약관 전량을 훑으면 **약 100초** 걸린다(실측: 60약관 7.0초).
    요청마다 돌릴 수 없다. 만들어 두고 관리자 화면이 읽는다.

★무엇을 담나

    범위        `F04~F99` · `N39.3` 처럼 약관이 쓴 표기 그대로
    장          `5 정신·행동` (KCD 22장. **질병명이 아니다**)
    쓰임        `exclude` 면책 · `exception` 면책의 예외 · `mention` 그 밖의 언급
    약관 수     몇 개 약관에 나오는가
    예시 조항   어디서 나왔는지 (조 번호·제목)

쓰는 법:
    python -m scripts.eval.kcd_catalog                 # 확정 약관 전량
    python -m scripts.eval.kcd_catalog --limit 100     # 표본만(빠른 확인)
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import time

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_OUT = _ROOT / "data" / "exports" / "kcd_catalog.json"


def build(limit: int = 0) -> dict:
    from app.adapters import file_clause_store as store
    from app.adapters import manifest_policy_resolver as resolver
    from app.core.domain.kcd_ranges import chapter_of, scan_clause

    versions = resolver.load_versions()
    if limit:
        versions = versions[:limit]

    #: (표기, 쓰임) → 통계. ★약관 수와 언급 수를 **나눠 센다** —
    #:   한 약관에 같은 범위가 여러 번 나오므로 언급 수만 보면 부풀어 보인다.
    mentions: collections.Counter = collections.Counter()
    docs: dict[tuple[str, str], set[str]] = collections.defaultdict(set)
    sample: dict[tuple[str, str], dict] = {}
    read_failed = 0
    t0 = time.time()

    for i, v in enumerate(versions):
        try:
            rows = store.load_clauses(v.sha256)
        except Exception as exc:  # noqa: BLE001
            #: ★조용히 넘기지 않는다. 못 읽은 것을 세어 결과에 적는다(CLAUDE.md §3).
            read_failed += 1
            if read_failed <= 3:
                print(f"  ? 조항을 못 읽음 {v.sha256[:12]}: {str(exc)[:70]}", flush=True)
            continue
        for r in rows:
            for m in scan_clause(r.text or ""):
                key = (str(m.range), m.kind)
                mentions[key] += 1
                docs[key].add(v.sha256)
                sample.setdefault(key, {
                    "insurer": v.insurer,
                    "qualified_no": r.qualified_no,
                    "title": r.title,
                    "context": (m.context or "")[:160],
                })
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(versions)} · {time.time() - t0:.0f}초", flush=True)

    items = []
    for (rng, kind), n in mentions.items():
        lo = rng.split("~")[0].split("∼")[0].strip()
        items.append({
            "range": rng,
            "kind": kind,
            "chapter": chapter_of(lo),
            "documents": len(docs[(rng, kind)]),
            "mentions": n,
            "example": sample.get((rng, kind), {}),
        })
    #: 약관 수 → 언급 수 순. 널리 쓰이는 것이 위로.
    items.sort(key=lambda x: (-x["documents"], -x["mentions"], x["range"]))

    return {
        "schema_version": "v1",
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "확정 약관(config/confirmed_documents.jsonl)의 조항 본문",
        #: ★★**분모를 함께 적는다.** 「37개 코드」만 내보내면 KCD 전체인 줄 안다.
        "scanned_policies": len(versions),
        "read_failed": read_failed,
        "total_ranges": len(items),
        "total_mentions": sum(mentions.values()),
        "★한계": [
            "이건 KCD 사전이 아니다 — 코드→질병명 표를 우리는 갖고 있지 않다(약 2만 항목).",
            "여기 있는 것은 **확정 약관 본문에 실제로 등장한 표기**뿐이다.",
            "`chapter` 는 KCD 22장 분류이지 질병명이 아니다.",
            "확정되지 않은 약관은 스캔하지 않는다 — 확정 범위가 넓어지면 이 목록도 늘어난다.",
        ],
        "items": items,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="확정 약관 상한(0=전량)")
    a = ap.parse_args(argv)

    data = build(a.limit)
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n약관 {data['scanned_policies']:,} 스캔(못 읽음 {data['read_failed']}) · "
          f"고유 표기 {data['total_ranges']:,} · 언급 {data['total_mentions']:,}")
    print(f"→ {_OUT}")
    by_kind = collections.Counter(x["kind"] for x in data["items"])
    print(f"쓰임: {dict(by_kind)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
