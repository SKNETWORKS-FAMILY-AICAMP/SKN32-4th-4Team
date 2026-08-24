"""커버리지 대조 — 사이트가 공시한 실손 건수와 우리가 받은 것을 맞춰본다.

★왜 필요한가

    "1,817건을 받았다"는 사실이지만 **"받아야 할 것을 다 받았다"는 증명은 아니다.**
    사이트를 다시 조회해 대상 건수를 세고, 우리 매니페스트와 대조해야
    "빠진 것이 없다"고 말할 수 있다.

★무엇을 비교하나

    사이트 대상   각 어댑터의 카탈로그 조회를 다시 돌려 얻은 **약관 파일 수**
    우리 수집분   `data/raw/manifests/<슬러그>.jsonl` 의 행 수
    누락          사이트에는 있는데 우리 매니페스트에 URL 이 없는 것

★한계 (미리 밝힌다)

    - 브라우저 수집분(삼성생명·메리츠·NH농협손보)은 **URL 을 모른다**.
      URL 대조가 불가능하므로 **건수만** 비교한다.
    - 사이트가 그 사이 개정판을 올렸으면 대상이 늘어난다. 그건 누락이 아니다.
    - 실손 판별은 상품명 기준이다. 사이트의 분류와 다를 수 있다.

실행:
    python -m scripts.crawl.verify_coverage
    python -m scripts.crawl.verify_coverage --only kbinsure
"""

from __future__ import annotations

import argparse
import json
import re
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
_RAW = _ROOT / "data" / "raw" / "insurance_terms"
_OUT = _ROOT / "docs" / "reports"


def _norm(s: str) -> str:
    """상품명 정규화. ★공백·중점 처리가 파싱 시점마다 달라진다.

    실측: 같은 상품이 `급여 실손 의료비보장보험` / `급여실손의료비보장보험` 으로
    다르게 기록돼 URL·이름 대조가 전부 어긋났다.
    """
    return re.sub(r"[\s·∙・]+", "", s)


def _keys(rows: list[dict]) -> set[tuple[str, str]]:
    """대조 키: (정규화 상품명, 판매개시일). ★URL 은 쓰지 않는다.

    동양생명·흥국생명 등은 다운로드 URL 에 **세션마다 바뀌는 암호화 토큰**이 들어간다.
    URL 로 대조하면 같은 문서도 매번 '누락'으로 잡힌다(실측).
    """
    return {(_norm(r.get("product_name") or ""), (r.get("sale_start") or "")) for r in rows}


def _collected(slug: str) -> tuple[int, set[str]]:
    """우리가 받은 것: (기록 수, URL 집합)."""
    path = _MANIFESTS / f"{slug}.jsonl"
    if not path.exists():
        return 0, set()
    urls: set[str] = set()
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        n += 1
        u = json.loads(line).get("url") or ""
        if u:
            urls.add(u)
    return n, urls


def _files(slug: str) -> int:
    d = _RAW / slug
    return len(list(d.glob("*.pdf"))) if d.exists() else 0


# ── 사이트별 "대상 URL 집합"을 만드는 함수들 ───────────────────────────
# 각 어댑터의 카탈로그 조회를 그대로 재사용한다. 어댑터가 바뀌면 여기도 따라 바뀐다.


def _t_samsungfire() -> set[str]:
    from scripts.crawl.sites import samsungfire as m

    items = m.fetch_catalog()
    return {m.PDF_BASE + p for _it, p in m.select_all_silson(items)}


def _t_dbins() -> set[str]:
    from scripts.crawl.sites import dbins as m

    return {
        m.pdf_url(fn)
        for it in m.collect_catalog()
        if not it.is_travel
        for _k, fn in it.files
    }


def _t_nhlife() -> set[str]:
    from scripts.crawl.sites import nhlife as m

    out: set[str] = set()
    for it in m.fetch_catalog():
        if it.is_travel:
            continue
        d = m.fetch_files(it)
        slot = d.terms_slot()
        if slot:
            out.add(m.pdf_url(d.file_id, slot))
    return out


def _t_myangel() -> set[tuple[str, str]]:
    from scripts.crawl.sites import myangel as m

    return {
        (_norm(i.product_name), i.sale_start)
        for i in m.fetch_catalog()
        if i.looks_like_silson and not i.is_travel
    }


def _t_kbinsure() -> set[str]:
    from scripts.crawl.sites import kbinsure as m

    out: set[str] = set()
    for p in m.fetch_products():
        if not (p.looks_like_silson and not p.is_travel):
            continue
        out.update(t.url for t in m.fetch_terms_files(p))
    return out


def _t_hyundaimarine() -> set[str]:
    from scripts.crawl.sites import hyundaimarine as m

    out: set[str] = set()
    for it in m.fetch_catalog():
        if it.is_travel:
            continue
        try:
            out.add(m.pdf_url(m.file_meta(it.terms_file_id)))
        except InfraError:
            continue
    return out


def _t_heungkukfire() -> set[str]:
    from scripts.crawl.sites import heungkukfire as m

    return {f.url for f in m.fetch_catalog() if f.looks_like_terms and not f.is_travel}


def _t_lotteins() -> set[str]:
    from scripts.crawl.sites import lotteins as m

    out: set[str] = set()
    for p in m.fetch_products():
        if not (p.looks_like_silson and not p.is_travel):
            continue
        out.update(t.url for t in m.fetch_terms(p))
    return out


def _t_heungkuklife() -> set[tuple[str, str]]:
    from scripts.crawl.sites import heungkuklife as m

    out: set[tuple[str, str]] = set()
    for p in m.fetch_products(robots_confirmed=True):
        if not (p.looks_like_silson and not p.is_travel):
            continue
        out.update((_norm(p.product_name), t.sale_start) for t in m.fetch_terms(p))
    return out


#: 브라우저 수집분은 URL 을 모른다 → 대상 집합을 만들 수 없다.
#: 건수만 별도로 확인해야 하므로 여기서는 제외하고 그 사실을 보고한다.
BROWSER_ONLY = {
    "samsunglife": "브라우저 수집 — URL 미보유. 사이트 검색 결과 223건이 대상",
    "meritzfire": "브라우저 수집 — URL 미보유. 검색형 '실손' 157건이 대상",
    "nhfire": "브라우저 수집 — URL 미보유. 단독실손 12개 상품이 대상",
}

TARGETS = {
    "samsungfire": _t_samsungfire,
    "dbins": _t_dbins,
    "nhlife": _t_nhlife,
    "myangel": _t_myangel,
    "kbinsure": _t_kbinsure,
    "hyundaimarine": _t_hyundaimarine,
    "heungkukfire": _t_heungkukfire,
    "lotteins": _t_lotteins,
    "heungkuklife": _t_heungkuklife,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="한 곳만 대조")
    args = ap.parse_args()

    names = [args.only] if args.only else list(TARGETS)
    rows: list[dict[str, object]] = []

    print(f"{'보험사':<15}{'사이트':>7}{'수집':>7}{'파일':>7}{'누락':>7}  비고")
    print("-" * 62)

    for slug in names:
        fn = TARGETS.get(slug)
        if not fn:
            print(f"{slug:<15}{'-':>7}{'-':>7}{'-':>7}{'-':>7}  대상 함수 없음")
            continue
        n_rec, urls = _collected(slug)
        n_file = _files(slug)
        path = _MANIFESTS / f"{slug}.jsonl"
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []
        mine = _keys(rows)
        try:
            target = fn()
        except Exception as e:  # noqa: BLE001
            #: ★조회 실패를 "누락 0"으로 만들지 않는다. 실패는 실패로 적는다.
            print(f"{slug:<15}{'실패':>7}{n_rec:>7}{n_file:>7}{'?':>7}  {type(e).__name__}: {str(e)[:28]}")
            rows.append({"slug": slug, "error": f"{type(e).__name__}: {e}", "collected": n_rec, "files": n_file})
            continue
        #: 대상이 (이름, 개시일) 튜플이면 그 키로, URL 집합이면 URL 로 비교한다.
        first = next(iter(target)) if target else None
        missing = sorted(target - (mine if isinstance(first, tuple) else urls))
        rows.append(
            {
                "slug": slug,
                "site_target": len(target),
                "collected": n_rec,
                "files": n_file,
                "missing": len(missing),
                "missing_sample": [str(x) for x in missing[:5]],
            }
        )
        note = "완전" if not missing else f"★{len(missing)}건 누락"
        print(f"{slug:<15}{len(target):>7}{n_rec:>7}{n_file:>7}{len(missing):>7}  {note}")

    print("-" * 62)
    for slug, why in BROWSER_ONLY.items():
        n_rec, _ = _collected(slug)
        print(f"{slug:<15}{'—':>7}{n_rec:>7}{_files(slug):>7}{'—':>7}  {why}")
        rows.append({"slug": slug, "url_unknown": True, "collected": n_rec, "files": _files(slug), "note": why})

    _OUT.mkdir(parents=True, exist_ok=True)
    out = _OUT / f"{date.today().isoformat()}_커버리지_대조.json"
    out.write_text(
        json.dumps(
            {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": rows},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n저장: {out.relative_to(_ROOT)}")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
