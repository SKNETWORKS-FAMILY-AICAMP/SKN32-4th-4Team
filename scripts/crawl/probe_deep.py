"""약관 PDF 심층 탐색 + 미응답 사이트 진단 (3차 실측).

2차(`probe_js.py`)에서 JS 렌더링을 켜자 막힘이 풀렸다. 남은 문제는 두 갈래였다.

  (A) 8곳 — 약관·공시 메뉴는 찾았는데 PDF가 안 잡힘  → 더 깊이 + 프레임까지 봐야 한다
  (B) 4곳 — 렌더링 후에도 링크가 0개                → 왜 그런지 진단이 필요하다

이 도구는 둘을 같이 한다.

가설 하나를 검증한다: **한국 보험사 공시실은 iframe 안에 있는 경우가 많다.**
그러면 메인 문서의 ``a[href]`` 로는 아무것도 안 잡힌다. 그래서 `page.frames` 전부를 훑는다.

진단으로 남기는 것(링크 0인 사이트가 '없는 것'인지 '못 본 것'인지 구분하려고):
- HTTP 상태 · 최종 URL(리다이렉트) · 제목 · 본문 길이 · 프레임 수 · 콘솔 오류

여전히 **PDF를 내려받지 않는다.** 링크 존재 여부만 본다.

실행: python -m scripts.crawl.probe_deep
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
NAV_TIMEOUT_MS = 30_000
SETTLE_MS = 5_000
DELAY_MS = 1_200
MAX_PAGES_PER_HOST = 7
MAX_DEPTH = 3

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

#: 2차보다 넓힌 키워드. '공시실'·'사업방법서'·'상품요약서'는 약관과 같은 자리에 있다.
_HINTS: tuple[tuple[str, int], ...] = (
    ("보험약관", 14),
    ("약관", 12),
    ("공시실", 12),
    ("상품공시", 11),
    ("실손", 10),
    ("사업방법서", 10),
    ("상품요약서", 9),
    ("공시", 8),
    ("다운로드", 6),
    ("disclosure", 7),
    ("terms", 4),
    ("자료실", 6),
)
_BLOCK = {"image", "font", "media"}


@dataclass
class Deep:
    insurer: str
    host: str
    robots: str = ""
    # ── 진단 ──
    http_status: int | None = None
    final_url: str = ""
    title: str = ""
    text_len: int = 0
    frame_count: int = 0
    console_errors: int = 0
    # ── 탐색 ──
    pages: int = 0
    max_depth_reached: int = 0
    links_main: int = 0
    links_frames: int = 0
    candidates: int = 0
    pdf_links: int = 0
    visited: list[str] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)
    verdict: str = "미판정"
    note: str = ""


def _robots_allows(host: str) -> tuple[bool, str]:
    req = urllib.request.Request(
        f"https://{host}/robots.txt", headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read(512_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True, "robots 없음"
        return False, f"robots HTTP {e.code} → 금지 취급"
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return False, f"robots 확인 불가({type(e).__name__}) → 금지 취급"
    rp = RobotFileParser()
    rp.parse(body.splitlines())
    ok = rp.can_fetch(USER_AGENT, f"https://{host}/")
    return ok, f"200 → {'allow' if ok else 'disallow'}"


def _score(text: str, href: str) -> int:
    blob = f"{text} {href}".lower()
    return sum(w for kw, w in _HINTS if kw.lower() in blob)


def _is_pdf(url: str) -> bool:
    bare = url.lower().split("?")[0].split("#")[0]
    return bare.endswith(".pdf") or "/pdf/" in bare


def _harvest(page) -> tuple[list[tuple[str, str, str]], int, int]:
    """메인 문서와 **모든 프레임**에서 링크를 걷는다.

    공시실이 iframe 안에 있으면 메인 문서만 봐서는 0건이 나온다.
    Returns: (링크들, 메인 개수, 프레임 개수)
    """
    js = "els => els.map(e => [e.getAttribute('href'), (e.innerText||'').trim().slice(0,80)])"
    out: list[tuple[str, str, str]] = []
    main_n = frame_n = 0
    for i, frame in enumerate(page.frames):
        try:
            rows = frame.eval_on_selector_all("a[href]", js)
            base = frame.url or page.url
        except PWError:
            continue
        for href, text in rows:
            if href:
                out.append((href, text or "", base))
        if i == 0:
            main_n = len(rows)
        else:
            frame_n += len(rows)
    return out, main_n, frame_n


def probe(browser, insurer: str, host: str) -> Deep:
    r = Deep(insurer=insurer, host=host)
    allowed, verdict = _robots_allows(host)
    r.robots = verdict
    if not allowed:
        r.verdict = "건너뜀"
        r.note = "robots 재확인 실패 또는 금지."
        return r

    net_pdfs: set[str] = set()
    dom_pdfs: set[str] = set()
    ctx = browser.new_context(
        user_agent=USER_AGENT, viewport={"width": 1366, "height": 950}, locale="ko-KR"
    )
    ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
    ctx.route("**/*", lambda rt: rt.abort() if rt.request.resource_type in _BLOCK else rt.continue_())
    ctx.on("response", lambda resp: net_pdfs.add(resp.url) if _is_pdf(resp.url) else None)

    page = ctx.new_page()
    page.on("console", lambda m: None)
    errs: list[str] = []
    page.on("console", lambda m: errs.append(m.type) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append("pageerror"))

    queue: list[tuple[str, int]] = [(f"https://{host}/", 0)]
    seen: set[str] = set()

    try:
        while queue and r.pages < MAX_PAGES_PER_HOST:
            url, depth = queue.pop(0)
            if url in seen or depth > MAX_DEPTH:
                continue
            seen.add(url)
            try:
                resp = page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(SETTLE_MS)
            except (PWTimeout, PWError) as e:
                r.pages += 1
                if depth == 0:
                    r.note = f"렌더링 실패({type(e).__name__})"
                continue

            r.pages += 1
            r.max_depth_reached = max(r.max_depth_reached, depth)
            r.visited.append(page.url)

            if depth == 0:
                r.http_status = resp.status if resp else None
                r.final_url = page.url
                try:
                    r.title = (page.title() or "")[:80]
                    r.text_len = page.evaluate("() => (document.body && document.body.innerText || '').length")
                except PWError:
                    pass
                r.frame_count = len(page.frames)

            links, main_n, frame_n = _harvest(page)
            r.links_main += main_n
            r.links_frames += frame_n

            scored: list[tuple[int, str]] = []
            for href, text, base in links:
                if href.startswith(("javascript:", "mailto:", "tel:", "#")):
                    continue
                absolute = urljoin(base, href)
                if _is_pdf(absolute):
                    dom_pdfs.add(absolute)
                    continue
                if urlparse(absolute).netloc != host or absolute in seen:
                    continue
                s = _score(text, href)
                if s > 0:
                    scored.append((s, absolute))

            scored.sort(key=lambda x: -x[0])
            if depth == 0:
                r.candidates = len(scored)
            room = MAX_PAGES_PER_HOST - r.pages - len(queue)
            if room > 0 and depth < MAX_DEPTH:
                queue.extend((u, depth + 1) for _, u in scored[:room])
            page.wait_for_timeout(DELAY_MS)
    finally:
        r.console_errors = len(errs)
        ctx.close()

    all_pdfs = dom_pdfs | net_pdfs
    r.pdf_links = len(all_pdfs)
    r.samples = sorted(all_pdfs)[:5]

    if r.pdf_links:
        r.verdict = "PDF 발견"
    elif r.text_len < 500:
        r.verdict = "페이지 미로딩"
        r.note = r.note or f"본문 {r.text_len}자. 봇 차단 또는 추가 대기 필요."
    elif r.links_main + r.links_frames == 0:
        r.verdict = "링크 없음"
        r.note = r.note or "렌더는 됐으나 앵커가 없다(버튼/스크립트 내비게이션 추정)."
    elif r.candidates == 0:
        r.verdict = "약관 메뉴 미발견"
        r.note = r.note or "키워드로 안 잡힘(이미지 메뉴·다른 표현 가능성)."
    else:
        r.verdict = "메뉴 도달, PDF 미발견"
        r.note = r.note or "검색 폼·POST 뒤. 사이트별 설정 필요."
    return r


def main() -> None:
    results: list[Deep] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for insurer, host in HOSTS:
                x = probe(browser, insurer, host)
                results.append(x)
                print(
                    f"  {x.host}: p={x.pages} d={x.max_depth_reached} "
                    f"main={x.links_main} frame={x.links_frames} cand={x.candidates} "
                    f"pdf={x.pdf_links} txt={x.text_len} fr={x.frame_count} [{x.verdict}]"
                )
        finally:
            browser.close()

    stamp = date.today().isoformat()
    _OUT.mkdir(parents=True, exist_ok=True)
    (_OUT / f"{stamp}_약관발견_심층.json").write_text(
        json.dumps(
            {
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "max_pages_per_host": MAX_PAGES_PER_HOST,
                "max_depth": MAX_DEPTH,
                "results": [asdict(x) for x in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    hit = [x for x in results if x.pdf_links]
    lines = [
        f"# 약관 PDF 심층 탐색 + 미응답 진단 — {stamp}",
        "",
        f"- 도구: `scripts/crawl/probe_deep.py` (깊이 {MAX_DEPTH}, 호스트당 {MAX_PAGES_PER_HOST}페이지, "
        "**프레임 포함**, PDF 미다운로드)",
        "- 2차에서 갈린 두 문제를 같이 다룬다 — (A) 메뉴는 찾았는데 PDF 미발견 (B) 링크 0",
        "",
        "## 탐색 결과",
        "",
        "| 회사 | 깊이 | 페이지 | 메인링크 | **프레임링크** | 후보 | **PDF** | 판정 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for x in results:
        lines.append(
            f"| {x.insurer} | {x.max_depth_reached} | {x.pages} | {x.links_main} "
            f"| **{x.links_frames}** | {x.candidates} | **{x.pdf_links}** | {x.verdict} |"
        )
    lines += [
        "",
        "## 진단 (첫 페이지 기준)",
        "",
        "| 회사 | HTTP | 본문길이 | 프레임수 | 콘솔오류 | 제목 | 비고 |",
        "|---|---|---|---|---|---|---|",
    ]
    for x in results:
        lines.append(
            f"| {x.insurer} | {x.http_status} | {x.text_len} | {x.frame_count} "
            f"| {x.console_errors} | {x.title} | {x.note} |"
        )
    lines += [
        "",
        "## 요약",
        "",
        f"- PDF 링크가 잡힌 곳: **{len(hit)} / {len(results)}**",
        f"- PDF 링크 총계: **{sum(x.pdf_links for x in results)}건** (실손 약관인지는 미확인 — 식별 단계의 일)",
        f"- 프레임에서 추가로 걷은 링크: **{sum(x.links_frames for x in results)}건**",
        "",
        "## 발견된 PDF 샘플",
        "",
    ]
    for x in hit:
        lines.append(f"**{x.insurer}**")
        lines += [f"- `{u}`" for u in x.samples]
        lines.append("")
    (_OUT / f"{stamp}_약관발견_심층.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"OK saved: docs/reports/{stamp}_약관발견_심층.md")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
