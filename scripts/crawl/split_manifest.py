"""수집 매니페스트를 보험사별 파일로 나눈다.

    data/raw/fetch_manifest.jsonl                  (이전: 한 파일)
    data/raw/manifests/<슬러그>.jsonl               (이후: 보험사별)

★왜 나누나

    9개사를 동시에 돌리면 **같은 파일에 동시 append** 가 일어나 줄이 섞일 수 있다.
    보험사별로 나누면 각 어댑터가 자기 파일에만 쓰므로 충돌이 없어진다.

★왜 조인이 안 깨지나

    읽을 때는 `glob("*.jsonl")` 로 **전부** 읽는다. 목록을 손으로 관리하지 않으므로
    "하나 빠뜨림"이 생기지 않는다.

★이것으로 해결되지 **않는** 것

    디스크가 가득 찼을 때의 기록 유실은 나눠도 막지 못한다(실제로 377건을 잃었다).
    그건 원자적 쓰기 문제이고, **PostgreSQL 로 옮길 때 해결된다**(P0).
    이 분리는 그때까지의 임시 조치다 — SQLite 같은 중간 단계를 새로 만들지 않는다.

실행:
    python -m scripts.crawl.split_manifest --dry-run
    python -m scripts.crawl.split_manifest
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from app.core.errors import InfraError, ValidationErr

from scripts.crawl.migrate_to_insurer_dirs import INSURER_SLUG

_ROOT = Path(__file__).resolve().parents[2]
_LEGACY = _ROOT / "data" / "raw" / "fetch_manifest.jsonl"
_DIR = _ROOT / "data" / "raw" / "manifests"


def manifest_path(insurer: str) -> Path:
    slug = INSURER_SLUG.get(insurer)
    if not slug:
        raise ValidationErr(
            f"보험사 '{insurer}' 의 슬러그가 등록되지 않았습니다(임의 생성하지 않습니다)."
        )
    return _DIR / f"{slug}.jsonl"


def load_all() -> list[dict]:
    """모든 보험사 기록을 읽는다. **glob 이라 빠뜨릴 수 없다.**"""
    out: list[dict] = []
    if _DIR.exists():
        for p in sorted(_DIR.glob("*.jsonl")):
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(json.loads(line))
    #: 아직 안 나뉜 옛 파일도 함께 읽는다(전환 중 유실 방지).
    if _LEGACY.exists():
        for line in _LEGACY.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def fetched_urls() -> set[str]:
    return {r["url"] for r in load_all()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not _LEGACY.exists():
        raise InfraError(f"나눌 매니페스트가 없습니다: {_LEGACY}")

    records = [
        json.loads(line)
        for line in _LEGACY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        grouped[r["insurer"]].append(r)

    print(f"기존 {len(records)}행 → 보험사 {len(grouped)}곳")
    for ins, rows in sorted(grouped.items(), key=lambda x: -len(x[1])):
        print(f"  {ins}: {len(rows)}행 → {manifest_path(ins).name}")

    if args.dry_run:
        print("\n(dry-run: 아무것도 쓰지 않았습니다.)")
        return

    _DIR.mkdir(parents=True, exist_ok=True)
    for ins, rows in grouped.items():
        path = manifest_path(ins)
        #: 임시파일에 쓰고 rename — 중간에 끊겨도 반쪽 파일이 남지 않는다.
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp.replace(path)

    #: 옛 파일은 지우지 않고 이름만 바꿔 둔다. 되돌릴 수 있어야 한다.
    _LEGACY.replace(_LEGACY.with_suffix(".jsonl.migrated"))
    total = sum(len(v) for v in grouped.values())
    print(f"\n분리 완료: {total}행 → {_DIR.relative_to(_ROOT)}/*.jsonl")
    print(f"옛 파일은 {_LEGACY.name}.migrated 로 남겨 두었습니다(되돌릴 수 있게).")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
