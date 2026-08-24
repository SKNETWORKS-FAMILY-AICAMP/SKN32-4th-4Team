"""흥국생명 상품공시 수집기 — 사이트별 어댑터 9호.

★공시실을 찾는 데 오래 걸렸다 (기록)

    홈페이지 HTML 에는 '공시' 문자열이 **없다**. `/front/*.do` 는 무엇을 요청하든
    같은 34,925B 셸을 돌려주고 `sitemap.xml` 239개에도 공시실이 없다.
    **브라우저로 렌더링하고 나서야** 링크가 보였다:

        공시실  `goPageMenu('N',' ','/front/public/manageList.do')`
          └ 상품공시 › 판매상품     `/front/public/saleProduct.do?searchFlgSale=Y`
                     › 판매중지상품 `/front/public/saleProduct.do?searchFlgSale=N`

★조회 (여기서 세 번 헛짚었다)

    `POST /front/public/saleProductAjax.do`

    1. **응답이 EUC-KR** 이다. UTF-8 로 읽으면 상품명이 깨져 '실손'을 못 찾는다
       (실제로 "의료 관련 0개"라는 잘못된 결론을 냈었다).
    2. **POST** 여야 한다. GET 으로 부르면 상품 목록만 오고 파일이 안 온다.
    3. 상품명은 `escape(encodeURIComponent(x))` — **이중 인코딩**이다
       (UTF-8 %XX 로 만든 뒤 `%` 를 다시 `%25` 로).

    응답은 `%||%` 로 세 부분이고 **세 번째가 파일 목록**이다.
    레코드는 `%|%`, 열은 `%,%` 로 나뉜다(10열):

        [0] 판매개시일  [1] 판매종료일  [2] 증권번호대  [3] 내부번호
        [4] **약관 암호화 경로**   [5] **약관 파일명**
        [6] 사업방법서 암호화 경로 [7] 사업방법서 파일명  …

★PDF: `POST /servlet/DownLoadEnc.do`  `encValue=<암호화 경로>`
    `fileName` 은 없어도 같은 바이트가 온다(실측).

실행:
    python -m scripts.crawl.sites.heungkuklife --catalog-only
    python -m scripts.crawl.sites.heungkuklife --all
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

INSURER = "흥국생명"
INSURER_SLUG = "heungkuklife"
HOST = "www.heungkuklife.co.kr"
BASE = f"https://{HOST}"
INDEX_URL = f"{BASE}/index.do"
GONGSI_URL = f"{BASE}/front/public/manageList.do"
LIST_URL = f"{BASE}/front/public/saleProduct.do"
AJAX_URL = f"{BASE}/front/public/saleProductAjax.do"
DOWNLOAD_URL = f"{BASE}/servlet/DownLoadEnc.do"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 BarobomResearchBot/0.1 "
    "(+contact: set-before-deploy; purpose: insurance-terms-research)"
)
TIMEOUT = 60
DELAY_SEC = 0.7
MAX_BYTES = 60 * 1024 * 1024

#: 대분류는 하나뿐이다(실측). 중분류는 I201~I209.
TYPE1 = "I101"
TYPE2_CODES = ("", "I201", "I202", "I203", "I204", "I205", "I206", "I207", "I208", "I209")

_SILSON_HINTS = ("실손", "의료비")
_TRAVEL_HINTS = ("해외여행", "국내여행", "여행자")
#: 파일 목록의 열 위치. **약관은 4·5열**이다(실측).
COL_START, COL_END, COL_TERMS_ENC, COL_TERMS_NAME = 0, 1, 4, 5

_ROOT = Path(__file__).resolve().parents[3]
_RAW = _ROOT / "data" / "raw" / "insurance_terms" / INSURER_SLUG
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
_CATALOG_DIR = _ROOT / "data" / "catalog"

_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_JAR))
_SESSION_READY = False


@dataclass(frozen=True)
class Product:
    insurer: str
    product_name: str
    is_on_sale: bool

    @property
    def is_travel(self) -> bool:
        return any(h in self.product_name for h in _TRAVEL_HINTS)

    @property
    def looks_like_silson(self) -> bool:
        return any(h in self.product_name for h in _SILSON_HINTS)


@dataclass(frozen=True)
class TermsFile:
    product: Product
    sale_start: str
    sale_end: str
    enc_value: str
    file_name: str

    @property
    def url(self) -> str:
        """기록용 식별자. 실제 요청은 POST 다."""
        return f"{DOWNLOAD_URL}?encValue={urllib.parse.quote(self.enc_value, safe='')}"


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
    """★공시실을 거쳐야 목록 조회가 된다. 순서대로 연다."""
    global _SESSION_READY
    if _SESSION_READY:
        return
    for url in (INDEX_URL, GONGSI_URL, f"{LIST_URL}?searchFlgSale=Y"):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with _OPENER.open(req, timeout=TIMEOUT) as resp:
                resp.read(2048)
        except Exception as e:  # noqa: BLE001
            raise InfraError(f"세션 초기화 실패({type(e).__name__}): {url}") from e
    _SESSION_READY = True


def _robots_allows(url: str) -> tuple[bool, str]:
    """★이 사이트의 robots.txt 는 Fasoo DRM 으로 암호화되어 **읽을 수 없다**.

    규칙을 확인할 수 없으므로 우리 원칙(확인 불가 시 금지)대로라면 중단해야 한다.
    그러나 파일이 암호화된 것은 **규칙이 없다는 뜻도, 금지라는 뜻도 아니다** —
    판단 자체가 불가능하다. 그래서 **사람이 확인할 사안으로 남기고**,
    이 어댑터는 실행할 때마다 그 사실을 출력한다.
    """
    req = urllib.request.Request(f"{BASE}/robots.txt", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(512_000)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True, "robots 없음(규칙 부재)"
        return False, f"robots HTTP {e.code}"
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return False, f"robots 확인 불가({type(e).__name__})"

    if raw[:7] == b"\xef\xbb\xbfDRMONE" or b"Fasoo DRM" in raw[:200] or b"DRMONE" in raw[:64]:
        return False, "★robots.txt 가 DRM 으로 암호화되어 읽을 수 없다 - 사람 확인 필요"
    body = raw.decode("utf-8-sig", errors="replace")
    rp = RobotFileParser()
    rp.parse(body.splitlines())
    ok = rp.can_fetch(USER_AGENT, url)
    return ok, f"200 → {'allow' if ok else 'disallow'}"


def _js_escape(s: str) -> str:
    """JS `escape(encodeURIComponent(x))` 를 그대로 흉내 낸다.

    ★이걸 안 하면 상품 상세가 오지 않는다(상품 목록만 온다).
    """
    return urllib.parse.quote(s, safe="").replace("%", "%25")


def _ajax(flg: str, *, type2: str = "", product: str = "") -> str:
    _ensure_session()
    params = (
        f"searchFlgSale={flg}&beforeYn=&searchCdPublicPrtType1={TYPE1}"
        f"&searchCdPublicPrtType2={type2}"
        f"&searchCdPublicPrtType3={_js_escape(product)}&searchText="
    )
    req = urllib.request.Request(
        AJAX_URL,
        data=params.encode("ascii"),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"{LIST_URL}?searchFlgSale={flg}",
            "X-Requested-With": "XMLHttpRequest",
        },
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            #: ★EUC-KR 이다. UTF-8 로 읽으면 상품명이 깨져 '실손'을 못 찾는다.
            return resp.read(MAX_BYTES + 1).decode("euc-kr", errors="replace")
    except urllib.error.HTTPError as e:
        raise InfraError(f"조회 실패 HTTP {e.code}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"조회 실패({type(e).__name__})") from e


def fetch_products(*, robots_confirmed: bool = False) -> list[Product]:
    allowed, verdict = _robots_allows(AJAX_URL)
    print(f"robots: {verdict}")
    if not allowed:
        if not robots_confirmed:
            #: ★기술적으로는 수집이 가능하다. 막는 것은 **준법 판단**이다.
            #: robots.txt 가 DRM 으로 암호화되어 규칙을 읽을 수 없으므로
            #: 우리가 "의도는 허용일 것"이라고 해석하지 않는다 — 사람이 정한다.
            raise InfraError(
                f"robots 를 근거로 수집을 진행할 수 없습니다: {verdict}\n"
                "  기술적으로는 수집이 가능하지만 그 판단은 사람이 해야 합니다.\n"
                "  운영자에게 확인한 뒤 --robots-confirmed 로 실행하세요."
            )
        print("  ★사람이 robots 를 확인했다고 선언하고 진행합니다(--robots-confirmed).")

    seen: dict[str, Product] = {}
    for flg in ("Y", "N"):
        for t2 in TYPE2_CODES:
            body = _ajax(flg, type2=t2)
            for rec in body.split("%||%")[0].split("%|%"):
                name = rec.split("%,%")[0].strip()
                if not name or "null" in name:
                    continue
                seen.setdefault(name, Product(INSURER, name, flg == "Y"))
            time.sleep(DELAY_SEC)
    if not seen:
        raise InfraError("상품을 하나도 얻지 못했습니다(구조 변경 의심).")
    return list(seen.values())


def fetch_terms(p: Product) -> list[TermsFile]:
    """상품 하나의 **판매기간별 약관**을 전부 읽는다."""
    body = _ajax("Y" if p.is_on_sale else "N", product=p.product_name)
    parts = body.split("%||%")
    if len(parts) < 3:
        return []
    out: list[TermsFile] = []
    for rec in parts[2].split("%|%"):
        cols = rec.split("%,%")
        if len(cols) <= COL_TERMS_NAME:
            continue
        enc = cols[COL_TERMS_ENC].strip()
        name = cols[COL_TERMS_NAME].strip()
        if not enc or not name or enc == "null":
            continue
        #: ★약관 열인지 파일명으로 교차확인한다. 열 위치만 믿지 않는다.
        if "약관" not in name:
            continue
        out.append(
            TermsFile(
                product=p,
                sale_start=cols[COL_START].replace(".", "").strip(),
                sale_end=cols[COL_END].replace(".", "").strip(),
                enc_value=enc,
                file_name=name,
            )
        )
    return out


def already_fetched_urls() -> set[str]:
    if not _MANIFESTS.exists():
        return set()
    got: set[str] = set()
    for path in _MANIFESTS.glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                got.add(json.loads(line)["url"])
    return got


def download(tf: TermsFile) -> FetchRecord:
    _ensure_session()
    body = urllib.parse.urlencode({"encValue": tf.enc_value}, encoding="euc-kr").encode()
    req = urllib.request.Request(
        DOWNLOAD_URL,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"{LIST_URL}?searchFlgSale=Y",
        },
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            status, ctype = resp.status, resp.headers.get("Content-Type", "")
            blob = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise InfraError(f"수집 실패 HTTP {e.code}: {tf.file_name[:30]}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"수집 실패({type(e).__name__}): {tf.file_name[:30]}") from e

    if len(blob) > MAX_BYTES:
        raise ValidationErr(f"상한 초과: {tf.file_name[:30]}")
    if not blob.startswith(b"%PDF"):
        raise InfraError(f"PDF가 아닙니다(앞 8바이트={blob[:8]!r}): {tf.file_name[:30]}")

    digest = hashlib.sha256(blob).hexdigest()
    safe = re.sub(r'[\\/:*?"<>|]', "_", tf.product.product_name)[:60]
    _RAW.mkdir(parents=True, exist_ok=True)
    saved = _RAW / f"{digest[:12]}_{tf.sale_start}_{safe}.pdf"
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
        product_code="",
        product_name=tf.product.product_name,
        sale_start=tf.sale_start,
        sale_end=tf.sale_end,
        source_filename=tf.file_name,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog-only", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=DELAY_SEC)
    ap.add_argument(
        "--robots-confirmed", action="store_true",
        help="robots.txt 를 사람이 직접 확인했음을 선언한다(DRM 암호화라 자동 판독 불가)",
    )
    args = ap.parse_args()

    products = fetch_products(robots_confirmed=args.robots_confirmed)
    target = [p for p in products if p.looks_like_silson and not p.is_travel]
    print(f"전체 상품 {len(products)}건 / 실손 후보 {len(target)}건")

    files: list[TermsFile] = []
    for n, p in enumerate(target):
        try:
            got = fetch_terms(p)
            files.extend(got)
            print(f"  [{n + 1}/{len(target)}] {p.product_name[:34]}: 약관 {len(got)}개")
        except InfraError as e:
            print(f"  [상세실패] {p.product_name[:30]}: {e}")
        time.sleep(args.delay)

    _CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    out = _CATALOG_DIR / f"{date.today().isoformat()}_heungkuklife_products.jsonl"
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
                print(f"  [{n + 1}/{len(jobs)}] {tf.sale_start} {rec.bytes:,}B")
        except (InfraError, ValidationErr) as e:
            failures.append((tf.file_name[:34], str(e)))

    if records:
        _MANIFESTS.mkdir(parents=True, exist_ok=True)
        with (_MANIFESTS / f"{INSURER_SLUG}.jsonl").open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    print(f"\n성공 {len(records)} / 실패 {len(failures)}")
    for nm, why in failures[:5]:
        print(f"  [FAIL] {nm}: {why[:60]}")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
