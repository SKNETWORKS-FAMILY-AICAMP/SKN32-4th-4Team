"""옛 통합 매니페스트를 보험사별 매니페스트로 합친다 — 한 번만 쓰는 이관 스크립트.

★왜 갈라졌나 (실측, 2026-08-01)

    처음에는 전 보험사가 `data/raw/fetch_manifest.jsonl` **한 파일**을 같이 썼다.
    나중에 보험사별로 쪼갰는데(`split_manifest.py`), **어댑터 4개를 안 고쳤다.**
      dbins · myangel · nhlife · samsungfire

    그래서 이 넷은 계속 옛 통합 파일에 쓰고, 그 파일을 읽어 "이미 받았다"를 판정했다.
    진행률은 보험사별 파일을 보므로 **서로 다른 파일을 본 것**이다.

    증상: 삼성화재 어댑터는 "409건 전부 받았다"는데
          보험사별 매니페스트는 386행이고 URL 은 245개뿐이었다.
          실제로는 통합 파일에 409행·406 URL 이 멀쩡히 있었다.

★어느 행을 남기나 — **sha 단위로** 통합본을 우선한다

    처음엔 `(sha, 상품명, 개시일)` 로 합쳤는데 **행이 부풀었다**(DB손보 454+462 → 658).
    복구본은 상품명·개시일이 비어 있어서 통합본과 **다른 키**로 잡혔기 때문이다.
    같은 파일인데 두 벌이 남는다.

    실측해 보니 관계가 분명했다.

        samsungfire  통합 409행/307sha · 현재 386행/307sha · 통합에 없는 sha **0**
        dbins        통합 462행/258sha · 현재 454행/258sha · 통합에 없는 sha **0**
        myangel      통합  10행/ 10sha · 현재  14행/ 13sha · 통합에 없는 sha **3**

    즉 통합본이 그 sha 들에 대해 **더 완전하다**(URL 을 전부 갖고 있다).
    그래서 규칙은:

        - 통합본에 있는 sha  → **통합본 행으로 대체**한다(현재 행은 버린다)
        - 통합본에 없는 sha  → 현재 행을 **그대로 지킨다**(동양생명 3건이 여기 해당)

    "현재 행을 버린다"가 걱정될 수 있으나, 버리는 것은 **같은 파일에 대한
    정보가 더 적은 기록**이다. 파일 자체는 건드리지 않는다.

실행:
    python -m scripts.crawl.merge_legacy_manifest --dry-run
    python -m scripts.crawl.merge_legacy_manifest
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_LEGACY = _ROOT / "data" / "raw" / "fetch_manifest.jsonl"
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"

#: 통합 파일의 `insurer` 는 한글 이름이다. 슬러그로 옮긴다.
KO_TO_SLUG = {
    "삼성화재": "samsungfire",
    "DB손해보험": "dbins",
    "동양생명": "myangel",
    "NH농협생명": "nhlife",
}


def _norm(s: str) -> str:
    return re.sub(r"[\s·∙・()]+", "", s or "")


def _key(r: dict) -> tuple[str, str, str]:
    return (
        r.get("sha256", ""),
        _norm(r.get("product_name") or ""),
        (r.get("sale_start") or "").strip(),
    )


def _score(r: dict) -> tuple[int, int, int]:
    """정보량. 클수록 좋은 행이다."""
    return (
        1 if (r.get("url") or "").strip() else 0,
        1 if (r.get("sale_start") or "").strip() else 0,
        1 if (r.get("product_name") or "").strip() else 0,
    )


def _rows(p: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not _LEGACY.exists():
        print(f"옛 통합 매니페스트가 없습니다: {_LEGACY} (이미 이관됐을 수 있습니다.)")
        return
    if not _MANIFESTS.exists():
        raise InfraError(f"매니페스트 폴더가 없습니다: {_MANIFESTS}")

    by_slug: dict[str, list[dict]] = {}
    unknown = 0
    for r in _rows(_LEGACY):
        slug = KO_TO_SLUG.get(r.get("insurer", ""))
        if not slug:
            unknown += 1
            continue
        by_slug.setdefault(slug, []).append(r)

    if unknown:
        #: ★모르는 보험사는 **버리지 않는다.** 조용히 사라지면 안 된다.
        raise InfraError(
            f"통합 매니페스트에 슬러그를 모르는 보험사 행이 {unknown}개 있습니다. "
            "KO_TO_SLUG 에 추가하세요."
        )

    for slug, legacy_rows in sorted(by_slug.items()):
        target = _MANIFESTS / f"{slug}.jsonl"
        cur = _rows(target) if target.exists() else []

        legacy_shas = {r.get("sha256", "") for r in legacy_rows}
        #: 통합본에 없는 sha 의 현재 행만 지킨다. 나머지는 통합본이 대신한다.
        kept = [r for r in cur if r.get("sha256", "") not in legacy_shas]
        merged = legacy_rows + kept

        #: ★같은 파일에 대한 행이 통합본 안에서 또 겹칠 수 있다. 한 번 더 줄인다.
        best: dict[tuple[str, str, str], dict] = {}
        order: list[tuple[str, str, str]] = []
        for r in merged:
            k = _key(r)
            if k not in best:
                best[k] = r
                order.append(k)
            elif _score(r) > _score(best[k]):
                best[k] = r
        merged = [best[k] for k in order]

        print(f"  {slug:<15} 통합 {len(legacy_rows):>4} + 현재만있음 {len(kept):>3} → {len(merged):>4}행"
              f"   (현재 {len(cur)}행 중 {len(cur) - len(kept)}행은 통합본이 대체)")
        if not args.dry_run:
            tmp = target.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for r in merged:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            tmp.replace(target)

    if args.dry_run:
        print("\n(dry-run: 아무것도 쓰지 않았습니다.)")
        return

    #: ★지우지 않고 이름만 바꾼다. 되돌릴 수 없는 일은 하지 않는다.
    retired = _LEGACY.with_name("fetch_manifest.jsonl.merged")
    _LEGACY.rename(retired)
    print(f"\n옛 통합 파일은 {retired.name} 로 남겨 두었습니다(지우지 않았습니다).")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
