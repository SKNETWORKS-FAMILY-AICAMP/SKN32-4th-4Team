"""수집 원문을 보험사별 폴더로 재배치한다.

    data/raw/insurance_terms/<파일>              (이전: 평평한 한 폴더)
    data/raw/insurance_terms/<슬러그>/<파일>      (이후: 보험사별)

★이 재배치는 **사람이 찾기 쉬우라고** 하는 것이지 정합성 때문이 아니다.
파일 정체성은 이미 sha256 이고 파일명 앞에 붙어 있어 충돌은 원래 나지 않는다.
보험사가 13곳으로 늘면 한 폴더에 수천 개가 쌓여 사람이 못 찾는 것이 진짜 이유다.

폴더명은 **영문 슬러그**를 쓴다. 한글 폴더명은 Windows/git 에서 인코딩이 깨진다.

★매니페스트의 `saved_as` 를 함께 고친다. 안 고치면 식별 파이프라인이 파일을 못 찾는다.
파일만 옮기고 기록을 안 고치면 조용히 "추출 실패"가 되어 원인을 찾기 어려워진다.

실행:
    python -m scripts.crawl.migrate_to_insurer_dirs --dry-run
    python -m scripts.crawl.migrate_to_insurer_dirs
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from app.core.errors import InfraError, ValidationErr

_ROOT = Path(__file__).resolve().parents[2]
_RAW = _ROOT / "data" / "raw" / "insurance_terms"
_MANIFEST = _ROOT / "data" / "raw" / "fetch_manifest.jsonl"

#: 보험사명 → 폴더 슬러그. 새 보험사를 추가하면 여기에도 넣는다.
#: ★매핑에 없는 보험사가 나오면 임의로 만들지 않고 실패시킨다(무폴백).
INSURER_SLUG: dict[str, str] = {
    "삼성화재": "samsungfire",
    "메리츠화재": "meritzfire",
    "DB손해보험": "dbins",
    "현대해상": "hyundaimarine",
    "KB손해보험": "kbinsure",
    "한화손해보험": "hanwhageneral",
    "흥국화재": "heungkukfire",
    "롯데손해보험": "lotteins",
    "NH농협손해보험": "nhfire",
    "삼성생명": "samsunglife",
    "한화생명": "hanwhalife",
    "교보생명": "kyobolife",
    "흥국생명": "heungkuklife",
    "DB생명": "dblife",
    "동양생명": "myangel",
    "NH농협생명": "nhlife",
}


def slug_for(insurer: str) -> str:
    slug = INSURER_SLUG.get(insurer)
    if not slug:
        raise ValidationErr(
            f"보험사 '{insurer}' 의 폴더 슬러그가 등록되지 않았습니다. "
            "INSURER_SLUG 에 추가하세요(임의 생성하지 않습니다)."
        )
    return slug


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="옮기지 않고 계획만 출력")
    args = ap.parse_args()

    if not _MANIFEST.exists():
        raise InfraError(f"매니페스트가 없습니다: {_MANIFEST}")

    records = [
        json.loads(line)
        for line in _MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    #: 같은 파일이 여러 행에 나올 수 있으므로 실제 이동은 한 번만 한다.
    moves: dict[Path, Path] = {}
    updated = 0
    for r in records:
        old_rel = r["saved_as"]
        old = _ROOT / old_rel
        name = Path(old_rel).name
        new_rel = f"data/raw/insurance_terms/{slug_for(r['insurer'])}/{name}"
        if old_rel == new_rel:
            continue
        moves[old] = _ROOT / new_rel
        r["saved_as"] = new_rel
        updated += 1

    print(f"매니페스트 {len(records)}행 중 경로 갱신 대상 {updated}행")
    print(f"실제 이동 대상 파일 {len(moves)}개")

    #: 매니페스트에 없는데 폴더에 있는 파일(예: 수집 중 중단분)을 조용히 두고 가지 않는다.
    known = {p.name for p in moves}
    orphans = [p for p in _RAW.glob("*.pdf") if p.name not in known]
    if orphans:
        print(f"[!] 매니페스트에 없는 파일 {len(orphans)}개 - 옮기지 않고 그대로 둡니다.")
        for p in orphans[:5]:
            print(f"   {p.name}")

    if args.dry_run:
        for old, new in list(moves.items())[:5]:
            print(f"  {old.name} → {new.relative_to(_ROOT)}")
        print("\n(dry-run: 아무것도 옮기지 않았습니다.)")
        return

    moved = skipped = 0
    #: 원본도 대상도 없는 기록. 비약관이라 의도적으로 지운 파일이 여기 해당한다.
    #: 조용히 넘기지 않고 개수를 보고한다 — '지웠다'와 '유실됐다'는 다르다.
    gone: list[str] = []
    for old, new in moves.items():
        if not old.exists():
            if new.exists():
                skipped += 1
                continue
            gone.append(old.name)
            continue
        new.parent.mkdir(parents=True, exist_ok=True)
        if new.exists():
            old.unlink()  # 같은 sha 파일이 이미 있으면 중복이다
            skipped += 1
            continue
        shutil.move(str(old), str(new))
        moved += 1

    with _MANIFEST.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n이동 {moved} / 이미 존재하여 건너뜀 {skipped}")
    print(f"매니페스트 갱신 완료: {_MANIFEST.relative_to(_ROOT)}")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
