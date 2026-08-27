"""롯데손해보험 보험상품약관 수집기 — 사이트별 어댑터 8호.

★뚫은 경로

    /index2.jsp  →  <frameset>  →  **/web/main.jsp**   ← 실제 메뉴가 여기 있다
      → 보험상품약관  `/web/C/D/H/cdh190.jsp`
      → 조회는 전부 `POST /CChannelSvl` · `ops_tc=dfi.c.d.g.cmd.Cdg079Cmd`

★4단계를 **순서대로** 밟아야 한다 (여기서 오래 막혔다)

    | 단계 | task | 무엇이 오나 |
    |---|---|---|
    | 2 | `gostep2issale` / `gostep2isnotsale` | **상품 목록** (step3 링크 + 상품명) |
    | 3 | `gostep3issale` / `gostep3isnotsale` | 그 상품의 **판매기간 목록** |
    | 4 | `gostep4issale` / `gostep4isnotsale` | **약관·사업방법서·요약서 PDF 경로** |

    ★2단계를 건너뛰고 3단계부터 부르면 **응답이 0바이트**로 온다.
      서버가 세션에 이전 단계 상태를 들고 있기 때문이다.
      한참을 "프레임 target 때문"이라고 오해했는데, 실제 원인은 **호출 순서**였다.

★PDF 는 정적 경로다 — 파일명이 종류를 밝힌다

    /upload/C/newProduct/1_care_silson_5_2605_yak.pdf   ← **_yak = 약관**
    /upload/C/newProduct/1_care_silson_5_2605_sb_v2_….pdf  (사업방법서)
    /upload/C/newProduct/1_care_silson_5_2605_yoy.pdf      (요약서)

    슬롯 번호가 아니라 **파일명으로 고른다**(회사마다 순서가 달라 번호는 못 옮긴다).

★robots
    `Disallow` 가 2개뿐이고 공시실은 허용이다.
    ※처음에 브라우저로 열었다가 WAF 차단 화면을 보고 "불가"로 접었는데,
      그건 **브라우저 자동화에만** 걸린 것이고 파이썬 직접 요청은 정상이다.

실행:
    python -m scripts.crawl.sites.lotteins --catalog-only
    python -m scripts.crawl.sites.lotteins --all
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

INSURER = "롯데손해보험"
INSURER_SLUG = "lotteins"
HOST = "www.lotteins.co.kr"
BASE = f"https://{HOST}"
ENTRY_URL = f"{BASE}/web/C/D/H/cdh190.jsp"
API_URL = f"{BASE}/CChannelSvl"
OPS_TC = "dfi.c.d.g.cmd.Cdg079Cmd"
RTN_URI = "/web/C/D/H/cdh190_result.jsp"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 BarobomResearchBot/0.1 "
    "(+contact: set-before-deploy; purpose: insurance-terms-research)"
)
TIMEOUT = 50
DELAY_SEC = 0.6
MAX_BYTES = 60 * 1024 * 1024

#: 장기보험(lcode=03) 안에서 실손이 있는 중분류. 나머지는 저축·운전자·재물 등이다.
#: ★그래도 전 중분류를 훑고 **상품명으로 거른다** — 분류가 바뀔 수 있다.
LCODE = "03"
MCODES = ("01", "02", "03", "04", "05", "06")

_SILSON_HINTS = ("실손", "의료보험", "의료비")
_TRAVEL_HINTS = ("해외여행", "국내여행", "여행자")
#: ★약관 파일명 규칙(실측). 슬롯 번호가 아니라 파일명으로 고른다.
_TERMS_SUFFIX = "_yak"
_NOT_TERMS = ("_sb", "_yoy", "_sm")

_ROOT = Path(__file__).resolve().parents[3]
_RAW = _ROOT / "data" / "raw" / "insurance_terms" / INSURER_SLUG
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
_CATALOG_DIR = _ROOT / "data" / "catalog"

_STEP3 = re.compile(r"step3\('([^']*)','([^']*)','([^']*)'")
_STEP4 = re.compile(r"step4\('([^']*)','([^']*)','([^']*)','([^']*)'")
_NAME = re.compile(r"<span>([^<]{2,60})</span>")
_PDF = re.compile(r"(/upload/[A-Za-z0-9_\-/.]+\.pdf)", re.IGNORECASE)

_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_JAR))
_SESSION_READY = False


@dataclass(frozen=True)
class Product:
    insurer: str
    scode: str
    product_name: str
    mcode: str
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
    path: str

    @property
    def url(self) -> str:
        return BASE + self.path


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
    req = urllib.request.Request(
        ENTRY_URL, headers={"User-Agent": USER_AGENT, "Referer": f"{BASE}/web/main.jsp"}
    )
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            resp.read(1024)
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"세션 초기화 실패({type(e).__name__})") from e
    if not len(_JAR):
        raise InfraError("세션 쿠키를 받지 못했습니다. 단계 호출이 빈 응답을 냅니다.")
    _SESSION_READY = True


def _robots_allows(url: str) -> tuple[bool, str]:
    req = urllib.request.Request(f"{BASE}/robots.txt", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read(512_000).decode("utf-8-sig", errors="replace")
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


def _step(task: str, *, issale: str, mcode: str, scode: str = "", startdate: str = "") -> str:
    """단계 호출. ★반드시 2→3→4 순서로 불러야 한다(서버가 세션 상태를 본다)."""
    _ensure_session()
    fields = {
        "ops_tc": OPS_TC,
        "rtnUri": RTN_URI,
        "task": task,
        "issale": issale,
        "lcode": LCODE,
        "mcode": mcode,
        "scode": scode,
        "startdate": startdate,
        "srcPrdNm": "",
    }
    body = urllib.parse.urlencode(fields, encoding="euc-kr").encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": ENTRY_URL,
        },
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            return resp.read(MAX_BYTES + 1).decode("euc-kr", errors="replace")
    except urllib.error.HTTPError as e:
        raise InfraError(f"단계 호출 실패 HTTP {e.code}: {task}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"단계 호출 실패({type(e).__name__}): {task}") from e


def fetch_products() -> list[Product]:
    allowed, verdict = _robots_allows(ENTRY_URL)
    if not allowed:
        raise InfraError(f"robots가 허용하지 않습니다: {ENTRY_URL} ({verdict})")

    out: list[Product] = []
    for issale, tag in (("Y", "gostep2issale"), ("N", "gostep2isnotsale")):
        for mcode in MCODES:
            html = _step(tag, issale=issale, mcode=mcode)
            codes = [m[2] for m in _STEP3.findall(html)]
            names = [re.sub(r"\s+", " ", n).strip() for n in _NAME.findall(html)]
            #: ★코드와 이름 개수가 다르면 짝을 지을 수 없다. 조용히 넘어가지 않는다.
            if codes and len(codes) != len(names):
                raise InfraError(
                    f"상품 코드({len(codes)})와 이름({len(names)}) 개수가 다릅니다"
                    f"(m={mcode}, issale={issale}). 화면 구조가 바뀌었을 수 있습니다."
                )
            for code, name in zip(codes, names):
                out.append(
                    Product(
                        insurer=INSURER,
                        scode=code,
                        product_name=name,
                        mcode=mcode,
                        is_on_sale=(issale == "Y"),
                    )
                )
            time.sleep(DELAY_SEC)
    if not out:
        raise InfraError("상품을 하나도 얻지 못했습니다(구조 변경 의심).")
    return out


def fetch_terms(p: Product) -> list[TermsFile]:
    """★2단계를 먼저 부른 뒤 3·4단계로 간다. 순서를 지키지 않으면 빈 응답이 온다."""
    issale = "Y" if p.is_on_sale else "N"
    _step(f"gostep2{'issale' if p.is_on_sale else 'isnotsale'}", issale=issale, mcode=p.mcode)
    h3 = _step(
        f"gostep3{'issale' if p.is_on_sale else 'isnotsale'}",
        issale=issale, mcode=p.mcode, scode=p.scode,
    )
    out: list[TermsFile] = []
    for _l, _m, _s, startdate in _STEP4.findall(h3):
        h4 = _step(
            f"gostep4{'issale' if p.is_on_sale else 'isnotsale'}",
            issale=issale, mcode=p.mcode, scode=p.scode, startdate=startdate,
        )
        for path in dict.fromkeys(_PDF.findall(h4)):
            low = path.lower()
            if _TERMS_SUFFIX not in low or any(x in low for x in _NOT_TERMS):
                continue  # ★약관만 받는다(파일명으로 판별)
            out.append(TermsFile(product=p, sale_start=startdate, path=path))
        time.sleep(DELAY_SEC)
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
    req = urllib.request.Request(
        tf.url, headers={"User-Agent": USER_AGENT, "Referer": ENTRY_URL}
    )
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            status, ctype = resp.status, resp.headers.get("Content-Type", "")
            blob = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise InfraError(f"수집 실패 HTTP {e.code}: {tf.path}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"수집 실패({type(e).__name__}): {tf.path}") from e

    if len(blob) > MAX_BYTES:
        raise ValidationErr(f"상한 초과: {tf.path}")
    if not blob.startswith(b"%PDF"):
        raise InfraError(f"PDF가 아닙니다(앞 8바이트={blob[:8]!r}): {tf.path}")

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
        product_code=tf.product.scode,
        product_name=tf.product.product_name,
        sale_start=tf.sale_start,
        sale_end="",
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
    print(f"  판매중 {sum(1 for p in target if p.is_on_sale)} / 판매중지 {sum(1 for p in target if not p.is_on_sale)}")

    files: list[TermsFile] = []
    for n, p in enumerate(target):
        if args.limit and len(files) >= args.limit:
            break
        try:
            got = fetch_terms(p)
            files.extend(got)
            if n % 10 == 0:
                print(f"  [{n + 1}/{len(target)}] {p.product_name[:30]}: 약관 {len(got)}개 (누적 {len(files)})")
        except InfraError as e:
            print(f"  [상세실패] {p.product_name[:26]}: {e}")

    _CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    out = _CATALOG_DIR / f"{date.today().isoformat()}_lotteins_products.jsonl"
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
                print(f"  [{n + 1}/{len(jobs)}] {tf.product.product_name[:28]} {rec.bytes:,}B")
        except (InfraError, ValidationErr) as e:
            failures.append((tf.path, str(e)))

    if records:
        _MANIFESTS.mkdir(parents=True, exist_ok=True)
        with (_MANIFESTS / f"{INSURER_SLUG}.jsonl").open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    print(f"\n성공 {len(records)} / 실패 {len(failures)}")
    for nm, why in failures[:5]:
        print(f"  [FAIL] {nm[:40]}: {why[:60]}")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
