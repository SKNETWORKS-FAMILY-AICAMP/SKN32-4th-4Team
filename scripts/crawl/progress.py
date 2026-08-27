"""수집 진행률 — 지금 어디까지 왔는지 한 화면에 보여준다.

카탈로그(대상)와 매니페스트(수집분)를 대조해 **보험사별 진행률**을 낸다.
백그라운드 수집이 도는 중에 아무 때나 불러도 된다.

★대상 건수의 출처

    `data/catalog/<날짜>_<슬러그>_products.jsonl` — 각 어댑터가 카탈로그 조회 때 저장한 것.
    사이트를 다시 조회하지 않으므로 **빠르고 남의 서버에 부하가 없다.**
    (사이트와 실제로 맞춰 보려면 `verify_coverage` 를 쓴다 — 그건 재조회한다.)

★카탈로그가 없는 곳

    브라우저 수집분(삼성생명·메리츠화재·NH농협손보)은 카탈로그 파일이 없다.
    대상 건수를 **알려진 값**으로 적어 두고 그 사실을 표시한다.

실행:
    python -m scripts.crawl.progress
    python -m scripts.crawl.progress --watch 30   # 30초마다 갱신
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_RAW = _ROOT / "data" / "raw" / "insurance_terms"
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
_CATALOG = _ROOT / "data" / "catalog"

INSURER_KO = {
    "samsungfire": "삼성화재", "dbins": "DB손보", "hyundaimarine": "현대해상",
    "heungkukfire": "흥국화재", "meritzfire": "메리츠화재", "kbinsure": "KB손보",
    "lotteins": "롯데손보", "nhlife": "NH농협생명", "samsunglife": "삼성생명",
    "myangel": "동양생명", "heungkuklife": "흥국생명", "nhfire": "NH농협손보",
}

#: ★대상 건수는 **어댑터가 실제로 쓰는 필터**를 거친 값이어야 한다.
#: 카탈로그를 내가 따로 세면 어댑터와 달라진다 — 실제로 그렇게 해서
#: 삼성화재가 74%(341/458)로 보였는데 어댑터 기준으로는 409/409 전량 완료였다.
#: 그래서 어댑터가 로그에 남긴 **확정 대상 수**를 여기 적는다.
#: (근거: 각 어댑터 실행 로그의 "대상 N건" 줄)
KNOWN_TARGET = {
    #: ★어댑터 로그의 "409건"은 **작업 건수**다. 그중 서로 다른 URL 은 406개다
    #:   (같은 PDF 가 상품 3건에 겹쳐 붙어 있다 — 실측). 문서 수는 406 이 맞다.
    "samsungfire": 406,
    "dbins": 462,           # "여행 제외 462건"
    "heungkukfire": 222,    # "약관으로 보이는 것 222건"
    "hyundaimarine": 310,   # 카탈로그 여행 제외
    "kbinsure": 116,
    "lotteins": 93,
    "nhlife": 52,
    "myangel": 10,
    "heungkuklife": 18,
    "samsunglife": 223,     # 화면 검색 '실손'
    "meritzfire": 157,      # 화면 검색형 '실손'
    "nhfire": 12,           # 단독실손 12개 상품
}


#: ★카탈로그는 **전체 상품**을 담는다. 실손만 세야 대상 건수가 맞다.
#: (처음에 필터를 안 걸어 삼성화재 대상이 9,404 로 나왔다 — 실제 실손은 406 이다.)
_SILSON = re.compile(r"실손|의료비|노후실손|유병력자")
_TRAVEL = re.compile(r"해외여행|국내여행|여행자")


def _target(slug: str) -> tuple[int, str]:
    """(실손 대상 건수, 출처)."""
    if slug in KNOWN_TARGET:
        return KNOWN_TARGET[slug], "어댑터"
    hits = sorted(_CATALOG.glob(f"*_{slug}_products.jsonl"))
    if not hits:
        return 0, "없음"
    n = 0
    for line in hits[-1].read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        #: 어댑터마다 필드명이 다르다. 상품명이 담길 만한 곳을 모두 본다.
        name = " ".join(
            str(r.get(k) or "")
            for k in ("product_name", "ttlNm", "prodNm", "file_name", "original_name")
        )
        if isinstance(r.get("product"), dict):
            name += " " + str(r["product"].get("product_name") or "")
        #: 삼성화재는 상품명 없이 경로만 있는 행이 있다 -> 경로로 판단할 수 없으니 센다.
        if not name.strip():
            n += 1
            continue
        if _SILSON.search(name) and not _TRAVEL.search(name):
            n += 1
    return n, "카탈로그"


def _bar(pct: float, width: int = 22) -> str:
    filled = int(round(pct / 100 * width))
    return "█" * filled + "·" * (width - filled)


def show() -> None:
    print(f"\n수집 진행률  ({datetime.now().strftime('%H:%M:%S')})")
    print("─" * 74)
    print(f"{'보험사':<12}{'대상':>6}{'수집':>6}{'파일':>6}  {'진행':<24}{'%':>5}  출처")
    print("─" * 74)
    t_all = c_all = f_all = 0
    for slug in INSURER_KO:
        target, src = _target(slug)
        mf = _MANIFESTS / f"{slug}.jsonl"
        got = sum(1 for line in mf.read_text(encoding="utf-8").splitlines() if line.strip()) if mf.exists() else 0
        d = _RAW / slug
        files = len(list(d.glob("*.pdf"))) if d.exists() else 0
        pct = min(got / target * 100, 100) if target else 0.0
        t_all += target
        c_all += got
        f_all += files
        mark = "✓" if target and got >= target else " "
        print(f"{INSURER_KO[slug]:<12}{target:>6}{got:>6}{files:>6}  {_bar(pct):<24}{pct:>4.0f}%{mark} {src}")
    print("─" * 74)
    pct = min(c_all / t_all * 100, 100) if t_all else 0.0
    size = sum(p.stat().st_size for p in _RAW.rglob("*.pdf")) / 1e9 if _RAW.exists() else 0
    print(f"{'합계':<12}{t_all:>6}{c_all:>6}{f_all:>6}  {_bar(pct):<24}{pct:>4.0f}%  {size:.2f}GB")
    print("\n※ '파일'이 '수집'보다 적은 것은 정상이다 — 같은 약관이 여러 상품·기간에 붙으면")
    print("   파일은 한 벌만 두고(Artifact) 기록은 각각 남긴다(Occurrence).")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=int, default=0, help="N초마다 갱신(0=한 번만)")
    args = ap.parse_args()
    if not args.watch:
        show()
        return
    try:
        while True:
            show()
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\n중단")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
