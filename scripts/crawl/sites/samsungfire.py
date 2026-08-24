"""삼성화재 상품공시 수집기 — 사이트별 어댑터 1호.

★어떻게 뚫었나 (다른 사이트에도 그대로 적용할 방법)

    1. 홈페이지를 **렌더링**해서 '약관찾기/공시실' 링크를 찾는다  → `/vh/page/VH.HPIF0103.do`
    2. 그 페이지를 열고 **네트워크 요청을 관찰**한다               → `POST /vh/data/VH.HDIF0103.do`
    3. 화면을 긁지 않고 **그 데이터 엔드포인트를 직접 호출**한다

    즉 "화면 자동조작"이 아니라 "사이트가 자기 화면을 그릴 때 쓰는 데이터를 그대로 받는다".
    HTML 셀렉터에 의존하지 않으므로 화면 개편에 잘 견딘다.

이 엔드포인트가 주는 것(빈 POST 한 번에 전량):
    prdName · prdGun(장기/일반/자동차…) · prdCode · **saleStDt · saleEnDt** ·
    prdfilename1~3 (사업방법서 · 상품요약서 · 보험약관 PDF)

★`saleEnDt != '99991231'` 이면 **판매중지 상품**이다. 1~3세대 실손 약관이 여기 있다 —
계획서에서 "가장 필요한데 가장 안 구해진다"고 적었던 바로 그것이다.

수집 정책:
- robots 를 매번 재확인한다. 확인 실패·금지면 `InfraError` 로 명시적 실패(무폴백).
- 카탈로그(메타데이터)는 저작물이 아니므로 저장소에 커밋한다.
- **PDF 원문은 `data/raw/` 에만 두고 커밋하지 않는다**(약관은 저작물, 재배포 금지).
- 받은 PDF가 '무엇인지'는 여기서 판정하지 않는다. `identification="unidentified"` 로 남긴다.
  파일명이 `file1` 이라고 그것이 약관이라는 보장은 없다 — 상품마다 파일 순서가 다르다.

실행:
    python -m scripts.crawl.sites.samsungfire --catalog-only
    python -m scripts.crawl.sites.samsungfire --limit 20
"""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.robotparser import RobotFileParser

from app.core.errors import InfraError, ValidationErr

INSURER = "삼성화재"
HOST = "www.samsungfire.com"
CATALOG_URL = f"https://{HOST}/vh/data/VH.HDIF0103.do"
PDF_BASE = f"https://{HOST}"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 BarobomResearchBot/0.1 "
    "(+contact: set-before-deploy; purpose: insurance-terms-research)"
)
TIMEOUT = 40
DELAY_SEC = 2.0
MAX_BYTES = 60 * 1024 * 1024
#: 판매중을 뜻하는 종료일 센티널.
OPEN_ENDED = "99991231"
#: 보험약관이 실려 오는 슬롯. 실측으로 확인했다(§ fetch_catalog 주석).
TERMS_FILE_KEY = "prdfilename1"

_ROOT = Path(__file__).resolve().parents[3]
#: 보험사별로 나눈다 — 13곳으로 늘면 한 폴더에 수천 개가 쌓여 사람이 못 찾는다.
#: 폴더명은 영문 슬러그를 쓴다(한글 경로는 Windows/git 에서 인코딩이 깨진다).
INSURER_SLUG = "samsungfire"
_RAW = _ROOT / "data" / "raw" / "insurance_terms" / INSURER_SLUG
#: ★보험사별 매니페스트. 예전에는 전 보험사가 한 파일(`fetch_manifest.jsonl`)을
#:   같이 썼는데, 분리할 때 이 어댑터들을 안 고쳐서 **기록이 두 곳으로 갈라졌다.**
#:   그 바람에 "이미 받았다" 판정과 진행률이 서로 다른 파일을 보게 됐다.
_MANIFEST = _ROOT / "data" / "raw" / "manifests" / f"{INSURER_SLUG}.jsonl"
_CATALOG_DIR = _ROOT / "data" / "catalog"

#: 실손 후보 판별. 넓게 잡고 **확정은 하지 않는다**(식별 단계의 일).
_SILSON_HINTS = ("실손", "실손의료비", "실손의료보험")
#: 여행 실손은 세대 구분 대상이 아니고 우리 코호트와 성격이 다르다 — 전량 수집에서 제외한다.
_TRAVEL_HINTS = ("해외여행", "국내여행", "글로벌케어", "여행카드")


@dataclass(frozen=True)
class CatalogItem:
    insurer: str
    product_name: str
    product_group: str
    product_code: str
    sale_start: str
    sale_end: str
    pdf_paths: tuple[str, ...]

    @property
    def is_discontinued(self) -> bool:
        """판매중지 여부. ★1~3세대 약관은 여기에 있다."""
        return bool(self.sale_end) and self.sale_end != OPEN_ENDED

    @property
    def looks_like_silson(self) -> bool:
        return any(h in self.product_name for h in _SILSON_HINTS)

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
    #: 받았다는 이유로 무엇인지 안다고 하지 않는다.
    identification: str = "unidentified"


def _robots_allows(url: str) -> tuple[bool, str]:
    req = urllib.request.Request(
        f"https://{HOST}/robots.txt", headers={"User-Agent": USER_AGENT}
    )
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


def fetch_catalog() -> list[CatalogItem]:
    """상품공시 전량을 받는다. 화면을 긁지 않고 데이터 엔드포인트를 직접 부른다."""
    allowed, verdict = _robots_allows(CATALOG_URL)
    if not allowed:
        raise InfraError(f"robots가 허용하지 않습니다: {CATALOG_URL} ({verdict})")

    req = urllib.request.Request(
        CATALOG_URL,
        data=b"{}",
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise InfraError(f"카탈로그 수집 실패 HTTP {e.code}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"카탈로그 수집 실패({type(e).__name__})") from e

    body = payload.get("responseMessage", {}).get("body", {})
    if body.get("result") != "S":
        raise InfraError(f"카탈로그 응답이 성공이 아닙니다: result={body.get('result')!r}")
    rows = body.get("data", {}).get("list")
    if not rows:
        raise InfraError("카탈로그가 비어 있습니다. 응답 구조가 바뀌었을 수 있습니다.")

    items: list[CatalogItem] = []
    for row in rows:
        # ★file1 = 보험약관 (실측 확인). file2 = 사업방법서, file3 = 상품요약서.
        #   같은 상품의 3개 파일 표지를 열어 대조했다:
        #     file1 118~242쪽 "보험약관" / file2 8~14쪽 "(사업방법서 별지)" / file3 19~26쪽 "상품요약서"
        #   우리가 판정 근거로 쓸 수 있는 것은 약관뿐이므로 나머지는 받지 않는다.
        #   ※그래도 이 판정을 '확정'으로 쓰지 않는다 — 식별 단계에서 표지를 다시 교차검증한다.
        paths = tuple(row[k] for k in (TERMS_FILE_KEY,) if row.get(k))
        items.append(
            CatalogItem(
                insurer=INSURER,
                product_name=row.get("prdName", ""),
                product_group=row.get("prdGun", ""),
                product_code=row.get("prdCode", ""),
                sale_start=row.get("saleStDt", ""),
                sale_end=row.get("saleEnDt", ""),
                pdf_paths=paths,
            )
        )
    return items


def save_catalog(items: list[CatalogItem]) -> Path:
    """카탈로그는 **메타데이터**라 저작물이 아니다 — 저장소에 커밋한다."""
    _CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    out = _CATALOG_DIR / f"{date.today().isoformat()}_samsungfire_products.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(asdict(it), ensure_ascii=False) + "\n")
    return out


def download(item: CatalogItem, path: str) -> FetchRecord:
    url = PDF_BASE + path
    allowed, verdict = _robots_allows(url)
    if not allowed:
        raise InfraError(f"robots가 허용하지 않습니다: {url} ({verdict})")

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
            blob = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise InfraError(f"수집 실패 HTTP {e.code}: {url}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"수집 실패({type(e).__name__}): {url}") from e

    if len(blob) > MAX_BYTES:
        raise ValidationErr(f"상한 초과: {url}")
    if not blob:
        raise InfraError(f"빈 응답: {url}")

    digest = hashlib.sha256(blob).hexdigest()
    name = path.rsplit("/", 1)[-1]
    _RAW.mkdir(parents=True, exist_ok=True)
    saved = _RAW / f"{digest[:12]}_{name}"
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
    )


def already_fetched_urls() -> set[str]:
    """이미 받은 URL. 다시 받지 않는다(남의 서버에 같은 요청을 반복하지 않는다)."""
    if not _MANIFEST.exists():
        return set()
    seen: set[str] = set()
    for line in _MANIFEST.read_text(encoding="utf-8").splitlines():
        if line.strip():
            seen.add(json.loads(line)["url"])
    return seen


def select_all_silson(items: list[CatalogItem]) -> list[tuple[CatalogItem, str]]:
    """실손 전량. 여행 실손은 제외하고, 판매중지 상품도 **전부** 포함한다.

    판매중지가 곧 1~3세대 후보이므로 빼면 안 된다.
    """
    pool = [i for i in items if i.looks_like_silson and not i.is_travel and i.pdf_paths]
    pool.sort(key=lambda i: (i.sale_start or "", i.product_code))
    return [(i, p) for i in pool for p in i.pdf_paths]


def select_silson(items: list[CatalogItem], limit: int) -> list[tuple[CatalogItem, str]]:
    """실손 후보를 고른다.

    판매중과 판매중지를 **섞어서** 고른다. 판매중만 받으면 4·5세대만 모이고,
    정작 필요한 가입 시점 약관(1~3세대)이 하나도 안 들어온다.
    """
    silson = [i for i in items if i.looks_like_silson and i.pdf_paths]
    live = [i for i in silson if not i.is_discontinued]
    dead = [i for i in silson if i.is_discontinued]
    live.sort(key=lambda i: i.sale_start, reverse=True)
    dead.sort(key=lambda i: i.sale_start, reverse=True)

    # ★상품 단위로 고른 뒤 파일로 펼치면, 앞쪽 상품이 한도를 다 먹어 뒤쪽(판매중지)이
    #   한 건도 안 들어온다. 실제로 첫 실행에서 20건 전부 판매중만 받혔다.
    #   그래서 **파일 단위로 번갈아** 고른다.
    def _files(pool: list[CatalogItem]) -> list[tuple[CatalogItem, str]]:
        return [(i, p) for i in pool for p in i.pdf_paths]

    live_files, dead_files = _files(live), _files(dead)
    jobs: list[tuple[CatalogItem, str]] = []
    li = di = 0
    while len(jobs) < limit and (li < len(live_files) or di < len(dead_files)):
        if li < len(live_files):
            jobs.append(live_files[li])
            li += 1
        if len(jobs) < limit and di < len(dead_files):
            jobs.append(dead_files[di])
            di += 1
    return jobs[:limit]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog-only", action="store_true", help="목록만 저장하고 받지 않는다")
    ap.add_argument("--limit", type=int, default=0, help="내려받을 PDF 최대 수")
    ap.add_argument("--all", action="store_true", help="실손 전량 수집(여행 실손 제외)")
    ap.add_argument("--delay", type=float, default=DELAY_SEC, help="요청 간 지연(초)")
    args = ap.parse_args()

    items = fetch_catalog()
    out = save_catalog(items)
    silson = [i for i in items if i.looks_like_silson]
    print(f"카탈로그 {len(items):,}건 → {out.relative_to(_ROOT)}")
    print(f"  장기={sum(1 for i in items if i.product_group == '장기'):,}  "
          f"실손후보={len(silson):,}  판매중지={sum(1 for i in items if i.is_discontinued):,}")
    print(f"  실손후보 중 판매중지={sum(1 for i in silson if i.is_discontinued):,}")

    if args.catalog_only or (args.limit <= 0 and not args.all):
        print("\n(카탈로그만 저장. PDF는 받지 않았다.)")
        return

    if args.all:
        jobs = select_all_silson(items)
        mode = "실손 전량(여행 제외)"
    else:
        jobs = select_silson(items, args.limit)
        mode = "판매중/판매중지 혼합 표본"

    done = already_fetched_urls()
    before = len(jobs)
    jobs = [(i, p) for i, p in jobs if PDF_BASE + p not in done]
    print(f"\n대상 {before}건 중 이미 받은 {before - len(jobs)}건 제외 → {len(jobs)}건 수집 [{mode}]")
    records: list[FetchRecord] = []
    failures: list[tuple[str, str]] = []
    for n, (item, path) in enumerate(jobs):
        if n:
            time.sleep(args.delay)
        try:
            rec = download(item, path)
            records.append(rec)
            flag = "중지" if item.is_discontinued else "판매중"
            if n % 25 == 0 or not args.all:
                print(f"  [{n + 1}/{len(jobs)}] {flag} {item.product_name[:20]} "
                      f"{rec.bytes:,}B {rec.sha256[:10]}")
        except (InfraError, ValidationErr) as e:
            failures.append((path, str(e)))
            print(f"  [FAIL] {path}: {e}")

    if records:
        _MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        with _MANIFEST.open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    print(f"\n성공 {len(records)} / 실패 {len(failures)}")
    print("※ 받은 문서가 '무엇인지'는 아직 판정하지 않았다(identification=unidentified).")
    print("※ 원문은 data/raw/ 에만 두고 커밋하지 않는다(저작물).")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
