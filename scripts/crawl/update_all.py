"""약관 수집 주기 실행기 — 새 버전이 나오면 그것만 받는다.

프로젝트에서 주기적으로 호출해 최신 약관을 따라가기 위한 진입점이다.
사이트별 어댑터는 `scripts/crawl/sites/` 에 남아 있고, 이 파일이 그것들을 묶어 돌린다.

★증분이 되는 원리

    새 약관 버전이 나오면 공시 카탈로그에 **새 행**이 생기고 **새 파일명(=새 URL)** 이 붙는다.
    이미 받은 URL 은 `fetch_manifest.jsonl` 에 남아 있으므로 건너뛴다.
    → 매번 전량을 다시 받지 않고 **새로 생긴 것만** 받는다.

★이 방식의 한계 (정직 기록)

    **같은 URL 의 내용이 바뀌면 놓친다.** 위 규칙은 "URL 이 바뀌면 새 문서"를 가정한다.
    보험사가 같은 파일명으로 내용을 갈아끼우면 우리는 옛 버전을 계속 들고 있게 된다.
    그래서 `--recheck` 로 기존 URL 을 다시 받아 **sha256 을 비교**하는 모드를 둔다.
    바뀌었으면 옛 파일을 덮지 않고 **새 Artifact 로 추가**한다(이력이 사라지면 안 된다).

실행:
    python -m scripts.crawl.update_all --dry-run       # 무엇이 새로 생겼는지만 확인
    python -m scripts.crawl.update_all                 # 새 버전만 수집
    python -m scripts.crawl.update_all --recheck 30    # 기존 URL 30건을 재확인(내용 변경 탐지)
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import date, datetime, timezone
from pathlib import Path

from app.core.errors import InfraError

from scripts.crawl.sites import dbins, samsungfire

_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _ROOT / "data" / "raw" / "fetch_manifest.jsonl"
_OUT = _ROOT / "docs" / "reports"

#: 디스크 여유가 이보다 적으면 시작하지 않는다.
#: 디스크가 가득 찬 상태로 받으면 잘린 파일이 정상처럼 쌓인다(실제로 겪었다).
MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024  # 2GB

#: 등록된 어댑터. 새 보험사를 추가하면 여기에 넣는다.
ADAPTERS = {
    "samsungfire": samsungfire,
    "dbins": dbins,
}


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _fetched_urls() -> set[str]:
    if not _MANIFEST.exists():
        return set()
    return {
        json.loads(line)["url"]
        for line in _MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _plan_samsungfire() -> list[tuple[object, str, str]]:
    items = samsungfire.fetch_catalog()
    return [(it, path, samsungfire.PDF_BASE + path) for it, path in samsungfire.select_all_silson(items)]


def _plan_dbins() -> list[tuple[object, str, str]]:
    items = [i for i in dbins.collect_catalog() if not i.is_travel]
    return [(it, fn, dbins.pdf_url(fn)) for it in items for _, fn in it.files]


PLANNERS = {"samsungfire": _plan_samsungfire, "dbins": _plan_dbins}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="받지 않고 새로 생긴 것만 보고")
    ap.add_argument("--only", help="어댑터 하나만 실행(samsungfire/dbins)")
    ap.add_argument("--delay", type=float, default=0.8)
    ap.add_argument("--limit", type=int, default=0, help="이번 실행에서 받을 최대 건수(0=제한 없음)")
    ap.add_argument("--recheck", type=int, default=0, help="기존 URL 재확인 건수(내용 변경 탐지)")
    args = ap.parse_args()

    free = _free_bytes(_ROOT)
    print(f"디스크 여유: {free / 1024**3:.1f}GB")
    if not args.dry_run and free < MIN_FREE_BYTES:
        raise InfraError(
            f"디스크 여유가 부족합니다({free / 1024**3:.1f}GB < "
            f"{MIN_FREE_BYTES / 1024**3:.0f}GB). 가득 찬 상태로 받으면 잘린 파일이 정상처럼 쌓입니다."
        )

    done = _fetched_urls()
    names = [args.only] if args.only else list(ADAPTERS)
    summary: list[dict[str, object]] = []
    stamp = date.today().isoformat()

    for name in names:
        if name not in ADAPTERS:
            raise InfraError(f"등록되지 않은 어댑터입니다: {name}")
        mod = ADAPTERS[name]
        print(f"\n── {name} ──")
        plan = PLANNERS[name]()
        fresh = [(it, ref, url) for it, ref, url in plan if url not in done]
        print(f"  카탈로그 대상 {len(plan)}건 / 이미 받음 {len(plan) - len(fresh)}건 / ★신규 {len(fresh)}건")
        summary.append({"adapter": name, "total": len(plan), "new": len(fresh)})

        if args.dry_run:
            for _, ref, _u in fresh[:5]:
                print(f"    NEW {ref}")
            continue

        jobs = fresh[: args.limit] if args.limit else fresh
        ok = fail = 0
        records = []
        for n, (item, ref, url) in enumerate(jobs):
            if n:
                time.sleep(args.delay)
            if _free_bytes(_ROOT) < MIN_FREE_BYTES // 4:
                print("  ★디스크 여유 소진 — 중단합니다(잘린 파일을 만들지 않기 위해).")
                break
            try:
                rec = (
                    mod.download(item, ref)
                    if name == "samsungfire"
                    else mod.download(item, "policy_terms", ref)
                )
                records.append(rec)
                ok += 1
            except Exception as e:  # noqa: BLE001
                fail += 1
                if fail <= 3:
                    print(f"    [FAIL] {ref[:40]}: {e}")
        if records:
            from dataclasses import asdict

            with _MANIFEST.open("a", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        print(f"  수집 성공 {ok} / 실패 {fail}")
        summary[-1] |= {"fetched": ok, "failed": fail}

    _OUT.mkdir(parents=True, exist_ok=True)
    (_OUT / f"{stamp}_수집_업데이트.json").write_text(
        json.dumps(
            {
                "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "dry_run": args.dry_run,
                "free_gb": round(free / 1024**3, 1),
                "adapters": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n요약 저장: docs/reports/{stamp}_수집_업데이트.json")
    if args.dry_run:
        print("(dry-run: 아무것도 받지 않았습니다.)")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
