"""원격 상자에서 **페이지 JSON 만 만든다.**

    python remote_pages.py --jobs 0

★4단계(PDF → 페이지 JSON)는 좌표 표 복원이 들어오면서 비싸졌다.
  실측(2026-08-03, 이 노트북 직렬): 분당 3.4건 → 전량 **약 6.7시간**.
  PDF 하나하나가 독립이라 두 기계로 나누면 그만큼 준다.

★★**PyMuPDF 판을 맞춰야 한다.** 산출물 경로에 추출기 판이 박혀 있다
  (`s5_pymupdf-1.28.0`). 원격이 1.27.2 인 채로 돌리면 같은 폴더에
  **다른 추출기 결과가 섞인다.** 그래서 시작할 때 판을 확인하고, 다르면 멈춘다.

★DB 도 매니페스트도 원격에 두지 않는다. 들어오는 것은 PDF, 나가는 것은 페이지 JSON 이다.

기대하는 원격 배치:
    C:\\pagejob\\jobs.json          이 상자가 맡을 목록(매니페스트 항목 그대로)
    C:\\pagejob\\scripts\\...       to_page_json.py · table_coords.py
    C:\\pagejob\\data\\raw\\...     PDF (매니페스트의 saved_as 경로 그대로)
    C:\\pagejob\\out\\{slug}\\{sha12}.json    ← 결과
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

#: ★작업 뿌리는 `scripts/extract/` 의 **두 단계 위**다. 처음에 부모로 잡아
#:   `jobs.json` 을 못 찾았다.
BASE = Path(__file__).resolve().parents[2]
EXPECT_PYMUPDF = "1.28.0"


def _one(job: dict) -> dict:
    from scripts.extract import to_page_json

    sha12 = job["sha256"][:12]
    #: ★매니페스트의 `saved_as` 는 구분자가 섞여 있다(`/` 와 `\` 둘 다).
    #:   그대로 쓰면 한 건이 조용히 "PDF 없음"이 된다 — 실제로 그랬다.
    rel = job["saved_as"].replace("\\\\", "/").replace("\\", "/")
    #: ★보험사 폴더는 `_slug` 로 따로 받는다. PDF 는 `pdfs_flat/{sha12}.pdf` 로
    #:   **평평하게** 오기 때문이다 — 한글 파일명이 tar 왕복에서 깨져
    #:   511건 중 379건이 "PDF 없음"이 된 뒤로 이렇게 바꿨다.
    slug = job.get("_slug") or Path(rel).parent.name
    pdf = BASE / rel
    out = BASE / "out" / slug / f"{sha12}.json"
    if out.exists():
        return {"sha": sha12, "ok": 0, "skip": 1, "why": ""}
    if not pdf.exists():
        return {"sha": sha12, "ok": 0, "skip": 0, "why": f"PDF 없음: {rel}"}
    try:
        doc = to_page_json.extract(pdf, job)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        tmp.replace(out)          # 원자적 — 반쪽 파일을 남기지 않는다
        return {"sha": sha12, "ok": 1, "skip": 0, "why": ""}
    except Exception as e:  # noqa: BLE001
        return {"sha": sha12, "ok": 0, "skip": 0,
                "why": f"{type(e).__name__}: {e}", "trace": traceback.format_exc(limit=3)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=0, help="0=코어 수")
    a = ap.parse_args()

    sys.path.insert(0, str(BASE))
    import fitz

    #: ★판이 다르면 **멈춘다.** 조용히 섞으면 나중에 어느 쪽이 만든 건지 못 가린다.
    if fitz.VersionBind != EXPECT_PYMUPDF:
        raise SystemExit(
            f"PyMuPDF {fitz.VersionBind} 입니다. {EXPECT_PYMUPDF} 이어야 합니다. "
            f"pip install pymupdf=={EXPECT_PYMUPDF}"
        )

    jobs = json.loads((BASE / "jobs.json").read_text(encoding="utf-8"))
    n = a.jobs or (os.cpu_count() or 1)
    print(f"{len(jobs)} docs / {n} procs / fitz {fitz.VersionBind}", flush=True)

    ok = skip = 0
    fails: list[dict] = []
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=n) as ex:
        futs = [ex.submit(_one, j) for j in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            ok += r["ok"]
            skip += r["skip"]
            if r["why"]:
                fails.append(r)
            if i % 25 == 0 or i == len(jobs):
                el = time.time() - t0
                eta = (len(jobs) - i) / (i / el) if el and i else 0
                print(f"  {i}/{len(jobs)} ok={ok} skip={skip} fail={len(fails)} "
                      f"eta={eta / 60:.0f}min", flush=True)

    #: ★실패를 파일로도 남긴다. 부모 기계가 나중에 대조해야 한다.
    (BASE / "failures.json").write_text(
        json.dumps(fails, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"done ok={ok} skip={skip} fail={len(fails)} {(time.time() - t0) / 60:.1f}min")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
