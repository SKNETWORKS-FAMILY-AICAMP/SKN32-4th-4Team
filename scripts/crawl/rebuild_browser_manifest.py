"""브라우저 수집분 매니페스트 복구 — 파일은 있는데 기록이 없는 것을 되살린다.

★왜 생겼나

    브라우저 수집기가 **배치 끝에 몰아서** 기록을 썼다. 배치 중간에 드라이버가 끊기면
    **파일은 이미 저장됐는데 기록만 사라진다.** 실제로 삼성생명이 파일 563개 / 기록 44행이 됐다.
    (`browser_collector.py` 는 이제 한 건마다 즉시 기록하도록 고쳤다.)

★무엇을 되살릴 수 있고 무엇은 못 하나

    파일명이 `{sha256앞12자}_{상품명}.pdf` 이므로 **보험사·sha·경로·상품명**은 복원된다.
    그러나 **원본 URL 은 파일명에 없다.** 브라우저 클릭으로 받았기 때문이다.

    → URL 을 **지어내지 않는다.** `url` 을 빈 값으로 두고 `url_unknown=true` 로 표시한다.
      그러면 증분 수집이 이 건을 "안 받은 것"으로 보고 다시 받을 수 있는데,
      그건 sha 중복으로 걸러지므로 파일이 늘지는 않는다.
      **모르는 것을 안다고 적는 것보다 낫다.**

실행:
    python -m scripts.crawl.rebuild_browser_manifest --dry-run
    python -m scripts.crawl.rebuild_browser_manifest
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.errors import InfraError

from scripts.crawl.migrate_to_insurer_dirs import INSURER_SLUG

_ROOT = Path(__file__).resolve().parents[2]
_RAW = _ROOT / "data" / "raw" / "insurance_terms"
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"

#: 슬러그 → 보험사명 (역방향).
_BY_SLUG = {v: k for k, v in INSURER_SLUG.items()}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _known_files() -> set[str]:
    out: set[str] = set()
    if not _MANIFESTS.exists():
        return out
    for p in _MANIFESTS.glob("*.jsonl"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.add(Path(json.loads(line)["saved_as"]).name)
    return out


def _parse_name(filename: str) -> tuple[str, str]:
    """`{sha12}_{상품명}.pdf` 에서 (sha12, 상품명) 을 뗀다."""
    stem = filename[:-4] if filename.lower().endswith(".pdf") else filename
    head, _, rest = stem.partition("_")
    if len(head) == 12 and all(c in "0123456789abcdef" for c in head):
        return head, rest
    return "", stem


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not _RAW.exists():
        raise InfraError(f"수집 폴더가 없습니다: {_RAW}")

    known = _known_files()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    by_slug: dict[str, list[dict]] = {}
    skipped_loose = 0

    for pdf in sorted(_RAW.rglob("*.pdf")):
        if pdf.name in known:
            continue
        slug = pdf.parent.name
        if slug == _RAW.name:
            #: 보험사 폴더 밖에 떨어진 파일. 어느 회사 것인지 알 수 없다.
            skipped_loose += 1
            continue
        insurer = _BY_SLUG.get(slug)
        if not insurer:
            raise InfraError(f"슬러그 '{slug}' 의 보험사명을 모릅니다(INSURER_SLUG 확인).")

        sha12, product = _parse_name(pdf.name)
        if args.dry_run:
            by_slug.setdefault(slug, []).append({"saved_as": str(pdf.relative_to(_ROOT))})
            continue

        digest = _sha256(pdf)
        if sha12 and not digest.startswith(sha12):
            raise InfraError(f"파일명 해시와 실제 해시가 다릅니다: {pdf.name}")

        by_slug.setdefault(slug, []).append(
            {
                "insurer": insurer,
                #: ★URL 을 모른다. 지어내지 않는다.
                "url": "",
                "url_unknown": True,
                "http_status": 200,
                "content_type": "application/pdf",
                "bytes": pdf.stat().st_size,
                "sha256": digest,
                "fetched_at": now,
                "saved_as": str(pdf.relative_to(_ROOT)).replace("\\", "/"),
                "product_code": "",
                "product_name": product,
                "sale_start": "",
                "sale_end": "",
                "filename_kind_hint": "policy_terms",
                "identification": "unidentified",
                "collector": "browser",
                "recovered": True,
            }
        )

    total = sum(len(v) for v in by_slug.values())
    print(f"기록 없는 파일 {total}개")
    for slug, rows in sorted(by_slug.items(), key=lambda x: -len(x[1])):
        print(f"  {slug}: {len(rows)}개")
    if skipped_loose:
        print(f"  [!] 보험사 폴더 밖 파일 {skipped_loose}개 - 소속을 알 수 없어 건너뜁니다")

    if args.dry_run:
        print("\n(dry-run: 아무것도 쓰지 않았습니다.)")
        return

    _MANIFESTS.mkdir(parents=True, exist_ok=True)
    for slug, rows in by_slug.items():
        with (_MANIFESTS / f"{slug}.jsonl").open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\n복구 완료: {total}행 추가")
    print("★url 은 빈 값이고 url_unknown=true 다. 모르는 것을 안다고 적지 않았다.")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
