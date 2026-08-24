"""같은 내용(sha256)의 PDF 를 한 벌만 남긴다 — Artifact / Occurrence 분리의 실현.

★왜 중복이 생기나 (실수가 아니다)

    같은 약관 PDF 가 **여러 상품·여러 판매기간**에 붙어 있는 것은 자연스럽다.
    예: DB손보 454개 파일 중 고유 내용은 258개, 삼성화재 324개 중 245개.
    보험사가 개정 없이 판매기간만 연장하면 같은 파일이 여러 행에 걸린다.

★우리 설계가 이미 이걸 위한 것이다

    Artifact(sha256)  = 문서 실체. **하나만 있으면 된다**
    SourceOccurrence  = "언제 어디서 받았나". **여러 개일 수 있다**

    지금은 파일명이 달라 같은 내용이 여러 벌 저장돼 있다.
    → 파일은 **한 벌만** 남기고, 매니페스트 행은 **전부 유지**하되
      `saved_as` 를 남은 파일로 맞춘다.

★기록을 지우지 않는다

    "이 상품의 이 판매기간에 이 약관이 붙어 있었다"는 사실은 각각 다른 정보다.
    파일이 같다고 그 사실을 지우면 **버전 매칭을 할 수 없게 된다.**

실행:
    python -m scripts.crawl.dedupe_artifacts --dry-run
    python -m scripts.crawl.dedupe_artifacts
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path

from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_RAW = _ROOT / "data" / "raw" / "insurance_terms"
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not _RAW.exists():
        raise InfraError(f"수집 폴더가 없습니다: {_RAW}")

    #: sha → 파일들. 이름순으로 정렬해 **가장 짧은 이름**을 대표로 남긴다
    #: (원본에 가까운 이름일 확률이 높고, 재실행해도 같은 것이 뽑힌다).
    by_sha: dict[str, list[Path]] = collections.defaultdict(list)
    for pdf in sorted(_RAW.rglob("*.pdf")):
        by_sha[_sha256(pdf)].append(pdf)

    dupes = {k: v for k, v in by_sha.items() if len(v) > 1}
    n_extra = sum(len(v) - 1 for v in dupes.values())
    freed = sum(p.stat().st_size for v in dupes.values() for p in v[1:])
    print(f"파일 {sum(len(v) for v in by_sha.values())}개 / 고유 내용 {len(by_sha)}개")
    print(f"중복 {len(dupes)}쌍, 여분 {n_extra}개 ({freed / 1e9:.2f}GB)")

    if args.dry_run:
        for sha, paths in list(dupes.items())[:5]:
            keep = min(paths, key=lambda p: (len(p.name), p.name))
            print(f"  {sha[:10]}: 유지 {keep.name[:40]}")
            for p in paths:
                if p != keep:
                    print(f"            제거 {p.name[:40]}")
        print("\n(dry-run: 아무것도 지우지 않았습니다.)")
        return

    #: 제거될 경로 → 남을 경로
    remap: dict[str, str] = {}
    removed = 0
    for paths in dupes.values():
        keep = min(paths, key=lambda p: (len(p.name), p.name))
        keep_rel = str(keep.relative_to(_ROOT)).replace("\\", "/")
        for p in paths:
            if p == keep:
                continue
            remap[str(p.relative_to(_ROOT)).replace("\\", "/")] = keep_rel
            p.unlink()
            removed += 1

    #: ★매니페스트 행은 **지우지 않는다**. `saved_as` 만 남은 파일로 고친다.
    #: "이 상품의 이 기간에 이 약관이 붙어 있었다"는 사실은 각각 다른 정보다.
    fixed = 0
    for m in sorted(_MANIFESTS.glob("*.jsonl")):
        rows = [json.loads(l) for l in m.read_text(encoding="utf-8").splitlines() if l.strip()]
        changed = False
        for r in rows:
            tgt = remap.get(r.get("saved_as", ""))
            if tgt:
                r["saved_as"] = tgt
                fixed += 1
                changed = True
        if changed:
            tmp = m.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            tmp.replace(m)

    print(f"\n여분 파일 {removed}개 삭제 / 매니페스트 경로 {fixed}행 정정")
    print(f"남은 파일 {len(list(_RAW.rglob('*.pdf')))}개")
    print("★기록은 지우지 않았다 — 같은 약관이 여러 상품·기간에 붙은 사실은 각각 다른 정보다.")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
