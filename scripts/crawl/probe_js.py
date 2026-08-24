"""약관 PDF 발견 — JS 렌더링 판(2차 실측).

1차 실측(`probe_terms_pages.py`)에서 13곳 전부 정적 HTML로는 PDF 링크 0건이었다.
원인은 사이트 구조가 제각각이라서가 아니라 **대부분이 JS 렌더링 SPA**이기 때문이었다.

그렇다면 필요한 것은 **사이트별 크롤러 13개가 아니라 "렌더링 엔진 1개 + 사이트별 설정"** 이다.
이 도구는 그 가설을 검증한다 — 브라우저로 렌더링하면 어디까지 잡히는가?

하는 일:
- 헤드리스 크롬으로 첫 페이지를 렌더링하고, 링크를 점수화해 상위 후보 페이지를 연다.
- 렌더링된 DOM의 링크 + **네트워크 응답 URL** 양쪽에서 PDF를 찾는다.
  (버튼 클릭으로 열리는 PDF는 DOM 링크에 없고 네트워크에만 나타난다.)

하지 않는 일:
- PDF를 내려받지 않는다. 링크 존재 여부만 본다.
- 검색 폼을 조작하지 않는다. 그건 사이트별 설정의 영역이다.
- robots 확인에 실패하면 건너뛰되 **결과에 남긴다**(조용히 빠뜨리면 '해당 없음'과 구분되지 않는다).

실행: python -m scripts.crawl.probe_js
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 BarobomResearchBot/0.1 "
    "(+contact: set-before-deploy; purpose: insurance-terms-research)"
)
NAV_TIMEOUT_MS = 25_000
SETTLE_MS = 3_500
DELAY_MS = 1_500
MAX_PAGES_PER_HOST = 3

_ROOT = Path(__file__).resolve().parents[2]
_OUT = _ROOT / "docs" / "reports"

HOSTS: list[tuple[str, str]] = [
    ("삼성화재", "www.samsungfire.com"),
    ("메리츠화재", "www.meritzfire.com"),
    ("DB손해보험", "www.idbins.com"),
    ("현대해상", "www.hi.co.kr"),
    ("KB손해보험", "www.kbinsure.co.kr"),
    ("한화손해보험", "www.hwgeneralins.com"),
    ("흥국화재", "www.heungkukfire.co.kr"),
    ("롯데손해보험", "www.lotteins.co.kr"),
    ("NH농협손해보험", "www.nhfire.co.kr"),
    ("삼성생명", "www.samsunglife.com"),
    ("흥국생명", "www.heungkuklife.co.kr"),
    ("동양생명", "www.myangel.co.kr"),
    ("NH농협생명", "www.nhlife.co.kr"),
]

_HINTS: tuple[tuple[str, int], ...] = (
    ("약관", 12),
    ("상품공시", 11),
    ("실손", 10),
    ("공시", 8),
    ("disclosure", 7),
    ("terms", 4),
    ("상품", 3),
)
#: 이미지·폰트·미디어는 받지 않는다. 남의 서버에 불필요한 부하를 주지 않기 위해서다.
_BLOCK = {"image", "font", "media"}


@dataclass
class Probe:
    insurer: str
    host: str
    robots: str = ""
    pages: int = 0
    dom_links: int = 0
    candidates: int = 0
    pdf_from_dom: int = 0
    pdf_from_network: int = 0
    visited: list[str] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)
    verdict: str = "미판정"
    note: str = ""

    @property
    def pdf_total(self) -> int:
        return self.pdf_from_dom + self.pdf_from_network


def _robots_allows(host: str) -> tuple[bool, str]:
    """확인 실패는 허용이 아니다(RFC 9309)."""
    url = f"https://{host}/robots.txt"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read(512_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True, "robots 없음(규칙 부재)"
        return False, f"robots HTTP {e.code} → 금지 취급"
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return False, f"robots 확인 불가({type(e).__name__}) → 금지 취급"
    rp = RobotFileParser()
    rp.parse(body.splitlines())
    ok = rp.can_fetch(USER_AGENT, f"https://{host}/")
    return ok, f"HTTP 200 → {'allow' if ok else 'disallow'}"


def _score(text: str, href: str) -> int:
    blob = f"{text} {href}".lower()
    return sum(w for kw, w in _HINTS if kw.lower() in blob)


def _is_pdf(url: str) -> bool:
    bare = url.lower().split("?")[0].split("#")[0]
    return bare.endswith(".pdf") or "/pdf/" in bare


def probe_host(browser, insurer: str, host: str) -> Probe:
    r = Probe(insurer=insurer, host=host)
    allowed, verdict = _robots_allows(host)
    r.robots = verdict
    if not allowed:
        r.verdict = "건너뜀"
        r.note = "robots 재확인 실패 또는 금지."
        return r

    net_pdfs: set[str] = set()
    ctx = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 900})
    ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)

    def _route(route):
        if route.request.resource_type in _BLOCK:
            route.abort()
        else:
            route.continue_()

    ctx.route("**/*", _route)
    ctx.on("response", lambda resp: net_pdfs.add(resp.url) if _is_pdf(resp.url) else None)

    page = ctx.new_page()
    dom_pdfs: set[str] = set()
    queue = [f"https://{host}/"]
    seen: set[str] = set()

    try:
        while queue and r.pages < MAX_PAGES_PER_HOST:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(SETTLE_MS)
            except (PWTimeout, PWError) as e:
                if r.pages == 0:
                    r.note = f"렌더링 실패({type(e).__name__})"
                r.pages += 1
                continue

            r.pages += 1
            r.visited.append(page.url)
            anchors = page.eval_on_selector_all(
                "a[href]",
                "els => els.map(e => [e.getAttribute('href'), (e.innerText||'').trim().slice(0,60)])",
            )
            r.dom_links += len(anchors)

            scored: list[tuple[int, str]] = []
            for href, text in anchors:
                if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                    continue
                absolute = urljoin(page.url, href)
                if _is_pdf(absolute):
                    dom_pdfs.add(absolute)
                    continue
                if urlparse(absolute).netloc != host or absolute in seen:
                    continue
                s = _score(text, href)
                if s > 0:
                    scored.append((s, absolute))

            if r.pages == 1:
                scored.sort(key=lambda x: -x[0])
                r.candidates = len(scored)
                queue.extend(u for _, u in scored[: MAX_PAGES_PER_HOST - 1])
            page.wait_for_timeout(DELAY_MS)
    finally:
        ctx.close()

    r.pdf_from_dom = len(dom_pdfs)
    r.pdf_from_network = len(net_pdfs - dom_pdfs)
    r.samples = sorted(dom_pdfs | net_pdfs)[:3]

    if r.pdf_total:
        r.verdict = "PDF 링크 발견"
    elif r.candidates:
        r.verdict = "약관 메뉴는 있으나 PDF 미발견"
        r.note = r.note or "검색 폼·POST 뒤에 있을 가능성. 사이트별 설정 필요."
    else:
        r.verdict = "약관 메뉴도 미발견"
        r.note = r.note or "첫 페이지에서 약관/공시 링크를 못 찾음."
    return r


def main() -> None:
    results: list[Probe] = []
    with sync_playwright() as pw:
        # 브라우저 미설치 시 `python -m playwright install chromium` 이 필요하다.
        # (`executable_path` 는 경로 존재를 확인하지 않으므로 설치 여부 판단에 쓰지 않는다.)
        browser = pw.chromium.launch(headless=True)
        try:
            for insurer, host in HOSTS:
                res = probe_host(browser, insurer, host)
                results.append(res)
                print(
                    f"  {host}: pages={res.pages} links={res.dom_links} "
                    f"cand={res.candidates} pdf={res.pdf_total} [{res.verdict}]"
                )
        finally:
            browser.close()

    stamp = date.today().isoformat()
    _OUT.mkdir(parents=True, exist_ok=True)
    (_OUT / f"{stamp}_약관발견_실측_JS.json").write_text(
        json.dumps(
            {
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "engine": "playwright/chromium headless",
                "max_pages_per_host": MAX_PAGES_PER_HOST,
                "results": [asdict(x) | {"pdf_total": x.pdf_total} for x in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    hit = [x for x in results if x.pdf_total]
    menu = [x for x in results if not x.pdf_total and x.candidates]
    lines = [
        f"# 약관 PDF 발견 실측 — JS 렌더링판 ({stamp})",
        "",
        "- 도구: `scripts/crawl/probe_js.py` (헤드리스 크롬, 호스트당 최대 "
        f"{MAX_PAGES_PER_HOST}페이지, **PDF 미다운로드**)",
        "- 1차 정적 실측에서 13곳 전부 0건이었다. 원인이 사이트 구조가 아니라 **JS 렌더링**인지 확인한다.",
        "",
        "| 회사 | 호스트 | robots | 페이지 | DOM링크 | 약관후보 | PDF(DOM) | PDF(네트워크) | 판정 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for x in results:
        lines.append(
            f"| {x.insurer} | `{x.host}` | {x.robots} | {x.pages} | {x.dom_links} "
            f"| {x.candidates} | {x.pdf_from_dom} | {x.pdf_from_network} | {x.verdict} |"
        )
    lines += [
        "",
        "## 요약",
        "",
        f"- PDF 링크가 잡힌 곳: **{len(hit)} / {len(results)}**",
        f"- 약관·공시 메뉴는 찾았으나 PDF 미발견: **{len(menu)}곳** → 검색 폼 조작이 필요한 사이트",
        f"- PDF 링크 총계: **{sum(x.pdf_total for x in results)}건** (실손 약관인지는 미확인)",
        "",
        "## 정적판과의 비교",
        "",
        "정적 HTML에서는 13곳 전부 0건이었다. 렌더링 후 수치가 오르면 "
        "**필요한 것은 사이트별 크롤러 13개가 아니라 렌더링 엔진 1개 + 사이트별 설정**이라는 뜻이다.",
    ]
    (_OUT / f"{stamp}_약관발견_실측_JS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"OK saved: docs/reports/{stamp}_약관발견_실측_JS.md")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
