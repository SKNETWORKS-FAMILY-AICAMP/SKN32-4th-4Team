"""고객 보험 화면 스크린샷 — 데스크톱·모바일·키보드 포커스 표시 확인.

실행 중 dev 서버(scripts.run_customer_server, 8080)에 대해 실제 페이지를 열어 캡처한다.
Claude Code Browser 패널이 아니라 Playwright가 직접 Chrome을 띄우므로 패널 표시 여부와
무관하다(app/static/{shop,mypage,video,facebench}.html용 capture_screenshots_customer.py와
같은 패턴).
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8080"
OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")

        # 19. 데스크톱(1280x800)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(f"{BASE}/")
        page.wait_for_selector("#insurer", timeout=15000)
        page.screenshot(path=str(OUT / "19_insurance_desktop.png"), full_page=True)
        print("saved 19 desktop")

        # 20. 모바일(375x812)
        page.close()
        page = browser.new_page(viewport={"width": 375, "height": 812})
        page.goto(f"{BASE}/")
        page.wait_for_selector("#insurer", timeout=15000)
        page.screenshot(path=str(OUT / "20_insurance_mobile.png"), full_page=True)
        print("saved 20 mobile")

        # 21. 키보드 포커스 — Tab으로 insurer 입력창까지 이동해 실제로 보이는지 확인
        page.close()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(f"{BASE}/")
        page.wait_for_selector("#insurer", timeout=15000)
        page.keyboard.press("Tab")
        page.wait_for_timeout(150)
        outline = page.evaluate(
            "() => { const e = document.activeElement; const cs = getComputedStyle(e);"
            " return {id: e.id, outline: cs.outlineStyle + ' ' + cs.outlineWidth + ' ' + cs.outlineColor}; }"
        )
        print("focused element:", outline)
        page.screenshot(path=str(OUT / "21_insurance_keyboard_focus.png"))
        print("saved 21 keyboard focus")

        browser.close()


if __name__ == "__main__":
    main()
