"""MCP 스크린샷 2개는 서브프로세스 워밍업 때문에 별도 스크립트로 넉넉한 타임아웃을 준다."""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8080"
OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")  # 설치된 Chrome 사용(playwright 브라우저 별도 다운로드 불필요)
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.goto(f"{BASE}/static/mcp.html")

        page.click("#listToolsBtn")
        page.wait_for_selector("#toolsResult pre.result", timeout=90000)
        page.screenshot(path=str(OUT / "13_mcp_tools_list.png"), full_page=True)
        print("saved 13")

        page.click("#callToolBtn")
        page.wait_for_selector("#callResult pre.result", timeout=90000)
        page.screenshot(path=str(OUT / "14_mcp_call_get_price.png"), full_page=True)
        print("saved 14")

        browser.close()


if __name__ == "__main__":
    main()
