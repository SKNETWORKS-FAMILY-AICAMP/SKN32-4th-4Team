"""상품명에 잘못 붙은 날짜 접두어를 뗀다.

★어쩌다 붙었나

    기록이 유실됐을 때 **파일명으로 복구**했다(`rebuild_manifest.py`).
    그런데 일부 보험사는 파일명이 `20240909_(무)흥국생명 실손의료비보험(갱신형).pdf`
    처럼 **날짜_상품명** 꼴이라, 그 전체를 상품명에 그대로 넣었다.

    실측: 흥국생명 3 · 현대해상 27 · KB손보 4 · 롯데손보 1 = 35행.

★왜 고쳐야 하나

    커버리지 대조가 `(상품명, 판매개시일)` 로 맞춘다.
    상품명에 날짜가 붙어 있으면 **같은 상품이 다른 상품으로 보인다.**
    실제로 흥국생명이 "3건 누락"으로 나왔는데, 파일은 멀쩡히 있었다.

★세 경우를 나눠 다룬다 (실측해 보니 상황이 달랐다)

    (a) `sale_start` 와 접두어 날짜가 **같다**   → 접두어만 뗀다. 정보 손실 없음.
        흥국생명 3건.

    (b) `sale_start` 가 **비어 있다**            → 접두어 날짜를 `sale_start` 로 옮기고 뗀다.
        현대해상 27건. 접두어가 그 날짜의 **유일한 출처**이므로 옮기면 정보 이득이다.
        옮긴 행에는 `enriched=true` 를 남긴다.

    (c) `sale_start` 가 있고 접두어와 **다르다** → 이름만 정리하고 날짜는 **건드리지 않는다.**
        KB손보 4 · 롯데손보 1. 예: 접두어 20150724 / sale_start 20150624.
        접두어는 **파일 개정일**, `sale_start` 는 **판매개시일**로 뜻이 다를 수 있다.
        판단할 근거가 없으므로 접두어를 `file_date` 에 남겨 **버리지 않는다.**

★KB손보는 접두어가 하나가 아니다

    파일명이 `{날짜}_{상품코드}_{슬롯}_{상품명}` 꼴이다.
      `20150724_15325_1_KB해외장기체류실손의료비보험`
    날짜만 떼면 `15325_1_...` 가 남는다. 코드·슬롯도 떼고
    `product_code` 가 비어 있으면 거기에 채운다.

실행:
    python -m scripts.crawl.fix_name_date_prefix --dry-run
    python -m scripts.crawl.fix_name_date_prefix
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"

_PREFIX = re.compile(r"^(\d{8})_(.+)$")
#: KB손보 `{상품코드}_{슬롯}_{상품명}` 잔여분.
_CODE_SLOT = re.compile(r"^(\d{3,6})_(\d)_(.+)$")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not _MANIFESTS.exists():
        raise InfraError(f"매니페스트 폴더가 없습니다: {_MANIFESTS}")

    n_same = n_filled = n_kept = 0
    for m in sorted(_MANIFESTS.glob("*.jsonl")):
        rows = [
            json.loads(line)
            for line in m.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        n = 0
        for r in rows:
            mm = _PREFIX.match(r.get("product_name") or "")
            if not mm:
                continue
            prefix_date, rest = mm.group(1), mm.group(2)
            sale = (r.get("sale_start") or "").strip()

            if sale == prefix_date:
                n_same += 1
            elif not sale:
                #: (b) 접두어가 판매개시일의 유일한 출처다. 옮긴다.
                r["sale_start"] = prefix_date
                r["enriched"] = True
                n_filled += 1
            else:
                #: (c) 뜻이 다를 수 있다. 날짜는 그대로 두고 접두어를 따로 남긴다.
                r["file_date"] = prefix_date
                n_kept += 1

            #: KB손보 꼴이면 코드·슬롯도 뗀다.
            cs = _CODE_SLOT.match(rest)
            if cs:
                rest = cs.group(3)
                if not (r.get("product_code") or "").strip():
                    r["product_code"] = cs.group(1)
            r["product_name"] = rest
            n += 1

        if not n:
            continue
        print(f"  {m.stem:<15} {n:>3}행 정리")
        if not args.dry_run:
            tmp = m.with_suffix(".jsonl.tmp")
            tmp.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                encoding="utf-8",
            )
            tmp.replace(m)

    print(
        f"\n합계 {n_same + n_filled + n_kept}행 정리"
        f"  (개시일과 같아 뗌 {n_same} / 개시일이 비어 채움 {n_filled} / "
        f"개시일과 달라 file_date 로 보존 {n_kept})"
    )
    if args.dry_run:
        print("(dry-run: 아무것도 쓰지 않았습니다.)")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
