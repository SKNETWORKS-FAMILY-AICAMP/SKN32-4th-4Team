"""현대해상 보험상품공시 수집기 — 사이트별 어댑터 6호.

★뚫은 경로 (전부 JSON API. 브라우저가 필요 없다)

    1. 목록  `POST /ajax.xhi`  tranId=`HHCA0310M38S`
             {"header":{...,"menuId":"100932"},"request":{"searchData":"실손",...}}
             → `data.slYProdList`(판매중) / `data.slNProdList`(판매중지)
               각 항목에 **`clauApnflId`(약관 파일 ID)** 와 `slStDt`/`slEdDt`(판매기간)가 있다

    2. 파일  `POST /ajax.xhi`  tranId=`HHCA0310M26S`  {"apnflId": <위 ID>}
             → `savPath`, `savFileNm`, `flExts`, `originalFileNm`, `fileSz`

    3. PDF   `GET /FileActionServlet/download/0{savPath}/{savFileNm}.{flExts}`
             화면의 `getRestDownloadUrl()` 이 만드는 규칙 그대로다.
             (`0`=원본파일명 미지정, `1`=지정. 둘 다 같은 바이트가 온다)

★`gId` 는 필요 없다
    화면은 헤더에 세션성 `gId` 를 담아 보내지만, **빈 값으로도 응답한다**(실측).
    그래서 세션 토큰을 흉내 내지 않는다 — 빈 값을 그대로 보낸다.

★이 사이트의 장점
    - 목록 한 번에 **약관 파일 ID가 바로** 온다(상세 페이지 순회가 필요 없다)
    - **판매개시일·중지일이 둘 다** 있다
    - 판매중/판매중지가 **분리된 리스트**로 온다 — 추정할 필요가 없다
    - `originalFileNm` 이 `03.약관_20260101_...pdf` 처럼 **종류를 밝힌다**
      → 받은 파일이 약관인지 파일명으로 교차검증할 수 있다(그래도 확정은 식별 단계에서)

실행:
    python -m scripts.crawl.sites.hyundaimarine --catalog-only
    python -m scripts.crawl.sites.hyundaimarine --all
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

INSURER = "현대해상"
INSURER_SLUG = "hyundaimarine"
HOST = "www.hi.co.kr"
BASE = f"https://{HOST}"
ENTRY_URL = f"{BASE}/serviceAction.do"
API_URL = f"{BASE}/ajax.xhi"
FILE_URL = f"{BASE}/FileActionServlet/download/0"

TRAN_LIST = "HHCA0310M38S"
TRAN_FILE = "HHCA0310M26S"
MENU_ID = "100932"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 BarobomResearchBot/0.1 "
    "(+contact: set-before-deploy; purpose: insurance-terms-research)"
)
TIMEOUT = 50
DELAY_SEC = 0.7
MAX_BYTES = 60 * 1024 * 1024

_ROOT = Path(__file__).resolve().parents[3]
_RAW = _ROOT / "data" / "raw" / "insurance_terms" / INSURER_SLUG
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
_CATALOG_DIR = _ROOT / "data" / "catalog"

#: 검색어. 이 사이트는 상품명 부분일치 검색이라 여러 어휘를 던져 합집합을 만든다.
#: (빈 검색어는 전체를 주지 않는다 — 실측)
SEARCH_TERMS = ("실손", "의료비", "노후실손", "유병력자")
_TRAVEL_HINTS = ("해외여행", "국내여행", "여행자")

_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_JAR))
_SESSION_READY = False


@dataclass(frozen=True)
class Item:
    insurer: str
    product_name: str
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
    #: 사이트가 알려준 원본 파일명. 종류가 적혀 있어 교차검증에 쓴다.
    source_filename: str = ""
    filename_kind_hint: str = "policy_terms"
    identification: str = "unidentified"


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


#: robots.txt 조회 재시도. **우회가 아니라 확인을 확실히 하기 위한 것**이다.
_ROBOTS_TRIES = 3
_ROBOTS_BACKOFF_SEC = 2.0


def _robots_allows(url: str) -> tuple[bool, str]:
    """robots 판정. 확인이 끝내 안 되면 **거부로 본다**(fail-closed).

    ★왜 재시도하나

        연달아 요청하면 robots.txt 조회가 간헐적으로 `URLError` 로 떨어진다.
        그때마다 "robots 가 허용하지 않습니다"로 보고돼서, 사이트가 우리를 막은 줄
        알았다. 직접 불러 보면 `200 → allow` 였다(실측).

        **일시적 통신 실패와 실제 거부는 다르다.** 몇 번 더 물어보고 판정한다.
        그래도 안 되면 **거부로 둔다** — 확인 못 한 채 받으러 가지 않는다.
    """
    last = ""
    for attempt in range(_ROBOTS_TRIES):
        req = urllib.request.Request(
            f"{BASE}/robots.txt", headers={"User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read(512_000).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return True, "robots 없음(규칙 부재)"
            #: HTTP 상태가 온 것은 서버의 **답**이다. 재시도하지 않는다.
            return False, f"robots HTTP {e.code}"
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            last = f"{type(e).__name__}"
            if attempt < _ROBOTS_TRIES - 1:
                time.sleep(_ROBOTS_BACKOFF_SEC * (attempt + 1))
            continue
        rp = RobotFileParser()
        rp.parse(body.splitlines())
        ok = rp.can_fetch(USER_AGENT, url)
        return ok, f"200 → {'allow' if ok else 'disallow'}"
    return False, f"robots 확인 불가({last}, {_ROBOTS_TRIES}회 시도)"


def _api(tran: str, request: dict) -> dict:
    """★`gId` 를 빈 값으로 보낸다. 세션 토큰을 흉내 내지 않는다(빈 값으로도 응답한다)."""
    _ensure_session()
    payload = {
        "header": {
            "gId": "",
            "tranId": tran,
            "channelId": "HI-HOME",
            "clientIp": "127.0.0.1",
            "menuId": MENU_ID,
            "loginId": None,
        },
        "request": request,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json; charset=UTF-8",
            "Referer": ENTRY_URL,
            "X-Requested-With": "XMLHttpRequest",
        },
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        raise InfraError(f"API 실패 HTTP {e.code}: {tran}") from e
    except json.JSONDecodeError as e:
        raise InfraError(f"API 응답이 JSON 이 아닙니다: {tran}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"API 실패({type(e).__name__}): {tran}") from e


def fetch_catalog() -> list[Item]:
    allowed, verdict = _robots_allows(API_URL)
    if not allowed:
        raise InfraError(f"robots가 허용하지 않습니다: {API_URL} ({verdict})")

    seen: dict[str, Item] = {}
    for term in SEARCH_TERMS:
        data = _api(
            TRAN_LIST,
            {"searchData": term, "searchDate": "", "searchType": "0", "searchGb": "1"},
        ).get("data") or {}
        #: ★판매중/판매중지가 분리되어 온다. 추정하지 않고 그대로 쓴다.
        for key, on_sale in (("slYProdList", True), ("slNProdList", False)):
            for r in data.get(key) or []:
                fid = r.get("clauApnflId")
                if not fid:
                    continue  # 약관 파일이 없는 행
                seen[fid] = Item(
                    insurer=INSURER,
                    product_name=(r.get("prodNm") or "").strip(),
                    sale_start=(r.get("slStDt") or "").strip(),
                    sale_end=(r.get("slEdDt") or "").strip(),
                    is_on_sale=on_sale,
                    terms_file_id=fid,
                )
        time.sleep(DELAY_SEC)
    if not seen:
        # ★0건과 '구조 변경'을 구분한다.
        raise InfraError("어떤 검색어로도 약관 파일 ID를 얻지 못했습니다(API 응답 구조 확인 필요).")
    return list(seen.values())


def file_meta(file_id: str) -> dict:
    data = _api(TRAN_FILE, {"apnflId": file_id}).get("data") or {}
    for k in ("savPath", "savFileNm", "flExts"):
        if not data.get(k):
            raise InfraError(f"파일 메타에 {k} 가 없습니다: {file_id}")
    return data


def pdf_url(meta: dict) -> str:
    """화면의 `getRestDownloadUrl()` 규칙 그대로 만든다."""
    path = f"{meta['savPath']}/{urllib.parse.quote(meta['savFileNm'])}.{meta['flExts']}"
    return FILE_URL + path


def already_fetched_urls() -> set[str]:
    if not _MANIFESTS.exists():
        return set()
    out: set[str] = set()
    for p in _MANIFESTS.glob("*.jsonl"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.add(json.loads(line)["url"])
    return out


def download(item: Item) -> FetchRecord:
    meta = file_meta(item.terms_file_id)
    url = pdf_url(meta)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": ENTRY_URL})
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            status, ctype = resp.status, resp.headers.get("Content-Type", "")
            blob = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise InfraError(f"수집 실패 HTTP {e.code}: {item.product_name[:30]}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"수집 실패({type(e).__name__}): {item.product_name[:30]}") from e

    if len(blob) > MAX_BYTES:
        raise ValidationErr(f"상한 초과: {item.product_name[:30]}")
    if not blob.startswith(b"%PDF"):
        raise InfraError(f"PDF가 아닙니다(앞 8바이트={blob[:8]!r}): {item.product_name[:30]}")
    #: 사이트가 알려준 크기와 다르면 조용히 넘어가지 않는다.
    declared = meta.get("fileSz")
    if isinstance(declared, int) and declared and abs(declared - len(blob)) > 1024:
        raise InfraError(
            f"크기가 사이트 고지({declared:,}B)와 다릅니다({len(blob):,}B): {item.product_name[:30]}"
        )

    digest = hashlib.sha256(blob).hexdigest()
    safe = re.sub(r'[\\/:*?"<>|]', "_", item.product_name)[:60]
    _RAW.mkdir(parents=True, exist_ok=True)
    saved = _RAW / f"{digest[:12]}_{item.sale_start}_{safe}.pdf"
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
        product_code=item.terms_file_id,
        product_name=item.product_name,
        sale_start=item.sale_start,
        sale_end=item.sale_end,
        source_filename=meta.get("originalFileNm", ""),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog-only", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=DELAY_SEC)
    args = ap.parse_args()

    items = fetch_catalog()
    target = [i for i in items if not i.is_travel]
    _CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    out = _CATALOG_DIR / f"{date.today().isoformat()}_hyundaimarine_products.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(asdict(it), ensure_ascii=False) + "\n")
    print(f"카탈로그 {len(items)}건 → {out.relative_to(_ROOT)}")
    print(f"  여행 제외 {len(target)}건 / 판매중지 {sum(1 for i in target if i.is_discontinued)}건")

    if args.catalog_only or (args.limit <= 0 and not args.all):
        print("(카탈로그만 저장. PDF는 받지 않았다.)")
        return

    done = already_fetched_urls()
    jobs = list(target)
    if args.limit:
        jobs = jobs[: args.limit]
    print(f"\n수집 대상 {len(jobs)}건")

    records: list[FetchRecord] = []
    failures: list[tuple[str, str]] = []
    for n, it in enumerate(jobs):
        if n:
            time.sleep(args.delay)
        try:
            meta = file_meta(it.terms_file_id)
            if pdf_url(meta) in done:
                continue
            rec = download(it)
            records.append(rec)
            if n % 10 == 0 or args.limit:
                flag = "중지" if it.is_discontinued else "판매중"
                print(f"  [{n + 1}/{len(jobs)}] {flag} {it.product_name[:26]} {rec.bytes:,}B")
        except (InfraError, ValidationErr) as e:
            failures.append((it.product_name[:28], str(e)))

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
