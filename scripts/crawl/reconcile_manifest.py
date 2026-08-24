"""매니페스트 ↔ 실제 파일 정합 맞추기.

★왜 어긋났나 (실측 감사, 2026-08-01)

    1) **기록은 있는데 파일이 없다** — 삼성화재 17행.
       전부 `_file2` / `_file3` 이었다. 이건 사업방법서·상품요약서라
       "실수는 지워라"는 지시로 **파일만 지우고 기록을 안 지운 것**이다.
       → 행을 지운다.

    2) **파일은 있는데 기록이 없다** — 고아 PDF 78개
       (삼성화재 73 · 삼성생명 4 · 메리츠 1). 전부 약관 본문이다.
       기록이 두 번 유실됐고(디스크 풀 / 배치 중간 사망) 그 잔재다.
       → 파일명에서 되살릴 수 있는 것만 넣는다.

★지어내지 않는다

    고아 파일에서 확실한 것은 `sha256`·`bytes`·`saved_as`·`original_name` 뿐이다.
    URL 은 **모른다**. `url=""` 로 두고 `recovered=true` 를 박아
    "이 행은 파일에서 되살린 것"임을 남긴다.
    판매개시일은 파일명에 `_YYYYMMDD_` 가 있을 때만 넣고, 없으면 비운다.

실행:
    python -m scripts.crawl.reconcile_manifest --dry-run
    python -m scripts.crawl.reconcile_manifest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
_RAW = _ROOT / "data" / "raw" / "insurance_terms"

#: 파일명 `{sha12}_{원본이름}` 규칙. 앞 12자가 sha256 머리다.
_SHA12 = re.compile(r"^([0-9a-f]{12})_(.+)$")
_DATE = re.compile(r"_(\d{8})[_.]")


def _rows(p: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write(p: Path, rows: list[dict]) -> None:
    tmp = p.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(p)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not _MANIFESTS.exists():
        raise InfraError(f"매니페스트 폴더가 없습니다: {_MANIFESTS}")

    n_drop = n_add = 0
    for m in sorted(_MANIFESTS.glob("*.jsonl")):
        slug = m.stem
        d = _RAW / slug
        if not d.exists():
            continue
        rows = _rows(m)
        on_disk = {p.name: p for p in d.glob("*.pdf")}

        # ── 1) 파일이 없는 행을 뺀다 ────────────────────────────────
        keep, dropped = [], []
        for r in rows:
            name = Path(r.get("saved_as", "")).name
            (keep if name in on_disk else dropped).append(r)

        # ── 2) 기록이 없는 파일을 넣는다 ─────────────────────────────
        known = {r["sha256"][:12] for r in keep}
        added = []
        for name, p in sorted(on_disk.items()):
            mm = _SHA12.match(name)
            #: ★sha12 접두어가 없는 파일(브라우저 수집 초기분)은 sha 를 직접 계산한다.
            sha12 = mm.group(1) if mm else _sha256(p)[:12]
            if sha12 in known:
                continue
            orig = mm.group(2) if mm else name
            dm = _DATE.search(name)
            added.append(
                {
                    "insurer": slug,
                    "product_name": "",
                    "product_code": "",
                    "sale_start": dm.group(1) if dm else "",
                    "sale_end": "",
                    "url": "",  # ★모른다. 지어내지 않는다.
                    "saved_as": str(p.relative_to(_ROOT)),
                    "original_name": orig,
                    "sha256": _sha256(p),
                    "bytes": p.stat().st_size,
                    "content_type": "application/pdf",
                    "recovered": True,
                    "url_unknown": True,
                }
            )
            known.add(sha12)

        if not dropped and not added:
            continue
        n_drop += len(dropped)
        n_add += len(added)
        print(f"  {slug:<15} 행 {len(rows):>4} → {len(keep) + len(added):>4}   "
              f"(파일없어 뺌 -{len(dropped)} / 기록없어 넣음 +{len(added)})")
        if not args.dry_run:
            _write(m, keep + added)

    print(f"\n합계 -{n_drop}행 / +{n_add}행")
    if args.dry_run:
        print("(dry-run: 아무것도 쓰지 않았습니다.)")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
