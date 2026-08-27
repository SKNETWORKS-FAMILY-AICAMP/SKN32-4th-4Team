"""판매기간 보충 — 목록 화면만 읽어 매니페스트의 빈 날짜를 채운다.

★왜 필요한가

    삼성생명은 목록 화면에 **판매기간 컬럼이 있다**(`td:nth-child(4)`,
    예: `2018-11-22 ~ 2018-11-22`). `browser_collector` 의 `SiteConfig` 에
    `period_cell_selector` 로 정의까지 돼 있는데 **저장하는 코드가 없었다.**
    그래서 95건이 날짜 없이 쌓였다.

    PDF 안에서 찾아보려 했으나 쓸 수 없었다.
      - 1페이지가 **비어 있다**(`''`). 표지가 이미지이거나 빈 장이다.
      - 본문에는 날짜가 있지만 `2007.6.28`, `2014.1.1` 같은 **법령 인용일**이다.
        이걸 판매개시일로 쓰면 지어내는 것이다.
      - 파일명에도 없다.

    ★그래서 남은 길은 **사이트뿐**이고, PDF 를 다시 받을 필요는 없다.
      목록 23페이지만 읽으면 된다.

★찾는 방식 — 상품명으로 맞춘다

    브라우저 수집분은 **PDF 의 원 URL 을 갖고 있지 않다.** URL 로 못 맞춘다.
    상품명을 정규화해 맞추고, **여러 건이 걸리면 채우지 않는다**(모호하면 비운다).

★지어내지 않는다

    채운 행에는 근거를 남긴다.

        sale_start / sale_end
        date_source     = "site_list"
        date_confidence = "exact"

실행:
    python -m scripts.crawl.backfill_dates --site samsunglife --dry-run
    python -m scripts.crawl.backfill_dates --site samsunglife
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from app.core.errors import InfraError

_ROOT = Path(__file__).resolve().parents[2]
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"

#: `2018-11-22 ~ 2018-11-22` / `2018.11.22~` 등.
_PERIOD = re.compile(
    r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})"
    r"(?:\s*~\s*(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2}))?"
)


def _norm(s: str) -> str:
    return re.sub(r"[\s·∙・()（）\[\]]+", "", s or "")


def _parse_period(text: str) -> tuple[str, str] | None:
    m = _PERIOD.search(text or "")
    if not m:
        return None
    start = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
    end = ""
    if m.group(4):
        end = f"{m.group(4)}{int(m.group(5)):02d}{int(m.group(6)):02d}"
        #: ★시작=종료 인 행이 많다(판매 하루?). 그건 사이트 표기 그대로 둔다.
    return start, end


def scrape_list(slug: str) -> dict[str, tuple[str, str]]:
    """목록 화면에서 `{정규화 상품명: (개시일, 종료일)}` 을 모은다. PDF 는 받지 않는다."""
    from scripts.crawl.browser_collector import SITES, _goto_next_page, _robots_allows

    cfg = SITES.get(slug)
    if cfg is None:
        raise InfraError(f"모르는 사이트: {slug}")
    if not cfg.period_cell_selector:
        raise InfraError(
            f"{slug} 에는 판매기간 셀렉터가 없습니다. 목록에 그 컬럼이 없는 사이트입니다."
        )

    allowed, why = _robots_allows(cfg.host, cfg.entry_url)
    print(f"robots: {why}")
    if not allowed:
        raise InfraError(f"robots 가 허용하지 않습니다: {cfg.entry_url} ({why})")

    found: dict[str, tuple[str, str]] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        try:
            page.goto(cfg.entry_url, wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_timeout(cfg.settle_ms)
            if cfg.search_input_selector and cfg.search_terms:
                page.fill(cfg.search_input_selector, cfg.search_terms[0])
                page.keyboard.press("Enter")
                page.wait_for_timeout(cfg.settle_ms)

            page_no = 0
            while page_no < cfg.max_pages:
                page_no += 1
                rows = page.locator(cfg.table_selector).first.locator(cfg.row_selector)
                try:
                    rows.nth(1).wait_for(timeout=15_000)
                except (PWTimeout, PWError):
                    pass
                n = rows.count()
                if n == 0:
                    break
                got = 0
                for i in range(n):
                    row = rows.nth(i)
                    try:
                        nm = row.locator(cfg.name_cell_selector).first.inner_text(timeout=3_000)
                        pd = row.locator(cfg.period_cell_selector).first.inner_text(timeout=3_000)
                    except (PWTimeout, PWError):
                        continue
                    per = _parse_period(pd)
                    if not per or not nm.strip():
                        continue
                    key = _norm(nm)
                    #: ★같은 이름이 다른 기간으로 여러 번 나오면 **모호하다.**
                    #:   빈 튜플로 표시해 두고 나중에 채우지 않는다.
                    if key in found and found[key] != per:
                        found[key] = ("", "")
                    else:
                        found[key] = per
                    got += 1
                print(f"  p{page_no}: 행 {n} / 기간 읽음 {got}  (누적 {len(found)})", flush=True)
                if not _goto_next_page(cfg, page, page_no + 1):
                    break
        finally:
            ctx.close()
            browser.close()
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    table = scrape_list(args.site)
    usable = {k: v for k, v in table.items() if v[0]}
    print(f"\n목록에서 읽은 상품 {len(table)}종 / 기간이 하나로 확정된 것 {len(usable)}종")

    m = _MANIFESTS / f"{args.site}.jsonl"
    if not m.exists():
        raise InfraError(f"매니페스트가 없습니다: {m}")
    rows = [
        json.loads(line)
        for line in m.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    filled = ambiguous = nomatch = 0
    for r in rows:
        if (r.get("sale_start") or "").strip():
            continue
        key = _norm(r.get("product_name") or r.get("original_name") or "")
        if not key:
            nomatch += 1
            continue
        per = table.get(key)
        if per is None:
            nomatch += 1
            continue
        if not per[0]:
            ambiguous += 1
            continue
        r["sale_start"], r["sale_end"] = per
        r["date_source"] = "site_list"
        r["date_confidence"] = "exact"
        filled += 1

    print(f"채움 {filled} / 기간이 여럿이라 보류 {ambiguous} / 목록에 없음 {nomatch}")
    if args.dry_run:
        print("(dry-run: 아무것도 쓰지 않았습니다.)")
        return
    if filled:
        tmp = m.with_suffix(".jsonl.tmp")
        tmp.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8",
        )
        tmp.replace(m)
        print(f"→ {m.relative_to(_ROOT)}")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
