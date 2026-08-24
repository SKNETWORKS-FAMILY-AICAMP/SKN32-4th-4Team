"""전처리 일괄 실행 — 수집한 PDF 전부를 페이지 JSON → 조항 JSON 으로 만든다.

기존 `to_page_json` · `to_clauses` 는 **한 건씩** 다루는 도구다(파일럿용).
2,030건을 돌리려면 배치가 필요하다. 이 스크립트가 그 껍데기다.
**변환 로직은 하나도 복제하지 않는다** — 두 모듈의 함수를 그대로 부른다.

★설계 원칙

    1. **이미 있는 것은 건너뛴다.** 산출물 경로에 추출기 버전이 박혀 있어
       (`s1_pymupdf-1.28.0`) 버전이 바뀌면 자연히 새 경로에 쌓인다.
       덮어쓰지 않으므로 중단 후 재실행이 안전하다.

    2. **한 건의 실패로 전체가 죽지 않는다.** 실패는 모아서 끝에 보고한다.
       조용히 넘기지 않는다 — 몇 건이 왜 실패했는지 반드시 찍는다.

    3. **진행률을 보여 준다.** 2,030건은 오래 걸린다. 어디까지 왔는지 모르면
       죽었는지 도는지 알 수 없다.

    4. **결과를 요약 파일로 남긴다.** 나중에 "무엇이 왜 빠졌나"를 다시 셀 수 있게.

실행:
    python -m scripts.extract.run_all --dry-run          # 대상만 센다
    python -m scripts.extract.run_all                    # 전량
    python -m scripts.extract.run_all --insurer dbins    # 한 보험사만
    python -m scripts.extract.run_all --limit 20         # 맛보기
    python -m scripts.extract.run_all --stage pages      # 4단계만
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from datetime import datetime
from pathlib import Path

from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_EXTRACTED = _ROOT / "data" / "extracted"
_STRUCTURED = _ROOT / "data" / "structured"
_REPORT = _ROOT / "docs" / "reports"


def _targets(insurer: str | None, limit: int) -> list[dict]:
    """매니페스트에서 sha 단위로 대상을 뽑는다.

    ★행이 아니라 **파일(sha)** 단위다. 같은 약관이 여러 상품에 붙으면
      행은 여럿이지만 **PDF 는 한 벌**이고, 추출도 한 번만 하면 된다.
    """
    from scripts.crawl.split_manifest import load_all

    records = load_all()
    if not records:
        raise InfraError("수집 기록이 없습니다(data/raw/manifests/*.jsonl).")

    by_sha: dict[str, dict] = {}
    for r in records:
        sha = r.get("sha256", "")
        if not sha:
            continue
        #: ★판정 대상이 아닌 문서는 전처리하지 않는다.
        #:   사업방법서·여행실손 180건을 `data/raw/excluded/` 로 옮겨 놨다.
        #:   여기서 걸러야 산출물에 섞이지 않는다(`classify_documents.py`).
        if (r.get("excluded_reason") or "").strip():
            continue
        if insurer and f"/{insurer}/" not in r.get("saved_as", "").replace("\\", "/"):
            continue
        #: 같은 sha 가 여러 행이면 **정보가 많은 행**을 대표로 쓴다.
        prev = by_sha.get(sha)
        if prev is None or _info(r) > _info(prev):
            by_sha[sha] = r
    out = sorted(by_sha.values(), key=lambda r: r.get("saved_as", ""))
    return out[:limit] if limit else out


def _info(r: dict) -> tuple[int, int, int]:
    return (
        1 if (r.get("product_name") or "").strip() else 0,
        1 if (r.get("sale_start") or "").strip() else 0,
        1 if (r.get("url") or "").strip() else 0,
    )


def _write_atomic(path: Path, doc: dict) -> None:
    """임시 파일에 다 쓴 뒤 바꿔 넣는다.

    ★왜 — 실패가 **0바이트 파일**을 남겼다.

        `path.write_text(...)` 는 파일을 먼저 비우고 쓴다. 직렬화 중에 예외가 나면
        **빈 파일이 남는다.** 실제로 현대해상 1건이 서로게이트 때문에 죽으면서
        0바이트를 남겼고, 다음 단계가 그걸 읽어 `JSONDecodeError` 로 또 죽었다.
        한 건의 실패가 두 건이 된 것이다.

        더 나쁜 건, 다음 실행 때 그 0바이트 파일이 **"이미 있음"으로 판정돼
        영원히 건너뛰어진다**는 것이다. 조용히 빠진다.
    """
    #: 직렬화를 먼저 끝낸다 — 여기서 실패하면 파일에 손도 대지 않는다.
    body = json.dumps(doc, ensure_ascii=False, indent=1)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


def _bar(done: int, total: int, width: int = 30) -> str:
    filled = int(done / total * width) if total else 0
    return "█" * filled + "·" * (width - filled)


def _process_one(job: dict) -> dict:
    """문서 하나를 처리한다. **프로세스 경계를 넘으므로 순수 함수**여야 한다.

    ★예외를 여기서 삼키지 않는다 — 사유를 담아 돌려주고 부모가 센다.
      조용한 스킵을 만들지 않는다(CLAUDE.md §3).
    """
    import json as _json
    import traceback as _tb
    from pathlib import Path as _P

    from scripts.extract import to_clauses, to_page_json

    r = {"sha": job["sha12"], "slug": job["slug"],
         "pages": 0, "clauses": 0, "skip_pages": 0, "skip_clauses": 0, "failures": []}
    page_out = _P(job["page_out"])
    clause_out = _P(job["clause_out"])
    pdf = _P(job["pdf"])

    if job["stage"] in ("pages", "both"):
        if page_out.exists():
            r["skip_pages"] = 1
        elif not pdf.exists():
            r["failures"].append({"sha": job["sha12"], "slug": job["slug"], "stage": "pages",
                                  "why": f"PDF 없음: {job['pdf']}"})
        else:
            try:
                _write_atomic(page_out, to_page_json.extract(pdf, job["meta"]))
                r["pages"] = 1
            except Exception as e:  # noqa: BLE001
                r["failures"].append({"sha": job["sha12"], "slug": job["slug"], "stage": "pages",
                                      "why": f"{type(e).__name__}: {e}",
                                      "trace": _tb.format_exc(limit=3)})

    if job["stage"] in ("clauses", "both"):
        if clause_out.exists():
            r["skip_clauses"] = 1
        elif page_out.exists():
            try:
                built = to_clauses.build(_json.loads(page_out.read_text(encoding="utf-8")))
                _write_atomic(clause_out, built)
                r["clauses"] = 1
            except Exception as e:  # noqa: BLE001
                r["failures"].append({"sha": job["sha12"], "slug": job["slug"], "stage": "clauses",
                                      "why": f"{type(e).__name__}: {e}",
                                      "trace": _tb.format_exc(limit=3)})
    return r


def _run_parallel(targets, tag, clause_tag, stage, jobs):
    """프로세스 풀로 돌린다. 진행률은 완료 순서대로 찍는다."""
    import os
    from concurrent.futures import ProcessPoolExecutor, as_completed

    n = jobs or (os.cpu_count() or 1)
    plan = []
    for meta in targets:
        sha12 = meta["sha256"][:12]
        slug = Path(meta["saved_as"]).parent.name
        plan.append({
            "sha12": sha12, "slug": slug, "meta": meta, "stage": stage,
            "pdf": str(_ROOT / meta["saved_as"]),
            "page_out": str(_EXTRACTED / slug / tag / f"{sha12}.json"),
            "clause_out": str(_STRUCTURED / slug / clause_tag / f"{sha12}.clauses.json"),
        })
    print(f"  프로세스 {n}개로 나눠 돌립니다.", flush=True)

    agg = {"pages": 0, "clauses": 0, "skip_pages": 0, "skip_clauses": 0}
    failures: list[dict] = []
    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(_process_one, j) for j in plan]
        for f in as_completed(futs):
            r = f.result()
            for k in agg:
                agg[k] += r[k]
            failures.extend(r["failures"])
            done += 1
            if done % 25 == 0 or done == len(plan):
                el = time.time() - t0
                rate = done / el if el else 0
                eta = (len(plan) - done) / rate if rate else 0
                print(f"  {_bar(done, len(plan))} {done:>5}/{len(plan)}  "
                      f"쪽{agg['pages']:>5} 조항{agg['clauses']:>5} "
                      f"건너뜀{agg['skip_pages'] + agg['skip_clauses']:>5} "
                      f"실패{len(failures):>3}  남은시간 {eta / 60:.0f}분", flush=True)
    return agg, failures, time.time() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--insurer", help="보험사 슬러그 하나만")
    ap.add_argument("--limit", type=int, default=0, help="0=전량")
    ap.add_argument("--stage", choices=("pages", "clauses", "both"), default="both")
    ap.add_argument("--dry-run", action="store_true")
    #: ★★**좌표 표 복원이 들어오면서 4단계가 느려졌다.**
    #:   실측(2026-08-03): 직렬로 분당 3.4건 → 전량 **약 6.7시간**.
    #:   PDF 하나하나가 독립이라 프로세스로 나누면 코어 수만큼 빨라진다.
    #:   ★스레드가 아니라 **프로세스**다. fitz 는 CPU 를 쓰므로 GIL 에 막힌다.
    ap.add_argument("--jobs", type=int, default=1,
                    help="동시 프로세스 수. 0=코어 수")
    args = ap.parse_args()

    from scripts.extract import to_clauses, to_page_json

    tag = to_page_json._version_tag()
    #: ★조항 산출물은 **조항 스키마 버전**으로 나눈다. 추출기는 그대로인데
    #:   조항 로직만 바뀌는 일이 있다(v5). 같은 태그를 쓰면 이전 판을 덮어써
    #:   비교가 불가능해진다.
    clause_tag = to_clauses._version_tag()
    targets = _targets(args.insurer, args.limit)
    print(f"대상 {len(targets):,}건  (페이지 {tag} / 조항 {clause_tag})")

    if args.dry_run:
        need_p = sum(
            1
            for m in targets
            if not (_EXTRACTED / Path(m["saved_as"]).parent.name / tag
                    / f"{m['sha256'][:12]}.json").exists()
        )
        print(f"  페이지 JSON 없는 것 {need_p:,}건")
        print("(dry-run: 아무것도 쓰지 않았습니다.)")
        return

    if args.jobs != 1:
        agg, failures, el = _run_parallel(targets, tag, clause_tag, args.stage, args.jobs)
        n_pages, n_clauses = agg["pages"], agg["clauses"]
        skip_pages, skip_clauses = agg["skip_pages"], agg["skip_clauses"]
        _report(tag, clause_tag, targets, n_pages, n_clauses,
                skip_pages, skip_clauses, failures, el)
        return

    t0 = time.time()
    n_pages = n_clauses = 0
    skip_pages = skip_clauses = 0
    failures: list[dict] = []

    for i, meta in enumerate(targets, 1):
        sha12 = meta["sha256"][:12]
        slug = Path(meta["saved_as"]).parent.name
        pdf = _ROOT / meta["saved_as"]

        page_out = _EXTRACTED / slug / tag / f"{sha12}.json"
        clause_out = _STRUCTURED / slug / clause_tag / f"{sha12}.clauses.json"

        # ── 4단계: PDF → 페이지 JSON ──────────────────────────────
        if args.stage in ("pages", "both"):
            if page_out.exists():
                skip_pages += 1
            elif not pdf.exists():
                failures.append({"sha": sha12, "slug": slug, "stage": "pages",
                                 "why": f"PDF 없음: {meta['saved_as']}"})
            else:
                try:
                    doc = to_page_json.extract(pdf, meta)
                    _write_atomic(page_out, doc)
                    n_pages += 1
                except Exception as e:  # noqa: BLE001
                    failures.append({"sha": sha12, "slug": slug, "stage": "pages",
                                     "why": f"{type(e).__name__}: {e}",
                                     "trace": traceback.format_exc(limit=3)})

        # ── 5단계: 페이지 JSON → 조항 JSON ─────────────────────────
        if args.stage in ("clauses", "both"):
            if clause_out.exists():
                skip_clauses += 1
            elif not page_out.exists():
                pass  # 4단계가 실패했으면 이미 위에 기록됐다
            else:
                try:
                    built = to_clauses.build(
                        json.loads(page_out.read_text(encoding="utf-8"))
                    )
                    _write_atomic(clause_out, built)
                    n_clauses += 1
                except Exception as e:  # noqa: BLE001
                    failures.append({"sha": sha12, "slug": slug, "stage": "clauses",
                                     "why": f"{type(e).__name__}: {e}",
                                     "trace": traceback.format_exc(limit=3)})

        if i % 25 == 0 or i == len(targets):
            el = time.time() - t0
            rate = i / el if el else 0
            eta = (len(targets) - i) / rate if rate else 0
            print(
                f"  {_bar(i, len(targets))} {i:>5}/{len(targets)}  "
                f"쪽{n_pages:>5} 조항{n_clauses:>5} 건너뜀{skip_pages + skip_clauses:>5} "
                f"실패{len(failures):>3}  남은시간 {eta / 60:.0f}분",
                flush=True,
            )

    _report(tag, clause_tag, targets, n_pages, n_clauses,
            skip_pages, skip_clauses, failures, time.time() - t0)


def _report(tag, clause_tag, targets, n_pages, n_clauses,
            skip_pages, skip_clauses, failures, el):
    """실행 결과를 찍고 파일로 남긴다. **직렬·병렬이 같은 보고를 낸다.**"""
    print(f"\n완료  {el / 60:.1f}분")
    print(f"  페이지 JSON 새로 만듦 {n_pages:,} / 이미 있어 건너뜀 {skip_pages:,}")
    print(f"  조항 JSON  새로 만듦 {n_clauses:,} / 이미 있어 건너뜀 {skip_clauses:,}")
    print(f"  실패 {len(failures)}건")
    for f in failures[:10]:
        print(f"    [{f['stage']}] {f['slug']}/{f['sha']}: {f['why'][:80]}")

    #: ★결과를 남긴다. "몇 건이 왜 빠졌나"를 나중에 다시 셀 수 있어야 한다.
    _REPORT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    out = _REPORT / f"{stamp}_전처리_실행결과.json"
    out.write_text(
        json.dumps(
            {
                "extractor_tag": tag,
                "targets": len(targets),
                "pages_built": n_pages,
                "pages_skipped": skip_pages,
                "clauses_built": n_clauses,
                "clauses_skipped": skip_clauses,
                "elapsed_sec": round(el, 1),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"  → {out.relative_to(_ROOT)}")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
