"""화면이 **실제로 있는 것**만 가리키는가.

★왜 필요한가 — 실제로 깨져 있었다.

    `video.html` 이 `/api/agent/chat` 을 부르고 있었는데, 그 라우터는
    레거시 격리 때 사라졌다. 화면은 열리고 버튼도 눌리는데 **404 가 난다.**
    테스트가 없어서 아무도 몰랐고, 나도 **화면 목록을 눈으로 세어** 찾았다.

★정적 파일은 테스트가 잘 닿지 않는 곳이다.

    파이썬 import 그래프에 안 잡히므로 깨져도 조용하다.
    그래서 세 가지를 **정적으로** 확인한다 —
      1. HTML 이 부르는 스크립트가 실제로 있는가
      2. 스크립트가 부르는 API 경로가 앱에 실제로 있는가
      3. 차단 목록이 **없는 파일**을 막는 척하지 않는가
"""

from __future__ import annotations

import pathlib
import re

from app.main import _OPS_STATIC, create_app

_STATIC = pathlib.Path(__file__).resolve().parents[1] / "app" / "static"

#: 화면이 호출하는 API 경로. 템플릿 문자열(`${...}`)은 앞부분만 본다.
_API_CALL = re.compile(r"""["'`](/api/[a-zA-Z0-9_/-]+|/v1/[a-zA-Z0-9_/-]+)""")
#: HTML 이 부르는 스크립트.
_SCRIPT_SRC = re.compile(r'<script[^>]+src="([^"]+)"')
#: ★HTML 이 **링크로** 가리키는 정적 파일. `<script src>` 만 보면 이걸 놓친다.
_STATIC_HREF = re.compile(r'(?:href|src)="(/static/[^"?#]+)')


def _app_paths() -> set[str]:
    app = create_app("full")
    return {r.path for r in app.routes if hasattr(r, "path")}


def test_화면이_링크하는_정적파일이_실제로_있다():
    """★`<a href="/static/...">` 도 검사한다.

    이 테스트가 없어서 **죽은 링크 12개가 조용히 남아 있었다**(2026-08-03 실측).

        admin.html      → mcp.html · orders.html · shop.html
        facebench.html  → mcp.html · orders.html · shop.html
        mypage.html     → index.html · shop.html · video.html
        rag.html        → mcp.html · orders.html · shop.html

    앞선 두 테스트는 `<script src>` 와 API 만 봤다. **네비게이션 링크는 아무도 안 봤다** —
    화면은 열리는데 메뉴를 누르면 404 가 난다. `video.html` 때와 같은 종류의 실패다.

    ★`legacy/` 는 검사 범위 밖이다. 격리한 화면은 서비스하지 않으므로 고칠 이유가 없고,
      특정 파일명을 예외로 두면 그 예외가 다음 구멍이 된다(코덱스 지적).
    """
    missing: list[str] = []
    for html in sorted(_STATIC.glob("*.html")):
        for path in _STATIC_HREF.findall(html.read_text(encoding="utf-8")):
            name = path.rsplit("/", 1)[-1]
            if not (_STATIC / name).is_file():
                missing.append(f"{html.name} → {path}")
    assert not missing, "없는 정적 파일을 링크합니다: " + ", ".join(missing)


def test_html이_부르는_스크립트가_실제로_있다():
    missing: list[str] = []
    for html in sorted(_STATIC.glob("*.html")):
        for src in _SCRIPT_SRC.findall(html.read_text(encoding="utf-8")):
            if src.startswith("http"):
                continue
            name = src.rsplit("/", 1)[-1]
            if not (_STATIC / name).is_file():
                missing.append(f"{html.name} → {src}")
    assert missing == [], f"없는 스크립트를 부릅니다: {missing}"


def test_화면이_부르는_API가_앱에_실제로_있다():
    """★없는 경로를 부르면 화면은 열리는데 눌러야 404 가 난다."""
    paths = _app_paths()
    offenders: list[str] = []
    for js in sorted(_STATIC.glob("*.js")):
        for call in set(_API_CALL.findall(js.read_text(encoding="utf-8"))):
            #: 정확 일치거나, 그 아래 하위 경로가 하나라도 있으면 산다.
            if call in paths or any(p.startswith(call.rstrip("/")) for p in paths):
                continue
            offenders.append(f"{js.name} → {call}")
    assert offenders == [], (
        "화면이 없는 API 를 부릅니다(눌러야 404 가 납니다): " + ", ".join(offenders)
    )


def test_운영_차단목록이_없는_파일을_막는_척하지_않는다():
    """★목록만 보면 '막고 있다'로 읽히지만 실은 막을 것이 없었다.

    `mcp.html`·`orders.html` 이 레거시로 간 뒤에도 목록에 남아 있었다.
    """
    ghosts = [n for n in _OPS_STATIC if not (_STATIC / n).is_file()]
    #: ★메시지가 사실을 거꾸로 전하면 안 된다.
    #:   앞서 "차단 목록에 없는 파일이 있습니다" 라고 적었는데, 재는 것은
    #:   **목록에 있는데 파일이 없는 것**이다. 정반대로 읽혀 한참 엉뚱한 데를 봤다.
    assert ghosts == [], (
        f"차단 목록에 적혀 있으나 실제 파일이 없습니다: {sorted(ghosts)}. "
        "레거시로 옮겼다면 목록에서도 빼세요 — 남겨 두면 '막고 있다'로 읽힙니다."
    )


def test_고객_포트에서_운영_화면이_실제로_막힌다():
    from fastapi.testclient import TestClient

    c = TestClient(create_app("customer"))
    for name in sorted(_OPS_STATIC):
        assert c.get(f"/static/{name}").status_code == 404, f"{name} 이 고객 포트에 노출됩니다"
    #: ★보험 화면은 고객 포트에서 **열려야 한다.** 다 막으면 서비스가 없다.
    assert c.get("/static/insurance.html").status_code == 200


def test_보험_화면이_랜딩이다():
    from fastapi.testclient import TestClient

    body = TestClient(create_app("full")).get("/").text
    assert "올바른 보험비서" in body
    #: ★앞서 여기 커머스 `shop.html` 이름이 남아 500 이 났다.
    assert "insurance.js" in body


def test_보험_클라이언트가_llm_설명과_모델정보를_그린다():
    """API만 성공하고 화면이 결과를 버리는 회귀를 막는다."""
    js = (_STATIC / "insurance.js").read_text(encoding="utf-8")
    assert "api('/v1/chat'" in js
    assert "body.llm?.used" in js
    assert "body.llm.provider" in js
    assert "body.llm.model" in js
    assert "AI 설명" in js
    assert "status === 502 || status === 503" in js


def test_보험_클라이언트가_약관범위를_상병코드로_제출하지_않는다():
    """카탈로그의 C30~C39를 눌러 invalid_code 결과가 나오던 회귀를 막는다."""
    js = (_STATIC / "insurance.js").read_text(encoding="utf-8")
    html = (_STATIC / "insurance.html").read_text(encoding="utf-8")
    assert "it.input_allowed" in js
    assert "SINGLE_KCD_CODE.test(code)" in js
    assert "약관의 코드 범위" in js
    assert "C30~C39 같은 약관 범위는 선택할 수 없고" in html
