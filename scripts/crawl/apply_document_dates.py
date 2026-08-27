"""문서가 **스스로 밝힌 판매개시일**을 매니페스트에 반영한다.

★왜 세대가 아니라 **날짜**를 고치나

    `scripts/crawl/set_generation.py` 는 매니페스트를 다시 훑을 때마다
    세대를 **지우고 `sale_start` 로 다시 계산**한다.

        for k in ("generation", "generation_label", "generation_note"):
            r.pop(k, None)

    그래서 세대를 직접 써 넣으면 그 스크립트를 한 번만 돌려도 사라진다.
    **날짜를 고치면 세대는 따라온다.** 고칠 곳은 하나다.

★무엇을 근거로 고치나 — 문서 표지가 명시한 것만

    실측 2026-08-05 — 메리츠화재 7건은 매니페스트가 `20260501` 인데
    **표지에 「판매개시 2026. 7. 13 · 판매버전 3.0」** 이라 적혀 있다.
    5세대 시행일이 2026-05-06 이므로 이 차이가 세대를 한 단계 바꾼다.

    ★내가 앞서 이 차이를 「상품 판매개시 ≠ 판본 효력일」이라며 통과시켰다.
      문서가 「판매개시」라고 **명시한** 날짜를 다른 것으로 바꿔 읽은 것이다.

★고친 자국을 남긴다

    `date_source="cover_page"` · `date_source_note` 에 원문 표기를 적는다.
    나중에 재수집하면 사이트 값으로 덮일 수 있는데, **덮였다는 사실을
    알 수 있어야** 한다. 출처를 안 남기면 어느 쪽이 맞는지 아무도 모른다.

    ★이건 매니페스트(수집 기록)에 사람의 판단을 섞는 것이라 조심해야 한다.
      그래서 **문서가 명시한 값만** 넣고, 추정치는 절대 넣지 않는다.

쓰는 법:
    python -m scripts.crawl.apply_document_dates --dry-run
    python -m scripts.crawl.apply_document_dates --apply
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
_EVIDENCE = _ROOT / "data" / "exports" / "generation_evidence.json"


def _candidates() -> list[dict]:
    """문서가 판매개시일을 명시했고 매니페스트와 **다른** 것."""
    if not _EVIDENCE.exists():
        raise SystemExit(
            "근거 파일이 없습니다. 먼저: python -m scripts.confirm.scan_generation_evidence")
    data = json.loads(_EVIDENCE.read_text(encoding="utf-8"))
    out = []
    for x in data.get("items") or []:
        dates = x.get("doc_sale_dates") or []
        #: ★날짜가 **하나로 특정될 때만** 고친다. 여러 개면 무엇이 판매개시인지 모른다.
        if len(dates) != 1:
            continue
        doc = dates[0]
        man = (x.get("manifest_sale_start") or "").strip()
        if not man or man == "00000000" or doc == man:
            continue
        out.append({"sha256": x["sha256"], "insurer": x["insurer"],
                    "product_name": x["product_name"],
                    "manifest_sale_start": man, "document_sale_start": doc,
                    "evidence": x.get("evidence", "")})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 매니페스트를 고친다")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if not a.apply:
        a.dry_run = True

    cands = _candidates()
    by_sha = {c["sha256"]: c for c in cands}
    print(f"문서가 판매개시일을 명시했고 매니페스트와 다른 것: {len(cands)}건\n")
    for c in cands:
        print(f"  {c['insurer']:<10} {c['manifest_sale_start']} → ★{c['document_sale_start']}"
              f"  {c['product_name'][:40]}")
    if a.dry_run:
        print("\n--dry-run 이라 고치지 않았습니다. 반영하려면 --apply")
        return 0

    changed = 0
    for m in sorted(_MANIFESTS.glob("*.jsonl")):
        lines = m.read_text(encoding="utf-8").splitlines()
        out, touched = [], False
        for line in lines:
            if not line.strip():
                out.append(line)
                continue
            r = json.loads(line)
            c = by_sha.get(r.get("sha256"))
            if c and (r.get("sale_start") or "") == c["manifest_sale_start"]:
                r["sale_start"] = c["document_sale_start"]
                #: ★출처를 남긴다. 남기지 않으면 다음 사람이 사이트 값과 어느 쪽이
                #:   맞는지 판단할 근거가 없다.
                r["date_source"] = "cover_page"
                r["date_confidence"] = "exact"
                r["date_source_note"] = (
                    f"표지 판매개시 표기({c['document_sale_start']}) 반영 · "
                    f"수집 당시 사이트 값 {c['manifest_sale_start']}")
                touched = True
                changed += 1
            out.append(json.dumps(r, ensure_ascii=False))
        if touched:
            m.write_text("\n".join(out) + "\n", encoding="utf-8")
            print(f"  갱신 {m.name}")

    print(f"\n매니페스트 {changed}건 갱신")
    print("★세대는 여기서 쓰지 않는다 — `python -m scripts.crawl.set_generation` 이")
    print("  `sale_start` 로 다시 계산한다. 그게 한 곳에서만 정해지는 방식이다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
