"""동양생명 상품공시 수집기 — 사이트별 어댑터 4호.

★뚫은 경로

    1. 홈페이지 → '공시실'이 **별도 도메인**이다  → `pbano.myangel.co.kr`
       (robots 를 새로 확인했다. `www` 기준 점검을 그대로 쓰면 안 된다 — 404 = 규칙 부재)
    2. 좌측 메뉴 버튼의 `value` 속성이 곧 경로다(href 가 아니라 button value)
         판매상품     `POST /paging/WE_AC_WEPAAP020100L`
         판매중지상품 `POST /paging/WE_AC_WEPAAP020201L`   ★1~3세대가 여기
    3. PDF: 표의 `MasFiledownload('_N','<FILE_GRP_ID>')` → `POST /process/CO_ComDownload`

★이 사이트의 장점: 표에 **판매기간 시작·종료가 둘 다** 있고 **`보험약관` 열이 따로** 있다.
    번호 / 판매채널 / 구분 / 상품명 / 판매기간(시작) / 판매기간(종료) /
    사업방법서 / **보험약관** / 운용관리계약서 / 상세보기
→ 어느 파일이 약관인지 **열 위치로 확정**된다(삼성화재의 file1/2/3 모호성이 없다).
  그래도 식별 단계에서 표지를 다시 교차검증한다 — 열 위치는 화면 개편에 바뀔 수 있다.

실행:
    python -m scripts.crawl.sites.myangel --catalog-only
    python -m scripts.crawl.sites.myangel --all
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

INSURER = "동양생명"
INSURER_SLUG = "myangel"
HOST = "pbano.myangel.co.kr"
BASE = f"https://{HOST}"
LIST_ON_SALE = f"{BASE}/paging/WE_AC_WEPAAP020100L"
LIST_STOPPED = f"{BASE}/paging/WE_AC_WEPAAP020201L"
DOWNLOAD_URL = f"{BASE}/process/CO_ComDownload"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 BarobomResearchBot/0.1 "
    "(+contact: set-before-deploy; purpose: insurance-terms-research)"
)
TIMEOUT = 40
DELAY_SEC = 1.0
MAX_BYTES = 60 * 1024 * 1024
MAX_PAGES = 60

_ROOT = Path(__file__).resolve().parents[3]
_RAW = _ROOT / "data" / "raw" / "insurance_terms" / INSURER_SLUG
#: ★보험사별 매니페스트. 예전에는 전 보험사가 한 파일(`fetch_manifest.jsonl`)을
#:   같이 썼는데, 분리할 때 이 어댑터들을 안 고쳐서 **기록이 두 곳으로 갈라졌다.**
#:   그 바람에 "이미 받았다" 판정과 진행률이 서로 다른 파일을 보게 됐다.
_MANIFEST = _ROOT / "data" / "raw" / "manifests" / f"{INSURER_SLUG}.jsonl"
_CATALOG_DIR = _ROOT / "data" / "catalog"

#: ★검색어를 추측하지 않는다. 빈 검색어로 **전체를 받아 로컬에서 거른다.**
#: '실손'만 던지면 10건, 어휘를 늘리면 38건이었다 — 어휘 목록이 맞는지 확인할 방법이 없다.
#: 전체를 받으면 "무엇을 놓쳤나"를 걱정하지 않아도 된다.
SEARCH_KEYWORDS = ("",)
SEARCH_KEYWORD = ""
#: 실손 후보 판별(로컬 필터). 넓게 잡고 확정은 식별 단계에서 한다.
_SILSON_HINTS = ("실손", "의료비", "노후실손", "유병력자")
_TRAVEL_HINTS = ("해외여행", "국내여행", "여행")

#: 표 열 순서. **상수로 박되 헤더로 검증**한다 — 개편되면 조용히 틀린 열을 읽으면 안 된다.
EXPECTED_HEADERS = ("상품명", "판매기간", "보험약관")
COL_NAME, COL_START, COL_END, COL_TERMS = 3, 4, 5, 7

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_FILEID = re.compile(r"MasFiledownload\(\s*'[^']*'\s*,\s*'([^']+)'", re.IGNORECASE)
_TAGS = re.compile(r"<[^>]+>")

_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_JAR))
#: 공시실 진입 페이지. 세션 없이 목록을 POST 하면 HTTP 404 가 온다(실측).
ENTRY_URL = f"{BASE}/notice/product/WE_PA_AP_01_00_00.jsp"
_SESSION_READY = False


def _ensure_session() -> None:
    global _SESSION_READY
    if _SESSION_READY:
        return
    req = urllib.request.Request(ENTRY_URL, headers={"User-Agent": USER_AGENT})
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            resp.read(1024)
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"세션 초기화 실패({type(e).__name__})") from e
    _SESSION_READY = True


def _text(html: str) -> str:
    return _TAGS.sub(" ", html).replace("&nbsp;", " ").replace("&amp;", "&").strip()


@dataclass(frozen=True)
class CatalogItem:
    insurer: str
    product_name: str
    product_code: str
    sale_start: str
    sale_end: str
    is_on_sale: bool
    terms_file_id: str

    @property
    def is_discontinued(self) -> bool:
        return not self.is_on_sale

    @property
    def is_travel(self) -> bool:
        return any(h in self.product_name for h in _TRAVEL_HINTS)

    @property
    def looks_like_silson(self) -> bool:
        return any(h in self.product_name for h in _SILSON_HINTS)


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


def _robots_allows(url: str) -> tuple[bool, str]:
    """★공시실이 별도 도메인이므로 그 도메인의 robots 를 본다."""
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


def _get(url: str, fields: dict[str, str]) -> bytes:
    """목록은 **GET + 쿼리스트링**이다.

    ★POST 로 보내면 HTTP 404 가 온다(실측). 브라우저가 화면 전이를 GET 으로 하기 때문이다.
    폼 필드가 있다고 POST 라고 단정한 것이 처음의 오류였다.
    """
    _ensure_session()
    q = urllib.parse.urlencode(fields, encoding="utf-8")
    req = urllib.request.Request(
        url + ("?" + q if q else ""),
        headers={"User-Agent": USER_AGENT, "Referer": ENTRY_URL},
    )
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            return resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise InfraError(f"요청 실패 HTTP {e.code}: {url}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"요청 실패({type(e).__name__}): {url}") from e


def _post_form(url: str, fields: dict[str, str]) -> bytes:
    """다운로드는 `downloadForm` 이 실제로 POST 한다."""
    _ensure_session()
    body = urllib.parse.urlencode(fields, encoding="utf-8").encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": LIST_STOPPED,
        },
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            return resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise InfraError(f"다운로드 실패 HTTP {e.code}: {url}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"다운로드 실패({type(e).__name__}): {url}") from e


def _check_headers(html: str) -> None:
    """열 위치를 상수로 쓰는 대신 **헤더로 검증**한다. 개편되면 즉시 실패시킨다."""
    head = html[: html.find("</thead>") + 8] if "</thead>" in html else html[:4000]
    missing = [h for h in EXPECTED_HEADERS if h not in head]
    if missing:
        raise InfraError(
            f"표 헤더가 예상과 다릅니다(누락: {missing}). 열 순서가 바뀌었을 수 있어 중단합니다."
        )


def _parse(html: str, *, on_sale: bool) -> list[CatalogItem]:
    items: list[CatalogItem] = []
    for row_html in _ROW.findall(html):
        cells_html = _CELL.findall(row_html)
        if len(cells_html) <= COL_TERMS:
            continue
        cells = [_text(c) for c in cells_html]
        name = cells[COL_NAME]
        if not name or name in ("상품명", "-"):
            continue
        m = _FILEID.search(cells_html[COL_TERMS])
        if not m:
            continue  # 약관 파일이 없는 행(구형 상품)은 건너뛴다
        items.append(
            CatalogItem(
                insurer=INSURER,
                product_name=name,
                product_code=cells[0],
                sale_start=cells[COL_START].replace(".", ""),
                sale_end=cells[COL_END].replace(".", ""),
                is_on_sale=on_sale,
                terms_file_id=m.group(1),
            )
        )
    return items


def fetch_catalog() -> list[CatalogItem]:
    allowed, verdict = _robots_allows(LIST_STOPPED)
    if not allowed:
        raise InfraError(f"robots가 허용하지 않습니다: {BASE} ({verdict})")

    out: dict[str, CatalogItem] = {}
    for keyword in SEARCH_KEYWORDS:
      for url, on_sale in ((LIST_ON_SALE, True), (LIST_STOPPED, False)):
        for page in range(1, MAX_PAGES + 1):
            # ★페이징은 GET 이 아니라 **POST** 다. 화면의 `PP_Query(form,'dw_99',N)` 가
            #   폼을 채워 submit 하는 것을 그대로 가로채 확인했다.
            #   `_paging_dw_99_*` 는 **-1** 이어야 한다(0 을 넣으면 HTTP 500).
            #   `pagenum` 은 1로 고정이고 페이지는 `_paging_page_idx` 로만 넘어간다.
            fields = {
                "_biz_op_code": "_Q",
                "_biz_dw_00": "",
                "seq_id": "",
                "pagenum": "1",
                "totalrc": "null",
                "sale_kind": "ALL",
                "goods_kind": "ALL",
                "nameStr": keyword,
                "tableKind": "1",
                "_paging_action": "PP",
                "_paging_dw_name": "dw_99",
                "_paging_row_idx": "0",
                "_paging_page_idx": str(page),
                "_paging_dw_99_row_idx": "-1",
                "_paging_dw_99_page_idx": "-1",
                "_paging_dw_99_total_row_count": "-1",
                "_paging_dw_list": "dw_99",
                "saleCode": "ALL",
                "goodsCode": "ALL",
                "searchWord": keyword,
                "_paging_dw_99_ds_fetch_count": "10",
            }
            html = _post_form(url, fields).decode("utf-8", errors="replace")
            if page == 1:
                _check_headers(html)
            rows = _parse(html, on_sale=on_sale)
            new = [r for r in rows if r.terms_file_id not in out]
            for r in new:
                out[r.terms_file_id] = r
            if not new:
                break
            time.sleep(DELAY_SEC)
    return list(out.values())


def already_fetched_urls() -> set[str]:
    if not _MANIFEST.exists():
        return set()
    return {
        json.loads(line)["url"]
        for line in _MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def terms_url(file_grp_id: str) -> str:
    """기록용 URL. 실제 요청은 POST 이지만 식별자로 이 형태를 쓴다."""
    return f"{DOWNLOAD_URL}?_biz_op_code=FDL&FILE_GRP_ID={urllib.parse.quote(file_grp_id)}"


def download(item: CatalogItem) -> FetchRecord:
    url = terms_url(item.terms_file_id)
    allowed, verdict = _robots_allows(DOWNLOAD_URL)
    if not allowed:
        raise InfraError(f"robots가 허용하지 않습니다: {DOWNLOAD_URL} ({verdict})")

    blob = _post_form(
        DOWNLOAD_URL,
        {
            "_biz_op_code": "FDL",
            "FILE_GRP_ID": item.terms_file_id,
            "FILE_PATH": "",
            "FILE_NAME": "",
            "USER_FILE_NM": "",
        },
    )
    if len(blob) > MAX_BYTES:
        raise ValidationErr(f"상한 초과: {item.product_name}")
    if not blob.startswith(b"%PDF"):
        raise InfraError(f"PDF가 아닙니다(앞 8바이트={blob[:8]!r}): {item.product_name}")

    digest = hashlib.sha256(blob).hexdigest()
    safe = re.sub(r'[\\/:*?"<>|]', "_", item.product_name)[:70]
    _RAW.mkdir(parents=True, exist_ok=True)
    saved = _RAW / f"{digest[:12]}_{safe}.pdf"
    saved.write_bytes(blob)

    return FetchRecord(
        insurer=INSURER,
        url=url,
        http_status=200,
        content_type="application/pdf",
        bytes=len(blob),
        sha256=digest,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        saved_as=str(saved.relative_to(_ROOT)).replace("\\", "/"),
        product_code=item.product_code,
        product_name=item.product_name,
        sale_start=item.sale_start,
        sale_end=item.sale_end,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog-only", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=DELAY_SEC)
    args = ap.parse_args()

    items = fetch_catalog()
    _CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    out = _CATALOG_DIR / f"{date.today().isoformat()}_myangel_products.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(asdict(it), ensure_ascii=False) + "\n")
    target = [i for i in items if i.looks_like_silson and not i.is_travel]
    print(f"카탈로그 {len(items)}건(전체) → {out.relative_to(_ROOT)}")
    print(f"  여행 제외 {len(target)}건 / 판매중지 {sum(1 for i in target if i.is_discontinued)}건")

    if args.catalog_only or (args.limit <= 0 and not args.all):
        print("\n(카탈로그만 저장. PDF는 받지 않았다.)")
        return

    done = already_fetched_urls()
    jobs = [i for i in target if terms_url(i.terms_file_id) not in done]
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"\n수집 대상 {len(jobs)}건")

    records: list[FetchRecord] = []
    failures: list[tuple[str, str]] = []
    for n, it in enumerate(jobs):
        if n:
            time.sleep(args.delay)
        try:
            rec = download(it)
            records.append(rec)
            if n % 10 == 0 or args.limit:
                flag = "중지" if it.is_discontinued else "판매중"
                print(f"  [{n + 1}/{len(jobs)}] {flag} {it.product_name[:26]} {rec.bytes:,}B")
        except (InfraError, ValidationErr) as e:
            failures.append((it.product_name, str(e)))

    if records:
        with _MANIFEST.open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    print(f"\n성공 {len(records)} / 실패 {len(failures)}")
    for nm, why in failures[:5]:
        print(f"  [FAIL] {nm[:28]}: {why[:70]}")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
