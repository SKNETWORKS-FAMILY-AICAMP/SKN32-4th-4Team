"""PDF 안에 적힌 판매 시점을 매니페스트에 채운다.

★찾는 순서 — 강한 근거부터. 앞에서 찾으면 뒤는 보지 않는다.

    1. `판매월 2026.05` / `판매일 2026년 7월`    (표지에 대놓고 적혀 있다)  exact
    2. `시행일 2022.04.01` / `개정 2022.4.1`                              exact
    3. 준법감시인확인필 `…-LP-220324-01`         (준법감시 확인일)         month
    4. 관리번호 끝의 `YYMM`  `메리츠일반-특종/기타/기타A-16-2607A`        month
    5. 상품명·파일명의 `YYMM`  `…실손의료비보험2605`, `(범용_1601)`        month

★신호가 엇갈리면 앞 순서를 믿는다 — 근거가 있다

    실측으로 6건이 엇갈렸다.

        NH유병력자실손의료비보험(갱신형,무배당)_2101   표지 '판매월 2023.01'
        (무) 헤아림실손의료비보험2605                  표지 '판매일 2026년 7월'

    상품명의 `_2101` 은 **상품이 처음 나온 때**(버전 코드)이고,
    표지의 `판매월` 은 **이 약관 문서가 팔린 때**다.
    "가입일에 어느 약관이 적용되나"를 정하려면 **후자**가 맞다.

★등급을 붙인다 — 날짜를 안다고 다 같은 게 아니다

    exact : 문서에 날짜가 **적혀 있다**. 그대로 쓴다.
    month : **월까지만** 안다(코드에서 유도). 세대 판정에는 충분하지만
            "정확히 어느 개정판"에는 못 쓴다.
    (없음): 비워 둔다. 자동 판정에서 제외한다.

★쓰지 않기로 한 것들 — 왜 안 쓰는지 남긴다

    · PDF 메타 `CreationDate` — 100% 있지만 판매개시일이 아니다.
      둘 다 아는 250건 대조: ±7일 51%, ±30일 81%, **최대 오차 1,725일**.
      절반은 맞지만 지어내는 것과 다르지 않다.
    · 본문 속 날짜 — `2007.6.28`, `2014.1.1` 처럼 **법령 인용일**이 대부분이다.
    · 파일명 앞 12자 — sha 접두어다. `0501f6d7a695` 의 `0501` 을 날짜로 오인했었다.

실행:
    python -m scripts.crawl.fill_dates_from_pdf --dry-run
    python -m scripts.crawl.fill_dates_from_pdf
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

import fitz

from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"

#: 표지에 대놓고 적힌 판매 시점.
_SALE_MONTH = re.compile(r"판매[월일]\s*(20\d{2})\s*[.\-년]\s*(\d{1,2})")
#: 시행·개정일(일자까지).
_EFFECTIVE = re.compile(
    r"(?:시행|개정|적용)\s*일?\s*[:：]?\s*(20\d{2})\s*[.\-년]\s*(\d{1,2})\s*[.\-월]\s*(\d{1,2})"
)
#: 부칙의 시행 문구. `이 약관은 2021년 7월 1일부터 시행합니다.`
_ENFORCE = re.compile(
    r"(20[0-2]\d)\s*[.\-년]\s*(\d{1,2})\s*[.\-월]\s*(\d{1,2})\s*일?\s*(?:부터)?\s*시행"
)
#: 준법감시인확인필 `[장기상품]-[L2461]-LP-220324-01`.
_COMPLIANCE = re.compile(r"준법감시인[^\n]{0,60}?[-_](\d{2})(0[1-9]|1[0-2])(\d{2})[-_]")
#: 관리번호 끝의 YYMM. ★뒤에 `)` 가 붙는 경우가 있어 `(?!\d)` 로만 막는다.
#:   `[A-Z]?\s*$` 로 잡았더니 `…-2509C)` 를 106건 놓쳤다(실측).
_MGMT_NO = re.compile(r"메리츠[^\n]{0,60}?-(\d{2})(0[1-9]|1[0-2])[A-Z]?(?!\d)")
#: 상품명·파일명의 YYMM.
_NAME_YYMM = re.compile(r"(?<!\d)(\d{2})[.\-]?(0[1-9]|1[0-2])(?!\d)")
#: 파일명 앞의 sha12 접두어 — 여기서 날짜를 찾으면 안 된다.
_SHA_PREFIX = re.compile(r"^[0-9a-f]{12}_")

#: 몇 페이지까지 볼 것인가. 관리번호는 뒤쪽에 있는 일이 많아 전 페이지를 본다.
_HEAD_PAGES = 3


def _yy_ok(yy: str) -> bool:
    """`YY` 가 실손이 존재한 기간(2005~2027) 안인가."""
    return 5 <= int(yy) <= 27


def _read(pdf: Path) -> tuple[str, str]:
    """(앞부분 텍스트, 전체 텍스트)."""
    doc = fitz.open(str(pdf))
    try:
        head = "".join(doc[i].get_text() for i in range(min(_HEAD_PAGES, doc.page_count)))
        full = "".join(doc[i].get_text() for i in range(doc.page_count))
    finally:
        doc.close()
    return head, full


def guess_date(pdf: Path, name: str) -> tuple[str, str, str] | None:
    """`(YYYYMMDD 또는 YYYYMM, 등급, 근거)` 또는 None."""
    try:
        head, full = _read(pdf)
    except Exception:  # noqa: BLE001
        return None

    m = _SALE_MONTH.search(head)
    if m:
        #: ★★**「판매월」은 월까지만 아는 것이다 — `exact` 가 아니라 `month`.**
        #:
        #:   표지에 「판매월 2026.05」라고만 적혀 있으면 **1일인지 31일인지 모른다.**
        #:   그런데 `exact` 로 기록하면 `20260501` 이 확인된 날짜인 양 취급된다.
        #:
        #:   ★그 자리가 하필 **세대 경계**다. 5세대 시행일이 2026-05-06 이라
        #:     5월 상품은 1일이냐 6일 이후냐로 4세대·5세대가 갈린다.
        #:     실측 2026-08-05 — 46건이 이렇게 `exact` 로 적혀 있었고
        #:     그중 **7건이 세대 경계 달**(2017-04 · 2026-05)에 걸려 있었다.
        #:     NH농협생명·NH농협손해보험 건은 본문에 5세대 표지(비중증)가 있는데도
        #:     매니페스트는 4세대였다.
        #:
        #:   `month` 로 내면 `set_generation.py` 가 경계 달을 보고 `ambiguous` 를
        #:   붙인다 — 그게 「모르면 모른다고 한다」에 맞는 동작이다.
        return f"{m.group(1)}{int(m.group(2)):02d}01", "month", "표지 판매월"

    m = _EFFECTIVE.search(head)
    if m:
        return (
            f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}",
            "exact",
            "표지 시행일",
        )

    #: ★부칙의 시행 문구 — "이 약관은 2021년 7월 1일부터 시행합니다."
    #:   표지에 아무것도 없는 제도성 특약이 여기에 날짜를 갖고 있다(실측 11건).
    #:   **문서 전체**를 봐야 한다 — 부칙은 맨 뒤에 있다.
    m = _ENFORCE.search(full)
    if m:
        return (
            f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}",
            "exact",
            "부칙 시행일",
        )

    m = _COMPLIANCE.search(head)
    if m and _yy_ok(m.group(1)):
        return f"20{m.group(1)}{m.group(2)}{m.group(3)}", "month", "준법감시인 확인일"

    #: 관리번호는 뒤쪽 페이지에 있는 일이 많다. **문서 안에서 한 값으로 일관함을 확인했다**
    #: (메리츠 120건 중 값이 둘 이상인 문서 0건).
    m = _MGMT_NO.search(full)
    if m and _yy_ok(m.group(1)):
        return f"20{m.group(1)}{m.group(2)}01", "month", "관리번호"

    base = _SHA_PREFIX.sub("", name)
    cands = [x for x in _NAME_YYMM.finditer(base) if _yy_ok(x.group(1))]
    if cands:
        #: 버전 코드는 보통 이름 **뒤쪽**에 온다.
        last = cands[-1]
        return f"20{last.group(1)}{last.group(2)}01", "month", "상품명 코드"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", help="한 곳만")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not _MANIFESTS.exists():
        raise InfraError(f"매니페스트 폴더가 없습니다: {_MANIFESTS}")

    files = (
        [_MANIFESTS / f"{args.site}.jsonl"] if args.site else sorted(_MANIFESTS.glob("*.jsonl"))
    )
    total = collections.Counter()
    for m in files:
        if not m.exists():
            raise InfraError(f"매니페스트가 없습니다: {m}")
        rows = [
            json.loads(line)
            for line in m.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        need = [r for r in rows if not (r.get("sale_start") or "").strip()]
        if not need:
            continue

        #: 같은 파일(sha)을 여러 번 열지 않는다.
        cache: dict[str, tuple[str, str, str] | None] = {}
        by = collections.Counter()
        for r in need:
            sha = r.get("sha256", "")
            if sha not in cache:
                pdf = _ROOT / r.get("saved_as", "")
                cache[sha] = (
                    guess_date(pdf, Path(r.get("saved_as", "")).name)
                    if pdf.exists()
                    else None
                )
            got = cache[sha]
            if got is None:
                by["못 찾음"] += 1
                continue
            r["sale_start"], r["date_confidence"], r["date_source"] = got
            r["inferred"] = got[1] != "exact"
            by[f"{got[2]}({got[1]})"] += 1

        filled = sum(v for k, v in by.items() if k != "못 찾음")
        print(f"  {m.stem:<14} 없던 {len(need):>4} → 채움 {filled:>4} / 못 찾음 {by['못 찾음']:>3}")
        for k, v in by.most_common():
            if k != "못 찾음":
                print(f"       {k:<24}{v:>4}")
        total.update(by)

        if not args.dry_run and filled:
            tmp = m.with_suffix(".jsonl.tmp")
            tmp.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                encoding="utf-8",
            )
            tmp.replace(m)

    filled = sum(v for k, v in total.items() if k != "못 찾음")
    print(f"\n합계 채움 {filled} / 못 찾음 {total['못 찾음']}")
    if args.dry_run:
        print("(dry-run: 아무것도 쓰지 않았습니다.)")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
