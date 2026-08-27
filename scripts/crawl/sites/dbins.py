"""DB손해보험 상품공시 수집기 — 사이트별 어댑터 2호.

★뚫은 경로 (삼성화재와 같은 3단계)

    1. 홈페이지 렌더링 → '상품목록 및 기초서류(보험약관)' 링크  → `/FWMAIV1534.do`
    2. 그 페이지에서 검색을 실행하고 네트워크를 관찰
         → 카탈로그: `POST /insuPcPbanFindProductStep5_AX.do`
         → PDF:      `GET /cYakgwanDown.do?FilePath=InsProduct/<파일명>`
    3. 화면을 긁지 않고 두 엔드포인트를 직접 호출

카탈로그 요청 파라미터(페이지 JS에서 확인):
    searchCheck  ★'1'로 보내면 0건이다(실측). 판매/중지 토글이 아니다.
                 한 번의 응답에 둘 다 오고, 행의 `ARC_PDC_SL_YN` 으로 화면에서 나눈다.
    keyword      상품명 검색어(필수)
    beginDate/endDate  판매기간(선택, YYYYMMDD)

응답 한 행:
    PDC_NM           상품명
    SALE_BEGIN_DAY   판매개시일 (YYYY.MM.DD)
    INPL_FINM        ★보험약관 파일명
    CNSL_SMAR_FINM   상품요약서 파일명
    BIZ_MDDC_FINM    사업방법서 파일명
    SQNO             일련번호
    ARC_PDC_SL_YN    ★판매여부('1'=판매중 / '0'=판매중지). 1~3세대는 '0' 쪽이다.

★삼성화재와 결정적으로 다른 점: **파일명에 문서종류가 박혀 있다**(`약관_`/`요약_`/`사업_`).
삼성화재의 `file1/file2/file3` 모호성이 여기서는 없다. 그래도 **파일명을 근거로 확정하지는
않는다** — 식별 단계에서 표지·목차·본문을 교차검증한다. 파일명은 출처 힌트일 뿐이다.

실행:
    python -m scripts.crawl.sites.dbins --catalog-only
    python -m scripts.crawl.sites.dbins --all
"""

from __future__ import annotations

import argparse
import hashlib
import json
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

INSURER = "DB손해보험"
INSURER_SLUG = "dbins"
HOST = "www.idbins.com"
BASE = f"https://{HOST}"
CATALOG_URL = f"{BASE}/insuPcPbanFindProductStep5_AX.do"
PDF_URL = f"{BASE}/cYakgwanDown.do"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 BarobomResearchBot/0.1 "
    "(+contact: set-before-deploy; purpose: insurance-terms-research)"
)
TIMEOUT = 40
DELAY_SEC = 1.0
MAX_BYTES = 60 * 1024 * 1024
#: 보험약관 파일명이 담긴 필드. 나머지(요약서·사업방법서)는 수집 대상이 아니다.
TERMS_FIELD = "INPL_FINM"

_ROOT = Path(__file__).resolve().parents[3]
_RAW = _ROOT / "data" / "raw" / "insurance_terms" / INSURER_SLUG
#: ★보험사별 매니페스트. 예전에는 전 보험사가 한 파일(`fetch_manifest.jsonl`)을
#:   같이 썼는데, 분리할 때 이 어댑터들을 안 고쳐서 **기록이 두 곳으로 갈라졌다.**
#:   그 바람에 "이미 받았다" 판정과 진행률이 서로 다른 파일을 보게 됐다.
_MANIFEST = _ROOT / "data" / "raw" / "manifests" / f"{INSURER_SLUG}.jsonl"
_CATALOG_DIR = _ROOT / "data" / "catalog"

#: 검색어는 필수라 실손 관련어를 나눠 던진다.
SEARCH_KEYWORDS = ("실손", "실손의료비", "노후실손", "유병력자")
#: 여행 실손은 세대 구분 대상이 아니라 제외한다.
_TRAVEL_HINTS = ("해외여행", "국내여행", "해외장기체류", "인바운드유학생", "OUTDOOR", "프리미엄해외")


@dataclass(frozen=True)
class CatalogItem:
    insurer: str
    product_name: str
    product_code: str
    sale_start: str
    sale_end: str
    is_on_sale: bool
    #: (문서종류 힌트, 파일명). 힌트는 파일명에서 온 것이라 **확정이 아니다.**
    files: tuple[tuple[str, str], ...]

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
    #: 파일명이 '약관_'으로 시작한다는 것은 힌트이지 확정이 아니다.
    filename_kind_hint: str = ""
    identification: str = "unidentified"


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


def _kind_hint(filename: str) -> str:
    """파일명에서 문서종류 힌트를 읽는다. **확정이 아니다.**"""
    if "약관" in filename:
        return "policy_terms"
    if "요약" in filename:
        return "product_summary"
    if "사업" in filename:
        return "business_method"
    return "unknown"


def fetch_catalog(keyword: str) -> list[CatalogItem]:
    allowed, verdict = _robots_allows(CATALOG_URL)
    if not allowed:
        raise InfraError(f"robots가 허용하지 않습니다: {CATALOG_URL} ({verdict})")

    # ★엔드포인트는 JSON 을 받는다. form-urlencoded 로 보내면 HTTP 415 다(실측).
    payload = json.dumps(
        {
            "searchCheck": "0",
            "keyword": keyword,
            "beginDate": "",
            "endDate": "",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        CATALOG_URL,
        data=payload,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{BASE}/FWMAIV1534.do",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise InfraError(f"카탈로그 수집 실패 HTTP {e.code} (keyword={keyword})") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"카탈로그 수집 실패({type(e).__name__}, keyword={keyword})") from e

    rows = data.get("result")
    if rows is None:
        raise InfraError("응답에 result 가 없습니다. 구조가 바뀌었을 수 있습니다.")

    items: list[CatalogItem] = []
    for r in rows:
        # ★INPL_FINM 이 보험약관이다. 상품요약서(CNSL_SMAR_FINM)·사업방법서(BIZ_MDDC_FINM)는
        #   판정 근거가 될 수 없으므로 받지 않는다. DB손보는 파일명에 종류가 박혀 있어
        #   **받기 전에** 고를 수 있다(삼성화재는 그게 안 돼서 받은 뒤 정리해야 했다).
        files = tuple((_kind_hint(r[k]), r[k]) for k in (TERMS_FIELD,) if r.get(k))
        if not files:
            continue
        items.append(
            CatalogItem(
                insurer=INSURER,
                product_name=r.get("PDC_NM", ""),
                product_code=str(r.get("SQNO", "")),
                sale_start=(r.get("SALE_BEGIN_DAY") or "").replace(".", ""),
                sale_end="",  # ★DB손보 응답에는 판매종료일이 없다. 없는 것을 지어내지 않는다.
                # 판매여부는 요청 파라미터가 아니라 **응답 행**이 알려준다.
                is_on_sale=str(r.get("ARC_PDC_SL_YN", "")) == "1",
                files=files,
            )
        )
    return items


def already_fetched_urls() -> set[str]:
    if not _MANIFEST.exists():
        return set()
    return {
        json.loads(line)["url"]
        for line in _MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def pdf_url(filename: str) -> str:
    return f"{PDF_URL}?FilePath=InsProduct/{urllib.parse.quote(filename)}"


def download(item: CatalogItem, kind_hint: str, filename: str) -> FetchRecord:
    url = pdf_url(filename)
    allowed, verdict = _robots_allows(url)
    if not allowed:
        raise InfraError(f"robots가 허용하지 않습니다: {url} ({verdict})")

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": f"{BASE}/FWMAIV1534.do"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status, ctype = resp.status, resp.headers.get("Content-Type", "")
            blob = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise InfraError(f"수집 실패 HTTP {e.code}: {filename}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"수집 실패({type(e).__name__}): {filename}") from e

    if len(blob) > MAX_BYTES:
        raise ValidationErr(f"상한 초과: {filename}")
    if not blob:
        raise InfraError(f"빈 응답: {filename}")
    if not blob.startswith(b"%PDF"):
        # HTML 오류 페이지가 200으로 오는 경우가 있다. 조용히 저장하지 않는다.
        raise InfraError(f"PDF가 아닙니다(앞 8바이트={blob[:8]!r}): {filename}")

    digest = hashlib.sha256(blob).hexdigest()
    safe = filename.replace("/", "_").replace("\\", "_")
    _RAW.mkdir(parents=True, exist_ok=True)
    saved = _RAW / f"{digest[:12]}_{safe}"
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
        product_code=item.product_code,
        product_name=item.product_name,
        sale_start=item.sale_start,
        sale_end=item.sale_end,
        filename_kind_hint=kind_hint,
    )


def collect_catalog() -> list[CatalogItem]:
    """검색어가 필수라 여러 번 나눠 던지고 합친다."""
    seen: set[tuple[str, str]] = set()
    out: list[CatalogItem] = []
    for kw in SEARCH_KEYWORDS:
        for it in fetch_catalog(kw):
            for _, fn in it.files:
                key = (it.product_code, fn)
                if key in seen:
                    continue
                seen.add(key)
            out.append(it)
        time.sleep(DELAY_SEC)
    # 상품+파일 조합 기준으로 중복 제거
    uniq: dict[tuple[str, str], CatalogItem] = {}
    for it in out:
        uniq[(it.product_code, it.product_name)] = it
    return list(uniq.values())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog-only", action="store_true")
    ap.add_argument("--all", action="store_true", help="실손 전량 수집(여행 제외)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=DELAY_SEC)
    args = ap.parse_args()

    items = collect_catalog()
    _CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    out = _CATALOG_DIR / f"{date.today().isoformat()}_dbins_products.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(asdict(it), ensure_ascii=False) + "\n")

    target = [i for i in items if not i.is_travel]
    print(f"카탈로그 {len(items)}건 → {out.relative_to(_ROOT)}")
    print(f"  여행 제외 {len(target)}건 / 판매중지 {sum(1 for i in target if i.is_discontinued)}건")

    if args.catalog_only or (args.limit <= 0 and not args.all):
        print("\n(카탈로그만 저장. PDF는 받지 않았다.)")
        return

    jobs = [(i, k, f) for i in target for k, f in i.files]
    if not args.all:
        jobs = jobs[: args.limit]
    done = already_fetched_urls()
    before = len(jobs)
    jobs = [j for j in jobs if pdf_url(j[2]) not in done]
    print(f"\n대상 {before}건 중 이미 받은 {before - len(jobs)}건 제외 → {len(jobs)}건 수집")

    records: list[FetchRecord] = []
    failures: list[tuple[str, str]] = []
    for n, (item, kind, fn) in enumerate(jobs):
        if n:
            time.sleep(args.delay)
        try:
            rec = download(item, kind, fn)
            records.append(rec)
            if n % 20 == 0:
                print(f"  [{n + 1}/{len(jobs)}] {kind} {item.product_name[:22]} {rec.bytes:,}B")
        except (InfraError, ValidationErr) as e:
            failures.append((fn, str(e)))

    if records:
        _MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        with _MANIFEST.open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    print(f"\n성공 {len(records)} / 실패 {len(failures)}")
    for fn, why in failures[:5]:
        print(f"  [FAIL] {fn[:40]}: {why[:70]}")
    print("※ 파일명의 '약관_' 은 힌트일 뿐 확정이 아니다(identification=unidentified).")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
