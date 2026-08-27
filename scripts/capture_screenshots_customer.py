"""고객용 화면 스크린샷 캡처 (shop/마이페이지/화상상담/인식비교).

실행 중 dev 서버(scripts/run_dev_server.py)에 대해 실제 페이지를 열어 캡처한다. 웹캠·마이크는
브라우저가 헤드리스라 실제 장치는 없지만, 화면 구성(레이아웃·버튼·안내)은 그대로 담긴다.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8080"
OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")  # 설치된 Chrome 사용(playwright 브라우저 별도 다운로드 불필요)
        page = browser.new_page(viewport={"width": 1200, "height": 860})

        # 15. 고객 스토어프론트 — 상품 담긴 상태
        page.goto(f"{BASE}/static/shop.html")
        page.wait_for_selector(".product-card", timeout=15000)
        page.evaluate(
            "() => { const b=[...document.querySelectorAll('.product-card .add-btn')]"
            ".filter(x=>!x.disabled); if(b[0]){b[0].click();b[0].click();} if(b[2])b[2].click(); }"
        )
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "15_shop_cart.png"), full_page=True)
        print("saved 15")

        # 16. 마이페이지 · 얼굴 로그인(로그아웃 상태 = 로그인 폼)
        page.goto(f"{BASE}/static/mypage.html")
        page.wait_for_selector("#loginPanel", timeout=15000)
        page.screenshot(path=str(OUT / "16_mypage_face_login.png"), full_page=True)
        print("saved 16")

        # 17. 화상 상담(웹캠 게이트 + AI 아바타)
        page.goto(f"{BASE}/static/video.html")
        page.wait_for_selector("#avatar", timeout=15000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / "17_video_consult.png"), full_page=True)
        print("saved 17")

        # 18. 얼굴인식 백엔드 성능 비교(셀렉터 + A/B 슬롯)
        page.goto(f"{BASE}/static/facebench.html")
        page.wait_for_selector("#backendSelect", timeout=15000)
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "18_facebench.png"), full_page=True)
        print("saved 18")

        browser.close()


if __name__ == "__main__":
    main()
