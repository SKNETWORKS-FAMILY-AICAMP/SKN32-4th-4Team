"""docs/screenshots용 실 화면 캡처 스크립트.

★★**2026-08-03 — 이 스크립트는 지금 돌지 않는다. 커머스 시대 산물이다.**

    가리키는 화면 중 `orders.html` · `shop.html` · `mcp.html` 은 **파일이 없다**(내 변경 이전부터).
    `rag.html` 은 2026-08-03 에 `legacy/v6_rag_ui.zip` 으로 격리했다.
    남은 것은 `admin.html` · `facebench.html` · `mypage.html` · `insurance.html` 뿐이다.

    ★**고치지 않고 사실만 적어 둔다.** 보험 화면(`insurance.html`) 캡처로 다시 쓰려면
      흐름을 새로 짜야 하는데, 그건 이 정리 작업의 범위가 아니다(RULE §2 범위 초과 금지).
      **후속 과제다.** 돌려 보고 "왜 안 되지" 하는 시간을 없애려고 여기 남긴다.

---

(원문) 재사용 도구 — UI 변경 시 다시 실행해 docs/screenshots를 갱신한다.

전제: `python scripts/run_dev_server.py`가 http://localhost:8080 에서 이미 떠 있어야 한다.
실제 페이지를 실제로 클릭·입력해서 실제 API 응답을 화면에 띄운 뒤 PNG로 저장한다(목업 아님).
관리자 화면은 별도 capture_screenshots_admin.py, MCP는 capture_screenshots_mcp.py가 담당한다
(각 결과 패널을 클릭 전에 비워 이전 단계의 응답이 남아있는 채로 캡처되지 않게 한다).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8080"
OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)


def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    print("saved", name)


def clear_and_click(page, clear_sel, click_sel, wait_sel, timeout=180000):
    """LLM 경유 동작이 섞여 있어 기본 대기를 넉넉히 둔다(로컬 CPU 추론은 수십 초)."""
    page.evaluate(f"document.querySelector('{clear_sel}').innerHTML = ''")
    page.click(click_sel)
    page.wait_for_selector(wait_sel, timeout=timeout)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")  # 설치된 Chrome 사용(playwright 브라우저 별도 다운로드 불필요)
        page = browser.new_page(viewport={"width": 1200, "height": 800})

        # --- 1. 에이전트 채팅 ---
        page.goto(f"{BASE}/static/index.html")
        page.fill("#question", "P0001 상품 가격 알려줘")
        # ★ #chatForm 안 첫 버튼은 마이크(#micBtn, Phase 11에 추가됨)다. 셀렉터를
        # "#chatForm button"으로 두면 녹음이 눌려 질문이 전송되지 않는다 → submit을 명시.
        page.click('#chatForm button[type="submit"]')
        page.wait_for_selector("#transcript .msg.bot", timeout=300000)  # 실측 56초(로컬 CPU) — 여유 확보
        time.sleep(1)
        shot(page, "01_agent_chat")

        # --- 2. RAG 검색 (임베딩만) ---
        page.goto(f"{BASE}/static/rag.html")
        clear_and_click(page, "#searchResult", "#searchBtn", "#searchResult pre.result", 30000)
        shot(page, "02_rag_vector_search")

        # --- 3. RAG QA (hybrid backend, 실 LLM) ---
        page.select_option("#qaBackend", "hybrid")
        clear_and_click(page, "#qaResult", "#qaBtn", "#qaResult pre.result", 300000)  # hybrid=검색+LLM 생성, 로컬 CPU에선 분 단위
        time.sleep(1)
        shot(page, "03_rag_qa_hybrid")

        # --- 4. 주문: 회원가입 + 미리보기 ---
        page.goto(f"{BASE}/static/orders.html")
        uname = "demo_" + uuid.uuid4().hex[:8]
        page.fill("#username", uname)
        page.click("#signupBtn")
        page.wait_for_selector("#authResult pre.result", timeout=15000)
        # 회원가입 응답에는 실 JWT가 담기므로 화면 캡처에는 남기지 않는다(admin 캡처와 동일 처리).
        page.evaluate(
            "document.getElementById('authResult').innerHTML = "
            "'<span class=\"badge ok\">HTTP 200</span> (토큰은 스크린샷에서 생략)'"
        )
        page.click("#loadProductsBtn")
        page.wait_for_selector("#productsTable table", timeout=15000)
        clear_and_click(page, "#previewResult", "#previewBtn", "#previewResult pre.result")
        shot(page, "04_orders_preview")

        # --- 5. 승인 성공 + 멱등 재생 ---
        clear_and_click(page, "#approveResult", "#approveBtn", "#approveResult pre.result")
        time.sleep(0.3)
        clear_and_click(page, "#approveResult", "#replayBtn", "#approveResult pre.result")
        shot(page, "05_orders_approve_idempotent")

        # --- 6. 거부 경로: 키 없음(422) ---
        clear_and_click(page, "#approveResult", "#noKeyBtn", "#approveResult pre.result")
        shot(page, "06_orders_reject_no_key_422")

        # --- 7. 거부 경로: 같은 키 다른 payload(409) ---
        clear_and_click(page, "#approveResult", "#conflictBtn", "#approveResult pre.result")
        shot(page, "07_orders_reject_conflict_409")

        # --- 8. 거부 경로: 재고초과(422) + 주문목록 ---
        clear_and_click(page, "#approveResult", "#oversellBtn", "#approveResult pre.result")
        page.click("#loadOrdersBtn")
        page.wait_for_selector("#ordersTable table", timeout=15000)
        shot(page, "08_orders_reject_oversell_and_list")

        browser.close()
        print("DONE 1-8")


if __name__ == "__main__":
    main()
