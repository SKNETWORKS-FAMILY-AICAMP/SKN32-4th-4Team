"""KB손해보험 상품목록(약관) 수집기 — 사이트별 어댑터 5호.

★뚫은 경로

    홈페이지 → 공시실 → 보험상품공시 → **상품목록(약관)** `/CG802030001.ec`

      목록  `POST /CG802030001.ec`  (devonTargetRow = 1, 11, 21 … 10건씩)
      상세  `POST /CG802030002.ec`  (bojongNo, gubun, bojongSeq)
      PDF   `GET  /CG802030003.ec?fileNm=<판매개시일>_<상품코드>_<슬롯>.pdf`

★이 사이트의 큰 장점: **개정 이력이 통째로 있다**

    상세 페이지에 판매개시일별 파일이 전부 나열된다.
      20120401_10101_1.pdf / 20130401_10101_1.pdf / 20130701_10101_1.pdf …
    즉 **같은 상품의 모든 버전**을 한 번에 받을 수 있다. 버전 매칭에 그대로 쓸 수 있다.

★슬롯 확정 (실측 — 같은 상품의 세 파일을 열어 표지를 대조했다)

      _1 = **보험약관**   (68쪽, "…보통약관 제1관 목적 및 용어의 정의")
      _2 = 사업방법서      ( 1쪽, "사업방법서 별지")
      _3 = 상품요약서      ( 4쪽, "…상품요약서")

    ※회사마다 슬롯 순서가 다르다(삼성화재 file1=약관, NH농협생명 seqn2=약관,
      NH농협손보 seq1=약관). **번호를 회사 간에 옮겨 쓰면 안 된다.**
      그래서 이 어댑터는 슬롯 상수를 이 파일 안에만 둔다.

★검색어를 추측하지 않는다
    `goodsNm=실손` 으로 조회하면 0건이 나왔다(필드명이 화면 것과 다르다).
    전체를 받아 **로컬에서 거른다** — 동양생명에서 같은 교훈을 얻었다.

실행:
    python -m scripts.crawl.sites.kbinsure --catalog-only
    python -m scripts.crawl.sites.kbinsure --all
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

INSURER = "KB손해보험"
INSURER_SLUG = "kbinsure"
HOST = "www.kbinsure.co.kr"
BASE = f"https://{HOST}"
LIST_URL = f"{BASE}/CG802030001.ec"
DETAIL_URL = f"{BASE}/CG802030002.ec"
FILE_URL = f"{BASE}/CG802030003.ec"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 BarobomResearchBot/0.1 "
    "(+contact: set-before-deploy; purpose: insurance-terms-research)"
)
TIMEOUT = 45
DELAY_SEC = 0.8
MAX_BYTES = 60 * 1024 * 1024
#: 한 페이지 10건. `devonTargetRow` 는 **행 번호**(1, 11, 21 …)다.
ROWS_PER_PAGE = 10
MAX_PAGES = 300

#: ★보험약관 슬롯. 실측으로 확인했다(§ 모듈 독스트링).
TERMS_SLOT = "1"

_ROOT = Path(__file__).resolve().parents[3]
_RAW = _ROOT / "data" / "raw" / "insurance_terms" / INSURER_SLUG
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
_CATALOG_DIR = _ROOT / "data" / "catalog"

#: 실손 후보(로컬 필터). 넓게 잡고 확정은 식별 단계에서 한다.
_SILSON_HINTS = ("실손", "의료비", "노후실손", "유병력자")
_TRAVEL_HINTS = ("해외여행", "국내여행", "여행")

_DETAIL_CALL = re.compile(r"detail\('(\d+)','(\d+)','(\d+)'\)")
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_FILE_LINK = re.compile(r"fileNm=(\d{8})_(\d+)_(\d)\.pdf")
_TAGS = re.compile(r"<[^>]+>")

_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_JAR))
_SESSION_READY = False


def _text(html: str) -> str:
    return _TAGS.sub(" ", html).replace("&nbsp;", " ").replace("&amp;", "&").strip()


@dataclass(frozen=True)
class Product:
    insurer: str
    product_code: str
    gubun: str
    bojong_seq: str
    product_name: str
    insurance_kind: str
    sale_status: str

    @property
    def is_discontinued(self) -> bool:
        return "중지" in self.sale_status

    @property
    def is_travel(self) -> bool:
        return any(h in self.product_name for h in _TRAVEL_HINTS)

    @property
    def looks_like_silson(self) -> bool:
        return any(h in self.product_name for h in _SILSON_HINTS)


@dataclass(frozen=True)
class TermsFile:
    """한 상품의 **한 판매기간 버전**에 붙은 약관 파일."""

    product: Product
    sale_start: str
    sale_end: str
    file_name: str

    @property
    def url(self) -> str:
        return f"{FILE_URL}?fileNm={self.file_name}"


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
            charset = resp.headers.get_content_charset() or "euc-kr"
            return resp.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        raise InfraError(f"요청 실패 HTTP {e.code}: {url}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"요청 실패({type(e).__name__}): {url}") from e


def _parse_list(html: str) -> list[Product]:
    items: list[Product] = []
    for row_html in _ROW.findall(html):
        m = _DETAIL_CALL.search(row_html)
        if not m:
            continue
        cells = [_text(c) for c in _CELL.findall(row_html)]
        if len(cells) < 4:
            continue
        items.append(
            Product(
                insurer=INSURER,
                product_code=m.group(1),
                gubun=m.group(2),
                bojong_seq=m.group(3),
                sale_status=cells[0],
                insurance_kind=cells[1],
                product_name=cells[3],
            )
        )
    return items


def fetch_products() -> list[Product]:
    """전체 상품 목록. ★검색어로 좁히지 않고 전부 받아 로컬에서 거른다."""
    allowed, verdict = _robots_allows(LIST_URL)
    if not allowed:
        raise InfraError(f"robots가 허용하지 않습니다: {LIST_URL} ({verdict})")

    seen: dict[str, Product] = {}
    for page in range(MAX_PAGES):
        row = page * ROWS_PER_PAGE + 1
        html = _post(
            LIST_URL,
            {
                "devonTargetRow": str(row),
                "gubun": "",
                "goodsNm": "",
                "onsaleYn": "",
                "bojongNo": "",
                "bojongSeq": "",
            },
        )
        rows = _parse_list(html)
        if page == 0 and not rows:
            # ★0건과 '못 읽음'을 구분한다.
            raise InfraError("첫 페이지에서 상품을 하나도 파싱하지 못했습니다(구조 변경 의심).")
        new = [r for r in rows if r.product_code not in seen]
        for r in new:
            seen[r.product_code] = r
        if not new:
            break
        time.sleep(DELAY_SEC)
    return list(seen.values())


def fetch_terms_files(p: Product) -> list[TermsFile]:
    """상세에서 **판매기간별 약관 파일**을 모두 읽는다(개정 이력)."""
    html = _post(
        DETAIL_URL,
        {
            "devonTargetRow": "1",
            "bojongNo": p.product_code,
            "gubun": p.gubun,
            "bojongSeq": p.bojong_seq,
            "goodsNm": "",
            "onsaleYn": "",
        },
    )
    out: list[TermsFile] = []
    for row_html in _ROW.findall(html):
        links = _FILE_LINK.findall(row_html)
        if not links:
            continue
        cells = [_text(c) for c in _CELL.findall(row_html)]
        start = cells[0] if cells else ""
        end = cells[1] if len(cells) > 1 else ""
        for day, code, slot in links:
            if slot != TERMS_SLOT:
                continue  # ★약관만 받는다
            out.append(
                TermsFile(
                    product=p,
                    sale_start=start or day,
                    sale_end=end,
                    file_name=f"{day}_{code}_{slot}.pdf",
                )
            )
    return out


def already_fetched_urls() -> set[str]:
    if not _MANIFESTS.exists():
        return set()
    out: set[str] = set()
    for path in _MANIFESTS.glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.add(json.loads(line)["url"])
    return out


def download(tf: TermsFile) -> FetchRecord:
    _ensure_session()
    allowed, verdict = _robots_allows(tf.url)
    if not allowed:
        raise InfraError(f"robots가 허용하지 않습니다: {tf.url} ({verdict})")
    req = urllib.request.Request(tf.url, headers={"User-Agent": USER_AGENT, "Referer": DETAIL_URL})
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            status, ctype = resp.status, resp.headers.get("Content-Type", "")
            blob = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise InfraError(f"수집 실패 HTTP {e.code}: {tf.file_name}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"수집 실패({type(e).__name__}): {tf.file_name}") from e

    if len(blob) > MAX_BYTES:
        raise ValidationErr(f"상한 초과: {tf.file_name}")
    if not blob.startswith(b"%PDF"):
        raise InfraError(f"PDF가 아닙니다(앞 8바이트={blob[:8]!r}): {tf.file_name}")

    digest = hashlib.sha256(blob).hexdigest()
    safe = re.sub(r'[\\/:*?"<>|]', "_", tf.product.product_name)[:60]
    _RAW.mkdir(parents=True, exist_ok=True)
    saved = _RAW / f"{digest[:12]}_{tf.file_name[:-4]}_{safe}.pdf"
    saved.write_bytes(blob)

    return FetchRecord(
        insurer=INSURER,
        url=tf.url,
        http_status=status,
        content_type=ctype,
        bytes=len(blob),
        sha256=digest,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        saved_as=str(saved.relative_to(_ROOT)).replace("\\", "/"),
        product_code=tf.product.product_code,
        product_name=tf.product.product_name,
        sale_start=tf.sale_start,
        sale_end=tf.sale_end,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog-only", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=DELAY_SEC)
    args = ap.parse_args()

    products = fetch_products()
    target = [p for p in products if p.looks_like_silson and not p.is_travel]
    print(f"전체 상품 {len(products)}건 / 실손 후보 {len(target)}건")

    files: list[TermsFile] = []
    for n, p in enumerate(target):
        if n:
            time.sleep(args.delay)
        try:
            got = fetch_terms_files(p)
            files.extend(got)
            print(f"  {p.product_name[:30]}: 약관 버전 {len(got)}개")
        except InfraError as e:
            print(f"  [상세실패] {p.product_name[:26]}: {e}")

    _CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    out = _CATALOG_DIR / f"{date.today().isoformat()}_kbinsure_products.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for tf in files:
            f.write(json.dumps(asdict(tf), ensure_ascii=False) + "\n")
    print(f"\n약관 파일 {len(files)}건 → {out.relative_to(_ROOT)}")

    if args.catalog_only or (args.limit <= 0 and not args.all):
        print("(카탈로그만 저장. PDF는 받지 않았다.)")
        return

    done = already_fetched_urls()
    jobs = [t for t in files if t.url not in done]
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"수집 대상 {len(jobs)}건")

    records: list[FetchRecord] = []
    failures: list[tuple[str, str]] = []
    for n, tf in enumerate(jobs):
        if n:
            time.sleep(args.delay)
        try:
            rec = download(tf)
            records.append(rec)
            if n % 10 == 0 or args.limit:
                print(f"  [{n + 1}/{len(jobs)}] {tf.file_name} {rec.bytes:,}B")
        except (InfraError, ValidationErr) as e:
            failures.append((tf.file_name, str(e)))

    if records:
        _MANIFESTS.mkdir(parents=True, exist_ok=True)
        with (_MANIFESTS / f"{INSURER_SLUG}.jsonl").open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    print(f"\n성공 {len(records)} / 실패 {len(failures)}")
    for nm, why in failures[:5]:
        print(f"  [FAIL] {nm}: {why[:70]}")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
