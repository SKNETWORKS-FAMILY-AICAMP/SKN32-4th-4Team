"""NH농협생명 상품공시 수집기 — 사이트별 어댑터 3호.

★뚫은 경로

    1. 홈페이지 렌더링 → '약관찾기'            → `/ho/on/HOON0004M00.nhl`
    2. 폼 제출·팝업 호출을 관찰
         목록   `POST /ho/on/HOON0004M00.nhl`   (prodnm, useyn, prsPagcn)  → HTML 표
         상세   `POST /ho/on/HOON0004P10.nhl`   (+selProdNm)              → 파일 링크
         PDF    `GET  /pdfViewer.nhl?apdFlid=<FILE_ID>&fileSeqn=<n>`
                ※ `pdfViewerPopup.nhl` 은 뷰어 화면(HTML)이다. 혼동하면 HTML 을 받게 된다.
    3. 화면을 긁지 않고 위 세 엔드포인트를 직접 호출

★삼성화재·DB손보와 다른 점 두 가지

    (1) 목록이 **JSON이 아니라 HTML**이다. 그래서 표를 파싱해야 한다.
        셀렉터에 의존하므로 **화면 개편에 약하다** — 파싱이 0건을 내면 조용히 넘어가지 않고
        `InfraError` 로 실패시킨다(0건과 '못 읽음'을 구분하기 위해).
    (2) 파일 슬롯 순서가 **삼성화재와 반대**다.
        삼성화재: file1=약관 / NH농협생명: fileSeqn 0=상품요약서, 1=사업방법서, **2=보험약관**
        → 슬롯 번호를 회사 간에 옮겨 쓰면 안 된다는 증거다. 링크 문구로 확인한다.

수집 정책은 다른 어댑터와 같다: robots 재확인, 약관만 수집, 원문 미커밋,
`identification="unidentified"`.

실행:
    python -m scripts.crawl.sites.nhlife --catalog-only
    python -m scripts.crawl.sites.nhlife --all
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.robotparser import RobotFileParser

from app.core.errors import InfraError, ValidationErr

INSURER = "NH농협생명"
INSURER_SLUG = "nhlife"
HOST = "www.nhlife.co.kr"
BASE = f"https://{HOST}"
LIST_URL = f"{BASE}/ho/on/HOON0004M00.nhl"
DETAIL_URL = f"{BASE}/ho/on/HOON0004P10.nhl"
#: ★`pdfViewerPopup.nhl` 은 **뷰어 화면(HTML)** 이고, 실제 PDF 바이트는 `pdfViewer.nhl` 이 준다.
#: 팝업 URL로 받으면 HTML 이 와서 매직바이트 검사에 걸린다(실제로 그렇게 실패했다).
PDF_URL = f"{BASE}/pdfViewer.nhl"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 BarobomResearchBot/0.1 "
    "(+contact: set-before-deploy; purpose: insurance-terms-research)"
)
TIMEOUT = 40
DELAY_SEC = 1.0
MAX_BYTES = 60 * 1024 * 1024
MAX_PAGES = 40

_ROOT = Path(__file__).resolve().parents[3]
_RAW = _ROOT / "data" / "raw" / "insurance_terms" / INSURER_SLUG
#: ★보험사별 매니페스트. 예전에는 전 보험사가 한 파일(`fetch_manifest.jsonl`)을
#:   같이 썼는데, 분리할 때 이 어댑터들을 안 고쳐서 **기록이 두 곳으로 갈라졌다.**
#:   그 바람에 "이미 받았다" 판정과 진행률이 서로 다른 파일을 보게 됐다.
_MANIFEST = _ROOT / "data" / "raw" / "manifests" / f"{INSURER_SLUG}.jsonl"
_CATALOG_DIR = _ROOT / "data" / "catalog"

SEARCH_KEYWORD = "실손"
_TRAVEL_HINTS = ("해외여행", "국내여행", "여행")

#: `popupPdfViewer('FILE_...', '2')` 와 그 링크 문구를 함께 잡는다.
#: 문구가 `>` 바로 뒤가 아니라 중첩 태그 안에 있을 수 있어 넓게 잡고 태그를 벗긴다.
_PDF_LINK = re.compile(
    r"popupPdfViewer\(\s*'([^']+)'\s*,\s*'(\d+)'\s*\)[^>]*>(.{0,200}?)</(?:a|button)>",
    re.IGNORECASE | re.DOTALL,
)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_BTN_VALUE = re.compile(r'<button[^>]*value="([^"]*)"', re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")


def _text(html: str) -> str:
    return _TAGS.sub(" ", html).replace("&nbsp;", " ").strip()


@dataclass(frozen=True)
class CatalogItem:
    insurer: str
    product_name: str
    product_category: str
    sale_status: str
    #: (fileSeqn, 링크문구). 약관이 몇 번인지는 **문구로 확인**한다.
    file_slots: tuple[tuple[str, str], ...] = ()
    file_id: str = ""

    @property
    def is_discontinued(self) -> bool:
        return "중지" in self.sale_status

    @property
    def is_travel(self) -> bool:
        return any(h in self.product_name for h in _TRAVEL_HINTS)

    def terms_slot(self) -> str | None:
        """보험약관 슬롯 번호. 문구에 '약관'이 있는 것을 고른다.

        ★슬롯 번호를 상수로 박지 않는다. 회사마다 순서가 다르고(삼성화재는 1번이 약관,
        NH농협생명은 3번째), 같은 회사도 상품마다 파일 수가 다를 수 있다.
        """
        for seqn, label in self.file_slots:
            if "약관" in label:
                return seqn
        return None


@dataclass(frozen=True)
class FetchRecord:
    insurer: str
    url: str
    http_status: int
    content_type: str
    bytes: int
    sha256: str
    fetched_at: str
    saved_as: str
    product_code: str
    product_name: str
    sale_start: str
    sale_end: str
    filename_kind_hint: str = "policy_terms"
    identification: str = "unidentified"


#: ★세션이 필요하다. 쿠키 없이 상세 팝업을 요청하면 스크립트 정의만 오고
#: 실제 파일 링크가 **비어서** 온다(0건과 구분이 안 되므로 반드시 세션을 먼저 만든다).
_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_JAR))
_SESSION_READY = False


def _ensure_session() -> None:
    """목록 페이지를 한 번 열어 세션 쿠키를 받는다."""
    global _SESSION_READY
    if _SESSION_READY:
        return
    req = urllib.request.Request(LIST_URL, headers={"User-Agent": USER_AGENT})
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            resp.read(1)
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"세션 초기화 실패({type(e).__name__})") from e
    if not len(_JAR):
        raise InfraError("세션 쿠키를 받지 못했습니다. 상세 조회가 빈 결과를 낼 수 있습니다.")
    _SESSION_READY = True


def _robots_allows(url: str) -> tuple[bool, str]:
    req = urllib.request.Request(f"{BASE}/robots.txt", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read(512_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True, "robots 없음(규칙 부재)"
        return False, f"robots HTTP {e.code}"
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return False, f"robots 확인 불가({type(e).__name__})"
    rp = RobotFileParser()
    rp.parse(body.splitlines())
    ok = rp.can_fetch(USER_AGENT, url)
    return ok, f"200 → {'allow' if ok else 'disallow'}"


def _post(url: str, fields: dict[str, str]) -> str:
    _ensure_session()
    body = urllib.parse.urlencode(fields, encoding="utf-8").encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": LIST_URL,
        },
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        raise InfraError(f"요청 실패 HTTP {e.code}: {url}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"요청 실패({type(e).__name__}): {url}") from e


def _parse_rows(html: str) -> list[CatalogItem]:
    items: list[CatalogItem] = []
    for row_html in _ROW.findall(html):
        cells = [_text(c) for c in _CELL.findall(row_html)]
        if len(cells) < 3:
            continue
        btn = _BTN_VALUE.search(row_html)
        name = btn.group(1) if btn else cells[1]
        if not name or "상품명" in name:
            continue
        items.append(
            CatalogItem(
                insurer=INSURER,
                product_name=name,
                product_category=cells[0],
                sale_status=cells[2],
            )
        )
    return items


def fetch_catalog() -> list[CatalogItem]:
    allowed, verdict = _robots_allows(LIST_URL)
    if not allowed:
        raise InfraError(f"robots가 허용하지 않습니다: {LIST_URL} ({verdict})")

    seen: dict[str, CatalogItem] = {}
    for page in range(1, MAX_PAGES + 1):
        html = _post(
            LIST_URL,
            {
                "prsPagcn": str(page),
                "selProdNm": "",
                "useyn": "",
                "prodDcd": "",
                "prodnm": SEARCH_KEYWORD,
            },
        )
        rows = _parse_rows(html)
        if page == 1 and not rows:
            # ★0건과 '파싱 실패'를 구분한다. HTML 구조가 바뀌면 조용히 0건이 되면 안 된다.
            raise InfraError(
                "첫 페이지에서 상품 행을 하나도 파싱하지 못했습니다. "
                "표 구조가 바뀌었을 수 있습니다(셀렉터 점검 필요)."
            )
        new = [r for r in rows if r.product_name not in seen]
        for r in new:
            seen[r.product_name] = r
        if not new:
            break
        time.sleep(DELAY_SEC)
    return list(seen.values())


def fetch_files(item: CatalogItem) -> CatalogItem:
    """상세 팝업에서 파일 슬롯과 링크 문구를 읽는다."""
    html = _post(
        DETAIL_URL,
        {
            "prsPagcn": "1",
            "selProdNm": item.product_name,
            "useyn": "",
            "prodDcd": "",
            "prodnm": SEARCH_KEYWORD,
        },
    )
    slots: list[tuple[str, str]] = []
    file_id = ""
    for fid, seqn, label in _PDF_LINK.findall(html):
        file_id = fid
        slots.append((seqn, _text(label)))
    if not slots:
        # ★0건과 '못 읽음'을 구분한다. 세션이 없으면 스크립트 정의만 오고 링크가 비어 온다.
        raise InfraError(
            f"상세에서 파일 링크를 하나도 읽지 못했습니다: {item.product_name!r} "
            "(세션 쿠키 또는 팝업 구조 확인 필요)"
        )
    return CatalogItem(
        insurer=item.insurer,
        product_name=item.product_name,
        product_category=item.product_category,
        sale_status=item.sale_status,
        file_slots=tuple(slots),
        file_id=file_id,
    )


def pdf_url(file_id: str, seqn: str) -> str:
    return f"{PDF_URL}?apdFlid={urllib.parse.quote(file_id)}&fileSeqn={seqn}"


def download(item: CatalogItem, seqn: str) -> FetchRecord:
    url = pdf_url(item.file_id, seqn)
    allowed, verdict = _robots_allows(url)
    if not allowed:
        raise InfraError(f"robots가 허용하지 않습니다: {url} ({verdict})")

    _ensure_session()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": LIST_URL})
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            status, ctype = resp.status, resp.headers.get("Content-Type", "")
            blob = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise InfraError(f"수집 실패 HTTP {e.code}: {item.product_name}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"수집 실패({type(e).__name__}): {item.product_name}") from e

    if len(blob) > MAX_BYTES:
        raise ValidationErr(f"상한 초과: {item.product_name}")
    if not blob.startswith(b"%PDF"):
        # 뷰어 페이지(HTML)가 돌아오는 경우가 있다. 조용히 저장하지 않는다.
        raise InfraError(
            f"PDF가 아닙니다(앞 8바이트={blob[:8]!r}, ctype={ctype}): {item.product_name}"
        )

    digest = hashlib.sha256(blob).hexdigest()
    safe = re.sub(r'[\\/:*?"<>|]', "_", item.product_name)[:80]
    _RAW.mkdir(parents=True, exist_ok=True)
    saved = _RAW / f"{digest[:12]}_{safe}.pdf"
    saved.write_bytes(blob)

    return FetchRecord(
        insurer=INSURER,
        url=url,
        http_status=status,
        content_type=ctype,
        bytes=len(blob),
        sha256=digest,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        saved_as=str(saved.relative_to(_ROOT)).replace("\\", "/"),
        product_code=item.file_id,
        product_name=item.product_name,
        # ★NH농협생명 목록에는 판매기간이 없다. 없는 것을 지어내지 않는다.
        sale_start="",
        sale_end="",
    )


def already_fetched_urls() -> set[str]:
    if not _MANIFEST.exists():
        return set()
    return {
        json.loads(line)["url"]
        for line in _MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog-only", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=DELAY_SEC)
    args = ap.parse_args()

    base = fetch_catalog()
    target = [i for i in base if not i.is_travel]
    print(f"목록 {len(base)}건 / 여행 제외 {len(target)}건 / 판매중지 {sum(1 for i in target if i.is_discontinued)}건")

    detailed: list[CatalogItem] = []
    for n, it in enumerate(target):
        if n:
            time.sleep(args.delay)
        try:
            detailed.append(fetch_files(it))
        except InfraError as e:
            print(f"  [상세실패] {it.product_name[:26]}: {e}")

    _CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    out = _CATALOG_DIR / f"{date.today().isoformat()}_nhlife_products.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for it in detailed:
            f.write(json.dumps(asdict(it), ensure_ascii=False) + "\n")
    with_terms = [i for i in detailed if i.terms_slot() is not None]
    print(f"상세 {len(detailed)}건 → {out.relative_to(_ROOT)}")
    print(f"  약관 슬롯 확인됨 {len(with_terms)}건")

    if args.catalog_only or (args.limit <= 0 and not args.all):
        print("\n(카탈로그만 저장. PDF는 받지 않았다.)")
        return

    done = already_fetched_urls()
    jobs = [(i, i.terms_slot()) for i in with_terms]
    jobs = [(i, s) for i, s in jobs if s and pdf_url(i.file_id, s) not in done]
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"\n수집 대상 {len(jobs)}건")

    records: list[FetchRecord] = []
    failures: list[tuple[str, str]] = []
    for n, (it, seqn) in enumerate(jobs):
        if n:
            time.sleep(args.delay)
        try:
            rec = download(it, seqn)
            records.append(rec)
            print(f"  [OK] {it.product_name[:30]} {rec.bytes:,}B")
        except (InfraError, ValidationErr) as e:
            failures.append((it.product_name, str(e)))

    if records:
        with _MANIFEST.open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    print(f"\n성공 {len(records)} / 실패 {len(failures)}")
    for nm, why in failures[:5]:
        print(f"  [FAIL] {nm[:30]}: {why[:80]}")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
