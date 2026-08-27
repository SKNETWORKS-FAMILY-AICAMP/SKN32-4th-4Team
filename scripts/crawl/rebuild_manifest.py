"""수집 매니페스트 복구 — 디스크의 파일과 카탈로그를 조인해 기록을 되살린다.

★왜 필요한가

    디스크가 100% 찬 상태에서 수집이 돌면 **PDF 는 이미 써진 뒤 매니페스트 append 가 실패**한다.
    그러면 파일은 남고 기록만 사라진다. 실제로 388개 중 377개가 그렇게 됐다.

    기록이 없으면:
      - 식별 파이프라인이 그 파일들을 **보지 못한다**(매니페스트를 읽어 파일을 찾으므로)
      - 증분 수집이 '안 받은 것'으로 보고 **다시 받는다**(남의 서버에 중복 요청)
      - URL·상품명·판매기간이 사라진다(파일명만으로는 어느 상품인지 모른다)

★다시 받지 않는다

    파일명이 `{sha256앞12자}_{원본파일명}` 이고, 원본파일명이 카탈로그의 경로와 일치한다.
      삼성화재: `ZPB293020_0_20230101_file1.pdf`  ← 카탈로그 `pdf_paths` 의 파일명
      DB손보  : `10687_20160101_약관_국내여행 실손의료비보험.pdf` ← `INPL_FINM`
    따라서 **조인만 하면 복원된다.** sha256 은 파일을 다시 읽어 계산한다(파일명 접두사는 검증용).

★지어내지 않는다

    카탈로그에서 못 찾은 파일은 **추측해서 채우지 않고** `unmatched` 로 보고한다.
    복구된 행의 `recovered=true` 로 원래 기록과 구분한다.

실행:
    python -m scripts.crawl.rebuild_manifest --dry-run
    python -m scripts.crawl.rebuild_manifest
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_RAW = _ROOT / "data" / "raw" / "insurance_terms"
_MANIFEST = _ROOT / "data" / "raw" / "fetch_manifest.jsonl"
_CATALOG = _ROOT / "data" / "catalog"

SAMSUNG_BASE = "https://www.samsungfire.com"
DB_PDF = "https://www.idbins.com/cYakgwanDown.do?FilePath=InsProduct/"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_samsungfire_index() -> dict[str, dict[str, str]]:
    """파일명 → 상품 메타. 카탈로그 `pdf_paths` 에서 만든다."""
    src = sorted(_CATALOG.glob("*_samsungfire_products.jsonl"))
    if not src:
        return {}
    idx: dict[str, dict[str, str]] = {}
    for line in src[-1].read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        for p in r.get("pdf_paths", []):
            idx[p.rsplit("/", 1)[-1]] = {
                "insurer": r["insurer"],
                "url": SAMSUNG_BASE + p,
                "product_code": r.get("product_code", ""),
                "product_name": r.get("product_name", ""),
                "sale_start": r.get("sale_start", ""),
                "sale_end": r.get("sale_end", ""),
            }
    return idx


def _load_dbins_index() -> dict[str, dict[str, str]]:
    src = sorted(_CATALOG.glob("*_dbins_products.jsonl"))
    if not src:
        return {}
    idx: dict[str, dict[str, str]] = {}
    for line in src[-1].read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        for _kind, fn in r.get("files", []):
            idx[fn] = {
                "insurer": r["insurer"],
                "url": DB_PDF + fn,
                "product_code": r.get("product_code", ""),
                "product_name": r.get("product_name", ""),
                "sale_start": r.get("sale_start", ""),
                "sale_end": r.get("sale_end", ""),
            }
    return idx


def _original_name(filename: str) -> str:
    """`{sha12}_{원본}` 에서 원본 파일명을 떼어낸다."""
    head, _, rest = filename.partition("_")
    if len(head) == 12 and all(c in "0123456789abcdef" for c in head):
        return rest
    return filename


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not _RAW.exists():
        raise InfraError(f"수집 폴더가 없습니다: {_RAW}")

    existing = []
    known_files: set[str] = set()
    if _MANIFEST.exists():
        for line in _MANIFEST.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                existing.append(r)
                known_files.add(Path(r["saved_as"]).name)

    index = {**_load_samsungfire_index(), **_load_dbins_index()}
    print(f"카탈로그 색인 {len(index):,}건 / 기존 매니페스트 {len(existing)}행")

    files = sorted(_RAW.rglob("*.pdf"))
    orphans = [p for p in files if p.name not in known_files]
    print(f"디스크 {len(files)}개 / 기록 없는 파일 {len(orphans)}개")

    recovered: list[dict[str, object]] = []
    unmatched: list[str] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for p in orphans:
        meta = index.get(_original_name(p.name))
        if not meta:
            unmatched.append(p.name)
            continue
        if args.dry_run:
            recovered.append({"saved_as": str(p.relative_to(_ROOT))})
            continue
        digest = _sha256(p)
        if not p.name.startswith(digest[:12]):
            # 파일명 접두사와 실제 해시가 다르면 파일이 바뀐 것이다. 조용히 넘기지 않는다.
            unmatched.append(f"{p.name} (해시 불일치: 실제 {digest[:12]})")
            continue
        recovered.append(
            {
                "insurer": meta["insurer"],
                "url": meta["url"],
                "http_status": 200,
                "content_type": "application/pdf",
                "bytes": p.stat().st_size,
                "sha256": digest,
                #: 원래 수집 시각은 알 수 없다. 복구 시각을 넣고 그 사실을 표시한다.
                "fetched_at": now,
                "saved_as": str(p.relative_to(_ROOT)).replace("\\", "/"),
                "product_code": meta["product_code"],
                "product_name": meta["product_name"],
                "sale_start": meta["sale_start"],
                "sale_end": meta["sale_end"],
                "identification": "unidentified",
                #: ★원래 기록과 구분한다. 수집 시각이 실제 시각이 아니다.
                "recovered": True,
            }
        )

    print(f"\n복구 가능 {len(recovered)}개 / 카탈로그에서 못 찾음 {len(unmatched)}개")
    for n in unmatched[:5]:
        print(f"  [미매칭] {n}")

    if args.dry_run:
        print("\n(dry-run: 매니페스트를 고치지 않았습니다.)")
        return

    with _MANIFEST.open("a", encoding="utf-8") as f:
        for r in recovered:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    total = len(existing) + len(recovered)
    print(f"\n매니페스트 {len(existing)} → {total}행")
    if unmatched:
        print(f"★{len(unmatched)}개는 지어내지 않고 남겨 두었습니다. 별도 확인이 필요합니다.")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
