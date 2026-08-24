"""흥국화재 보험상품공시 수집기 — 사이트별 어댑터 7호.

★뚫은 경로

    목록  `POST /FRW/announce/insGoodsGongsiSale.do`
          (searchvalue=상품명, page=N, mode=판매/판매중지 탭)
          표: 구분 | 판매년도 | 상품명 | 판매일 | 첨부파일
    PDF   `POST /common/download.do`
          FILE_NAME=<경로+저장명>  FILE_EXT_NAME=<원본 파일명>  **TYPE=filedownX**

★`TYPE=filedownX` 가 빠지면 PDF 대신 오류 HTML(1,571B)이 온다 (실측).
  세 필드가 모두 있어야 받아진다 — 화면의 `fn_filedownX()` 가 그렇게 보낸다.

★막다른 길(기록)
    - `/Upload/gongsi/goods/<저장명>.pdf` 직접 요청 -> 1,542B 오류 페이지
    - `/FRW/common/fileDown.do`, `/common/fileDown.do` -> 95B
    - `fn_filedownX` 정의가 페이지에 없어 외부 JS(`/js/cyber_common.js`)에서 찾았다

★첨부파일 열에 `상품약관` 링크가 여러 개 붙는다
    `fn_filedownX('/Upload/gongsi/goods/', '<원본명>', '<저장명>')` 형태이고,
    **원본 파일명에 종류가 적혀 있다**(예: "…기초서류.pdf", "…약관.pdf").
    슬롯 번호가 아니라 **파일명으로 약관을 고른다** — 회사마다 순서가 다르기 때문이다.

실행:
    python -m scripts.crawl.sites.heungkukfire --catalog-only
    python -m scripts.crawl.sites.heungkukfire --all
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

INSURER = "흥국화재"
INSURER_SLUG = "heungkukfire"
HOST = "www.heungkukfire.co.kr"
BASE = f"https://{HOST}"
LIST_URL = f"{BASE}/FRW/announce/insGoodsGongsiSale.do"
DOWNLOAD_URL = f"{BASE}/common/download.do"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 BarobomResearchBot/0.1 "
    "(+contact: set-before-deploy; purpose: insurance-terms-research)"
)
TIMEOUT = 50
DELAY_SEC = 0.8
MAX_BYTES = 60 * 1024 * 1024
MAX_PAGES = 120

_ROOT = Path(__file__).resolve().parents[3]
_RAW = _ROOT / "data" / "raw" / "insurance_terms" / INSURER_SLUG
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
_CATALOG_DIR = _ROOT / "data" / "catalog"

#: 판매/판매중지 탭. 화면의 `mode` 값이다.
MODES = ("sale", "stop")
SEARCH_TERMS = ("실손", "의료비")
_TRAVEL_HINTS = ("해외여행", "국내여행", "여행자")

#: ★약관 판별은 **원본 파일명**으로 한다. 슬롯 번호를 쓰지 않는다.
_TERMS_HINTS = ("약관",)
#: 약관이 아닌 것이 확실한 문서(파일명에 이게 있으면 제외).
_NOT_TERMS = ("요약서", "사업방법서", "안내장", "설명서", "기초서류")

_FILEDOWN = re.compile(r"fn_filedownX\('([^']+)','([^']+)',\s*'([^']+)'\)")
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")

_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_JAR))
_SESSION_READY = False


def _text(html: str) -> str:
    return _TAGS.sub(" ", html).replace("&nbsp;", " ").replace("&amp;", "&").strip()


@dataclass(frozen=True)
class TermsFile:
    insurer: str
    product_name: str
    category: str
    sale_year: str
    sale_date: str
    is_on_sale: bool
    dir_path: str
    original_name: str
    saved_name: str

    @property
    def is_discontinued(self) -> bool:
        return not self.is_on_sale

    @property
    def is_travel(self) -> bool:
        return any(h in self.product_name for h in _TRAVEL_HINTS)

    @property
    def looks_like_terms(self) -> bool:
        """★파일명으로 약관을 고른다. 종류가 파일명에 적혀 있다."""
        n = self.original_name
        if any(x in n for x in _NOT_TERMS):
            return False
        return any(x in n for x in _TERMS_HINTS)

    @property
    def url(self) -> str:
        """기록용 식별자. 실제 요청은 POST 다."""
        return f"{DOWNLOAD_URL}?FILE_NAME={urllib.parse.quote(self.dir_path + self.saved_name)}"


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
    source_filename: str = ""
    filename_kind_hint: str = "policy_terms"
    identification: str = "unidentified"


def _ensure_session() -> None:
    global _SESSION_READY
    if _SESSION_READY:
        return
    req = urllib.request.Request(LIST_URL, headers={"User-Agent": USER_AGENT})
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            resp.read(1024)
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"세션 초기화 실패({type(e).__name__})") from e
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


def _post(url: str, fields: dict[str, str]) -> bytes:
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
            return resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise InfraError(f"요청 실패 HTTP {e.code}: {url}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"요청 실패({type(e).__name__}): {url}") from e


def _parse(html: str, *, on_sale: bool) -> list[TermsFile]:
    out: list[TermsFile] = []
    for row_html in _ROW.findall(html):
        calls = _FILEDOWN.findall(row_html)
        if not calls:
            continue
        cells = [_text(c) for c in _CELL.findall(row_html)]
        if len(cells) < 4:
            continue
        for dir_path, original, saved in calls:
            out.append(
                TermsFile(
                    insurer=INSURER,
                    category=cells[0],
                    sale_year=cells[1],
                    product_name=cells[2],
                    sale_date=cells[3],
                    is_on_sale=on_sale,
                    dir_path=dir_path,
                    original_name=original,
                    saved_name=saved,
                )
            )
    return out


def fetch_catalog() -> list[TermsFile]:
    allowed, verdict = _robots_allows(LIST_URL)
    if not allowed:
        raise InfraError(f"robots가 허용하지 않습니다: {LIST_URL} ({verdict})")

    seen: dict[str, TermsFile] = {}
    for term in SEARCH_TERMS:
        for mode in MODES:
            for page in range(1, MAX_PAGES + 1):
                html = _post(
                    LIST_URL,
                    {"searchvalue": term, "page": str(page), "mode": mode, "t_search": "1"},
                ).decode("utf-8", errors="replace")
                rows = _parse(html, on_sale=(mode == "sale"))
                new = [r for r in rows if r.saved_name not in seen]
                for r in new:
                    seen[r.saved_name] = r
                if not new:
                    break
                time.sleep(DELAY_SEC)
    if not seen:
        raise InfraError("첨부파일 링크를 하나도 파싱하지 못했습니다(구조 변경 의심).")
    return list(seen.values())


def already_fetched_urls() -> set[str]:
    if not _MANIFESTS.exists():
        return set()
    out: set[str] = set()
    for p in _MANIFESTS.glob("*.jsonl"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.add(json.loads(line)["url"])
    return out


def download(tf: TermsFile) -> FetchRecord:
    #: ★`TYPE=filedownX` 가 빠지면 PDF 대신 오류 HTML 이 온다(실측 1,571B).
    blob = _post(
        DOWNLOAD_URL,
        {
            "FILE_NAME": tf.dir_path + tf.saved_name,
            "FILE_EXT_NAME": tf.original_name,
            "TYPE": "filedownX",
        },
    )
    if len(blob) > MAX_BYTES:
        raise ValidationErr(f"상한 초과: {tf.original_name[:30]}")
    if not blob.startswith(b"%PDF"):
        raise InfraError(f"PDF가 아닙니다(앞 8바이트={blob[:8]!r}): {tf.original_name[:30]}")

    digest = hashlib.sha256(blob).hexdigest()
    safe = re.sub(r'[\\/:*?"<>|]', "_", tf.product_name)[:60]
    _RAW.mkdir(parents=True, exist_ok=True)
    saved = _RAW / f"{digest[:12]}_{tf.sale_date}_{safe}.pdf"
    saved.write_bytes(blob)

    return FetchRecord(
        insurer=INSURER,
        url=tf.url,
        http_status=200,
        content_type="application/pdf",
        bytes=len(blob),
        sha256=digest,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        saved_as=str(saved.relative_to(_ROOT)).replace("\\", "/"),
        product_code=tf.saved_name,
        product_name=tf.product_name,
        sale_start=tf.sale_date.replace("-", ""),
        sale_end="",
        source_filename=tf.original_name,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog-only", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=DELAY_SEC)
    args = ap.parse_args()

    files = fetch_catalog()
    target = [f for f in files if f.looks_like_terms and not f.is_travel]
    _CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    out = _CATALOG_DIR / f"{date.today().isoformat()}_heungkukfire_products.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for tf in files:
            fh.write(json.dumps(asdict(tf), ensure_ascii=False) + "\n")
    print(f"첨부파일 {len(files)}건 → {out.relative_to(_ROOT)}")
    print(f"  약관으로 보이는 것 {len(target)}건 / 판매중지 {sum(1 for f in target if f.is_discontinued)}건")

    if args.catalog_only or (args.limit <= 0 and not args.all):
        print("(카탈로그만 저장. PDF는 받지 않았다.)")
        return

    done = already_fetched_urls()
    jobs = [t for t in target if t.url not in done]
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"\n수집 대상 {len(jobs)}건")

    records: list[FetchRecord] = []
    failures: list[tuple[str, str]] = []
    for n, tf in enumerate(jobs):
        if n:
            time.sleep(args.delay)
        try:
            rec = download(tf)
            records.append(rec)
            if n % 10 == 0 or args.limit:
                print(f"  [{n + 1}/{len(jobs)}] {tf.product_name[:28]} {rec.bytes:,}B")
        except (InfraError, ValidationErr) as e:
            failures.append((tf.original_name[:30], str(e)))

    if records:
        _MANIFESTS.mkdir(parents=True, exist_ok=True)
        with (_MANIFESTS / f"{INSURER_SLUG}.jsonl").open("a", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    print(f"\n성공 {len(records)} / 실패 {len(failures)}")
    for nm, why in failures[:5]:
        print(f"  [FAIL] {nm}: {why[:70]}")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
