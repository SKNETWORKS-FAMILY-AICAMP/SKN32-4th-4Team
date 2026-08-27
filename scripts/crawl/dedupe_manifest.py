"""매니페스트 중복 정리 — 같은 sha 에 여러 행이 쌓인 것을 하나로 줄인다.

★왜 생기나

    재수집·복구를 여러 번 돌리면서 **같은 파일에 대한 행이 여러 벌** 쌓였다.
      - 복구본(`recovered=true`): URL 도 판매기간도 없다
      - 원본: URL·판매기간이 있다

    실측: KB손보 기록 120 / 파일 116, 흥국화재 228 / 205, 동양생명 20 / 13.
    "수집이 대상보다 많다"는 이상한 숫자가 여기서 나왔다.

★어느 행을 남기나 — **정보가 가장 많은 행**

    1) URL 이 있는 것          (복구본은 URL 을 모른다)
    2) 판매개시일이 있는 것     (버전 매칭의 열쇠)
    3) 상품명이 있는 것
    4) 그래도 같으면 먼저 온 것

    ★단순히 "나중 것"을 남기면 안 된다. 복구본이 나중일 수 있고,
      그러면 URL·판매기간을 잃는다.

★같은 sha 라도 **서로 다른 상품**이면 남긴다

    같은 약관이 여러 상품에 붙는 것은 자연스럽고, 그 사실은 각각 다른 정보다.
    그래서 정체성은 `(sha256, 정규화 상품명, 판매개시일)` 로 본다.

실행:
    python -m scripts.crawl.dedupe_manifest --dry-run
    python -m scripts.crawl.dedupe_manifest
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"


def _norm(s: str) -> str:
    return re.sub(r"[\s·∙・()]+", "", s or "")


def _score(r: dict) -> tuple[int, int, int]:
    """정보량 점수. 클수록 좋은 행이다."""
    return (
        1 if (r.get("url") or "").strip() else 0,
        1 if (r.get("sale_start") or "").strip() else 0,
        1 if (r.get("product_name") or "").strip() else 0,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not _MANIFESTS.exists():
        raise InfraError(f"매니페스트 폴더가 없습니다: {_MANIFESTS}")

    t_before = t_after = 0
    for m in sorted(_MANIFESTS.glob("*.jsonl")):
        rows = [
            json.loads(line)
            for line in m.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        t_before += len(rows)

        best: dict[tuple[str, str, str], dict] = {}
        order: list[tuple[str, str, str]] = []
        for r in rows:
            key = (
                r.get("sha256", ""),
                _norm(r.get("product_name") or ""),
                (r.get("sale_start") or "").strip(),
            )
            prev = best.get(key)
            if prev is None:
                best[key] = r
                order.append(key)
            elif _score(r) > _score(prev):
                #: ★정보가 더 많은 행으로 교체. "나중 것"이 아니다.
                best[key] = r

        #: ★2단계 병합(sha+개시일만으로 합치기)은 **하지 않는다.**
        #: 한 번 넣었다가 DB손보 454 -> 262 로 **서로 다른 상품의 기록을 합쳐** 정보를 잃었다.
        #: 같은 약관이 여러 상품에 붙는 것은 자연스럽고 그 사실은 각각 다른 정보다.
        #: 상품명 표기 흔들림(`급여 실손 의료비` / `급여실손의료비`)은 `_norm` 이 흡수한다.
        merged = [best[k] for k in order]

        t_after += len(merged)
        if len(merged) != len(rows):
            print(f"  {m.stem:<15} {len(rows):>5} → {len(merged):>5}  (-{len(rows) - len(merged)})")
        if not args.dry_run and len(merged) != len(rows):
            tmp = m.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for r in merged:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            tmp.replace(m)

    print(f"\n합계 {t_before} → {t_after}행 (-{t_before - t_after})")
    if args.dry_run:
        print("(dry-run: 아무것도 쓰지 않았습니다.)")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
