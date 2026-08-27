"""동작 스토리보드용 화면 캡처 — 실제로 눌러서 찍는다.

    python -m scripts.run_customer_server   # :8080
    python -m scripts.run_admin_server      # :8081
    python scripts/capture_storyboard.py docs/delivery/screenshots

★캡처는 「동작한다」의 증거다. 그러므로 목업을 찍지 않는다.
  실제 서버에 붙어 화면을 눌러 가며 찍고, 그때 화면에 실제로 있던 문구를
  cuts.json 에 함께 남긴다. 문구를 손으로 따로 적으면 캡처와 어긋나고,
  어긋나는 순간 증거가 아니게 된다.

★약관 원문은 저작물이다(CLAUDE.md §2).
  `data/extracted/`·`data/structured/` 를 커밋에서 막아 둔 것과 같은 이유로
  원문이 찍힌 캡처도 저장소에 넣지 않는다. 그런데 `docs/` 는 .gitignore 가
  막지 않으므로 **찍는 단계에서** 가린다(MASK). 가리는 것은 인용 본문(`.quote`)
  뿐이고 라벨·조항ID·페이지(`.loc`)는 남긴다 — 인용이 동작한다는 증거는 거기에 있다.
"""

from __future__ import annotations

import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

CUSTOMER = "http://127.0.0.1:8080"
ADMIN = "http://127.0.0.1:8081"

# 이 흐름에 쓰는 입력. 가입일 형식은 YYYYMMDD 다(ISO 로 보내면 422).
INSURER, ENROLLED, KCD = "삼성화재", "20190501", "K02.1"

MASK = """
document.querySelectorAll('.quote').forEach(q => {
  const n = (q.textContent || '').replace(/\\s+/g,'').length;
  q.textContent = `［약관 원문 ${n}자 — 저작물이라 캡처에서 가림］`;
  q.style.cssText += ';color:#7a7a7a;background:#f4f4f4;border:1px dashed #c0c0c0;'
                   + 'padding:8px;font-style:italic;letter-spacing:.3px';
});
document.querySelectorAll('.quote').length;
"""


def main(out_dir: str) -> int:
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cuts: list[dict] = []

    def snap(page, no, title, *, element=None):
        """가리고 → 찍고 → 그때 화면 문구를 함께 기록한다. 순서가 중요하다."""
        masked = page.evaluate(MASK)
        name = f"{no:02d}_{title}.png"
        target = element or page
        target.screenshot(path=str(out / name), **({} if element else {"full_page": True}))
        src = element or page.query_selector("#result") or page.query_selector("body")
        text = " ".join((src.inner_text() or "").split()) if src else ""
        cuts.append({"no": no, "file": name, "title": title, "onscreen": text[:600]})
        print(f"  컷{no} {name}" + (f"  (원문 {masked}곳 가림)" if masked else ""))

    def panel(page, needle: str, min_h: int = 200):
        """화면의 한 영역만 골라 돌려준다.

        ★처음엔 전 컷을 full_page 로 찍었는데 컷1(빈 입력)과 컷2(입력 완료)가
          눈으로 구분되지 않았다. 페이지 전체가 3,000px 인데 달라진 곳은 입력칸
          세 개뿐이라 스토리보드에서 같은 그림으로 보였다.
          「무엇이 달라졌는지」가 안 보이면 그건 증거가 아니다.
        """
        page.evaluate(
            """([needle, minH]) => {
              document.querySelectorAll('[data-shot]').forEach(e => e.removeAttribute('data-shot'));
              const re = new RegExp(needle);
              const all = [...document.querySelectorAll('div,section,aside,form')];
              const hit = all.filter(e => re.test(e.textContent || '') && e.offsetHeight > minH);
              if (hit.length) hit[hit.length - 1].setAttribute('data-shot', '1');
            }""",
            [needle, min_h],
        )
        return page.query_selector("[data-shot='1']")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1280, "height": 900})

        pg.goto(CUSTOMER, wait_until="networkidle", timeout=60_000)
        snap(pg, 1, "입력", element=panel(pg, "보험정보 입력"))

        pg.fill("#insurer", INSURER)
        pg.fill("#enrolled", ENROLLED)
        pg.fill("#codes", KCD)
        box = pg.query_selector("input[type=checkbox]")
        if box and not box.is_checked():
            box.check()
        snap(pg, 2, "입력완료", element=panel(pg, "보험정보 입력"))

        pg.click("#go")
        pg.wait_for_timeout(12_000)
        # 여기서 기권한다 — 상품이 여럿이라. 오류가 아니라 정상 결과다.
        snap(pg, 3, "기권_후보제시", element=pg.query_selector("#result"))

        cand = pg.query_selector("button.chip-btn.cand")
        if cand is None:
            print("  ★후보 버튼이 없다 — 화면이 기권하지 않았거나 선택자가 바뀌었다")
            return 1
        cand.click()
        pg.wait_for_timeout(15_000)
        snap(pg, 4, "판정_근거조항", element=pg.query_selector("#result"))

        # 용어 설명은 대화 패널에서 일어난다. 전체 페이지를 찍으면 판정 결과에 묻히므로
        # 패널만 따로 찍는다 — 앞서 전체를 찍었다가 컷4 와 구분이 안 됐다.
        pg.goto(CUSTOMER, wait_until="networkidle", timeout=60_000)
        for btn in pg.query_selector_all("button"):
            if (btn.inner_text() or "").strip() == "본인부담금":
                btn.click()
                break
        pg.wait_for_timeout(14_000)
        snap(pg, 5, "용어설명", element=panel(pg, "보험 상담 챗봇", 300))

        pg.goto(f"{ADMIN}/static/admin.html", wait_until="networkidle", timeout=60_000)
        pg.wait_for_timeout(4_000)
        snap(pg, 6, "운영화면")

        browser.close()

    (out / "cuts.json").write_text(
        json.dumps(cuts, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\n{len(cuts)}컷 저장 → {out}")
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "docs/delivery/screenshots"
    raise SystemExit(main(target))
