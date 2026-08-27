"""약관 PDF 자동 발견 가능성 실측 (S7 선행 조사).

질문: "robots 허용 13곳에서 실손 약관을 **자동으로 전부** 받을 수 있는가?"

이 도구가 **하는 일**: 각 사이트의 첫 페이지와 후보 페이지 소수를 열어
**약관 PDF 링크가 정적 HTML에 존재하는지** 센다.

이 도구가 **하지 않는 일**:
- PDF를 내려받지 않는다. **링크가 있는지만** 본다.
- 깊이 2를 넘지 않고, 호스트당 페이지 수에 상한을 둔다. 사이트를 훑지 않는다.
- 발견한 링크가 '실손 약관'이라고 단정하지 않는다. 그건 식별 단계의 일이다.

robots 는 매번 재확인하고, 확인 실패·금지면 그 호스트를 건너뛰되 **결과에 그 사실을 남긴다**
(조용히 빠뜨리면 "해당 없음"과 구분되지 않는다).

실행: python -m scripts.crawl.probe_terms_pages
"""

from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

USER_AGENT = (
    "BarobomResearchBot/0.1 (+contact: set-before-deploy; purpose: insurance-terms-research)"
)
TIMEOUT = 25
RETRIES = 2
BACKOFF_SEC = 2.0
DELAY_SEC = 2.0
#: 호스트당 열어볼 페이지 수 상한(첫 페이지 포함). 사이트를 훑지 않기 위한 제한.
MAX_PAGES_PER_HOST = 6
MAX_HTML_BYTES = 3 * 1024 * 1024

_ROOT = Path(__file__).resolve().parents[2]
_OUT_DIR = _ROOT / "docs" / "reports"

#: robots 점검에서 '허용'으로 나온 13곳 (2026-07-31 기준).
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

#: 약관·공시 페이지로 이어질 법한 링크 키워드. 점수가 높은 순으로 따라간다.
_HINTS: tuple[tuple[str, int], ...] = (
    ("약관", 10),
    ("공시", 8),
    ("상품공시", 10),
    ("disclosure", 8),
    ("terms", 5),
    ("product", 3),
    ("보험료", 2),
    ("실손", 9),
)

_HREF = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_ANCHOR = re.compile(r'<a\b[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")


@dataclass
class HostProbe:
    insurer: str
    host: str
    robots: str = ""
    pages_fetched: int = 0
    total_links: int = 0
    pdf_links: int = 0
    hinted_links: int = 0
    sample_pdfs: list[str] = field(default_factory=list)
    #: 정적 크롤링으로 약관 PDF에 닿을 수 있는가에 대한 **실측 근거가 있는** 판정
    verdict: str = "미판정"
    note: str = ""


def _fetch(url: str, *, expect_html: bool = True) -> tuple[str | None, str]:
    """텍스트를 받아온다.

    ``expect_html=False`` 는 robots.txt 용이다 — robots 는 ``text/plain`` 이므로
    HTML 검사를 걸면 **전부 '확인 불가'가 되어 모든 호스트가 금지로 취급된다.**
    (실제로 첫 실행에서 13곳 전부 건너뛰어 이 버그를 발견했다.)
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = "시도 없음"
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                ctype = resp.headers.get("Content-Type", "")
                if expect_html and "html" not in ctype.lower():
                    return None, f"HTML 아님({ctype})"
                raw = resp.read(MAX_HTML_BYTES)
                charset = resp.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace"), f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            return None, f"HTTP {e.code}"
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            last = f"연결 실패: {type(e).__name__}"
        except Exception as e:  # noqa: BLE001
            last = f"오류: {type(e).__name__}"
        if attempt < RETRIES:
            time.sleep(BACKOFF_SEC * attempt)
    return None, last


def _robots_allows(host: str, url: str) -> tuple[bool, str]:
    """확인 실패는 '허용'이 아니다(RFC 9309)."""
    body, status = _fetch(f"https://{host}/robots.txt", expect_html=False)
    if body is None:
        if status == "HTTP 404":
            return True, "robots.txt 없음(규칙 부재)"
        return False, f"robots 확인 불가({status}) → 금지로 취급"
    rp = RobotFileParser()
    rp.parse(body.splitlines())
    ok = rp.can_fetch(USER_AGENT, url)
    return ok, f"{status} → {'allow' if ok else 'disallow'}"


def _score(text: str, href: str) -> int:
    blob = f"{text} {href}".lower()
    return sum(w for kw, w in _HINTS if kw.lower() in blob)


def probe(insurer: str, host: str) -> HostProbe:
    result = HostProbe(insurer=insurer, host=host)
    root = f"https://{host}/"

    allowed, verdict = _robots_allows(host, root)
    result.robots = verdict
    if not allowed:
        result.verdict = "건너뜀"
        result.note = "robots 재확인 실패 또는 금지. 조용히 빠뜨리지 않고 기록만 남긴다."
        return result

    seen: set[str] = set()
    queue: list[str] = [root]
    pdfs: list[str] = []

    while queue and result.pages_fetched < MAX_PAGES_PER_HOST:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        if result.pages_fetched:
            time.sleep(DELAY_SEC)

        html, status = _fetch(url)
        result.pages_fetched += 1
        if html is None:
            if result.pages_fetched == 1:
                result.note = f"첫 페이지 실패({status})"
            continue

        hrefs = [h for h in _HREF.findall(html)]
        result.total_links += len(hrefs)

        for h in hrefs:
            absolute = urljoin(url, h)
            if absolute.lower().split("?")[0].endswith(".pdf"):
                if absolute not in pdfs:
                    pdfs.append(absolute)

        if result.pages_fetched == 1:
            scored: list[tuple[int, str]] = []
            for h, inner in _ANCHOR.findall(html):
                text = _TAGS.sub(" ", inner).strip()
                s = _score(text, h)
                if s <= 0:
                    continue
                absolute = urljoin(url, h)
                if urlparse(absolute).netloc != host or absolute in seen:
                    continue
                scored.append((s, absolute))
            scored.sort(key=lambda x: -x[0])
            result.hinted_links = len(scored)
            queue.extend(u for _, u in scored[: MAX_PAGES_PER_HOST - 1])

    result.pdf_links = len(pdfs)
    result.sample_pdfs = pdfs[:3]

    if result.pdf_links > 0:
        result.verdict = "정적 링크 발견"
    elif result.hinted_links == 0 and result.total_links < 20:
        result.verdict = "정적 크롤링 불가 추정"
        result.note = result.note or "첫 페이지에 링크가 거의 없다(JS 렌더링 가능성)."
    else:
        result.verdict = "얕은 탐색으로는 미발견"
        result.note = result.note or "약관은 검색 폼·POST 뒤에 있을 가능성이 크다."
    return result


def main() -> None:
    results = [probe(name, host) for name, host in HOSTS]

    stamp = date.today().isoformat()
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    (_OUT_DIR / f"{stamp}_약관발견_실측.json").write_text(
        json.dumps(
            {
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "user_agent": USER_AGENT,
                "max_pages_per_host": MAX_PAGES_PER_HOST,
                "results": [asdict(r) for r in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    lines = [
        f"# 약관 PDF 자동 발견 가능성 실측 — {stamp}",
        "",
        f"- 도구: `scripts/crawl/probe_terms_pages.py` (호스트당 최대 {MAX_PAGES_PER_HOST}페이지, **PDF 미다운로드**)",
        "- 목적: 'robots 허용 13곳에서 약관을 자동으로 전부 받을 수 있는가'에 대한 실측",
        "- ★링크 발견 = 수집 가능이 아니다. 그 PDF가 실손 약관인지는 식별 단계에서 정한다.",
        "",
        "| 회사 | 호스트 | robots | 페이지 | 링크 | PDF | 판정 | 비고 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.insurer} | `{r.host}` | {r.robots} | {r.pages_fetched} | {r.total_links} "
            f"| **{r.pdf_links}** | {r.verdict} | {r.note} |"
        )

    found = [r for r in results if r.pdf_links > 0]
    lines += [
        "",
        "## 요약",
        "",
        f"- 정적 HTML에서 PDF 링크가 잡힌 곳: **{len(found)} / {len(results)}**",
        f"- 발견된 PDF 링크 합계: **{sum(r.pdf_links for r in results)}건** (실손 약관인지는 미확인)",
        "",
        "## 이 결과가 뜻하는 것",
        "",
        "얕은 탐색으로 PDF가 안 잡히는 사이트는 **약관이 검색 폼·POST·JS 렌더링 뒤에** 있다는 뜻이다.",
        "이런 사이트는 사이트별 전용 수집기를 따로 만들어야 하며, **한 번에 13곳을 자동 수집하는 것은 성립하지 않는다.**",
    ]
    (_OUT_DIR / f"{stamp}_약관발견_실측.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"OK saved: docs/reports/{stamp}_약관발견_실측.md")
    for r in results:
        print(f"  {r.host}: pages={r.pages_fetched} links={r.total_links} pdf={r.pdf_links} [{r.verdict}]")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
