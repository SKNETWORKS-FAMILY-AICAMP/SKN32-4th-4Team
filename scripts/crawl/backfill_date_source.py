"""판매시점의 **출처를 기록**한다 — 값을 만들어 내지 않는다.

★무엇이 문제였나

    매니페스트 1,367건(제외사유 없는 고유 sha) 중 **1,098건에 `date_confidence` 가 없다.**
    `usable_for_judgment` 가 그걸 「모른다」로 보고 막아, 판정 가능 약관이
    132건(9.7%)에 머물렀다.

    그런데 그 1,098건 중 **805건은 `sale_start` 도 있고 세대도 나와 있다**
    (`generation_confidence` 가 전부 `exact`). 날짜를 모르는 게 아니라
    **어디서 왔는지를 안 적어 둔 것**이다.

★왜 그렇게 됐나 — 수집기가 두 세대다 (실측 2026-08-04)

    나중에 만든 모듈(`nhlife`·`samsunglife`)만 `date_confidence`·`date_source`·
    `inferred` 를 쓴다. 먼저 만든 모듈들은 `source_filename` 만 쓴다.
    **신뢰도가 낮은 게 아니라 스키마가 다르다.**

★그래서 무엇을 채우나 — **관측한 사실만**

    각 수집기가 `sale_start` 를 어디서 읽었는지 코드로 확인했다. 전부
    **보험사 자신의 상품목록**(API 필드 또는 표 열)이다.

        samsungfire    saleStDt            (API)
        dbins          SALE_BEGIN_DAY      (API)
        hyundaimarine  slStDt              (API)
        heungkukfire   목록 표 4번째 열
        heungkuklife   목록 표 판매개시 열
        myangel        목록 표 판매개시 열
        kbinsure       목록 표 1번째 열
        lotteins       목록 요청의 startdate

    그래서 `date_source="site_list"` 로 적는다. 이건 **추정이 아니라 관측**이다 —
    어느 필드에서 읽었는지까지 `date_source_field` 에 남긴다.

    ★값은 **이미 쓰이던 어휘에 맞춘다.** 매니페스트에 이런 것들이 이미 있다 —
      `site_list`(40) · `url_path`(173) · `표지 판매월`(46) · `관리번호`(121) ·
      `상품명 코드`(23) · `부칙 시행일`(11) · `준법감시인 확인일`(5).
      새 이름을 만들면 같은 뜻이 두 낱말로 갈린다.
      (CLAUDE.md §1 은 `"site"` 라고 적어 두었지만 **실제 어휘는 더 잘다.** 문서가 낡았다.)

★이미 있는 도구와 무엇이 다른가 — **겹치지 않는다**

    `backfill_dates.py`      삼성생명 목록 화면을 **다시 방문해** 빈 날짜를 채운다
    `fill_dates_from_url.py` 삼성생명 URL 경로에 박힌 날짜를 뽑아 채운다
    이 파일                   **이미 저장된** 날짜의 **출처를 기록**한다(네트워크 없음)

    앞의 둘은 「값이 없어서」 채우고, 이건 「값은 있는데 출처가 없어서」 적는다.

★★그래도 **맞다고 보장하지 않는다**

    실측 2026-08-04 — 메리츠화재는 목록이 `20260501` 인데 약관 표지에
    「판매개시 2026. 7. 13」 이라 적혀 있다. **목록도 틀린다.**
    그래서 이 백필은 「확정」이 아니라 **확정 심사의 입구를 열어 주는 것**이다.
    실제 확정은 `scripts.confirm.identify_documents` 가 문서와 대조해 정한다.

★건드리지 않는 것

    · 이미 `date_confidence` 가 있는 행 — 덮지 않는다
    · `sale_start` 가 없거나 `00000000` 인 행 — 채울 근거가 없다
    · 아래 표에 없는 수집기 — **추측하지 않고 건너뛰며 센다**

쓰는 법:
    python -m scripts.crawl.backfill_date_source --dry-run
    python -m scripts.crawl.backfill_date_source --apply
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"

#: 매니페스트 파일명(= 수집기 모듈명) → `sale_start` 를 읽은 자리.
#: ★여기 없는 파일은 **건너뛴다.** 모르는 수집기의 출처를 지어내지 않는다.
_FIELD_BY_MODULE = {
    "samsungfire": "목록 API `saleStDt`",
    "dbins": "목록 API `SALE_BEGIN_DAY`",
    "hyundaimarine": "목록 API `slStDt`",
    "heungkukfire": "목록 표 4번째 열",
    "heungkuklife": "목록 표 판매개시 열",
    "myangel": "목록 표 판매개시 열",
    "kbinsure": "목록 표 1번째 열",
    "lotteins": "목록 요청 `startdate`",
}


def _valid(s: str | None) -> bool:
    s = (s or "").strip()
    return len(s) == 8 and s.isdigit() and s != "00000000"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="매니페스트에 쓴다")
    ap.add_argument("--dry-run", action="store_true", help="세기만 한다")
    a = ap.parse_args(argv)
    if not (a.apply or a.dry_run):
        raise SystemExit("★`--apply` 또는 `--dry-run` 중 하나를 고르세요.")

    stat: collections.Counter = collections.Counter()
    per_module: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    for m in sorted(_MANIFESTS.glob("*.jsonl")):
        module = m.stem
        field = _FIELD_BY_MODULE.get(module)
        lines = m.read_text(encoding="utf-8").splitlines()
        out: list[str] = []
        changed = 0
        for line in lines:
            if not line.strip():
                out.append(line)
                continue
            r = json.loads(line)
            why = None
            if field is None:
                why = "수집기 미등록(출처를 모른다)"
            elif (r.get("date_confidence") or "").strip():
                why = "이미 라벨 있음(덮지 않음)"
            elif (r.get("excluded_reason") or "").strip():
                why = "판정 제외 문서"
            elif not _valid(r.get("sale_start")):
                why = "sale_start 가 없거나 자리표시자"
            if why:
                per_module[module][why] += 1
                stat[why] += 1
                out.append(line)
                continue
            #: ★**출처만 적는다.** 날짜 자체는 손대지 않는다.
            r["date_source"] = "site_list"
            r["date_source_field"] = field
            #: 목록이 8자리 완전 날짜를 준다 → `exact`. ★「맞다」가 아니라
            #: 「출처가 그렇게 말했다」는 뜻이다. 대조는 확정 단계가 한다.
            r["date_confidence"] = "exact"
            r["date_backfilled_by"] = "scripts.crawl.backfill_date_source"
            per_module[module]["★출처 기록함"] += 1
            stat["★출처 기록함"] += 1
            changed += 1
            out.append(json.dumps(r, ensure_ascii=False))
        if a.apply and changed:
            m.write_text("\n".join(out) + "\n", encoding="utf-8")

    print("수집기별:")
    for mod in sorted(per_module):
        parts = " · ".join(f"{k} {v}" for k, v in sorted(per_module[mod].items()))
        print(f"  {mod:<14} {parts}")
    print("\n합계:")
    for k, v in sorted(stat.items(), key=lambda x: -x[1]):
        print(f"  {k:<26} {v:>5}")
    print("\n" + ("적용했습니다." if a.apply else "★dry-run — 쓰지 않았습니다."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
