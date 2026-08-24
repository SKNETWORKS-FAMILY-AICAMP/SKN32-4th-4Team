"""브라우저 렌더링 수집기 — API 직접 호출이 막힌 사이트용 공통 엔진.

★왜 필요한가

    지금까지 4개사(삼성화재·DB손보·NH농협생명·동양생명)는 **데이터 엔드포인트를 직접 호출**해
    뚫었다. 그게 빠르고 화면 개편에 강하다. 그러나 그 방법이 안 통하는 곳이 있다.

      삼성생명   요청 본문이 **암호화**되어 있다(`g=…&b=…`). 페이로드를 만들 수 없다.
      NH농협손보  선택 UI 가 커스텀이라 AJAX 를 잡지 못했다.

    이런 곳은 **브라우저가 알아서 암호화·선택을 처리하게 하고** 결과만 받는다.
    우회가 아니라 **사이트가 의도한 경로(브라우저)를 그대로 쓰는 것**이다.

★이 방식의 대가 (정직 기록)

    - 화면 구조(셀렉터)에 의존하므로 **개편에 약하다.** 그래서 셀렉터가 0건을 내면
      조용히 넘어가지 않고 `InfraError` 로 실패시킨다 — '없음'과 '못 찾음'은 다르다.
    - 느리다. 한 건당 수 초가 걸린다.
    - 그래서 **API 가 되는 곳에는 쓰지 않는다.** 마지막 수단이다.

★설정만 추가하면 사이트가 늘어난다
    `SITES` 에 `SiteConfig` 를 하나 넣으면 된다. 엔진은 공통이다.

실행:
    python -m scripts.crawl.browser_collector --site samsunglife --probe
    python -m scripts.crawl.browser_collector --site samsunglife --limit 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.robotparser import RobotFileParser

from playwright.sync_api import Error as PWError
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from app.core.errors import ConfigError, InfraError, ValidationErr

_ROOT = Path(__file__).resolve().parents[2]
_RAW = _ROOT / "data" / "raw" / "insurance_terms"
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
_CATALOG = _ROOT / "data" / "catalog"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 BarobomResearchBot/0.1 "
    "(+contact: set-before-deploy; purpose: insurance-terms-research)"
)
NAV_TIMEOUT_MS = 45_000
MAX_BYTES = 60 * 1024 * 1024
#: 남의 서버에 부하를 주지 않기 위한 행 간 대기.
ROW_DELAY_MS = 1_200
#: 이미지·폰트는 받지 않는다.
_BLOCK = {"image", "font", "media"}


@dataclass(frozen=True)
class SiteConfig:
    """사이트 하나를 뚫는 데 필요한 최소 정보."""

    insurer: str
    slug: str
    host: str
    entry_url: str
    #: 결과 표. 한 페이지에 표가 여러 개면 이걸로 먼저 고른다(삼성생명은 3개다).
    table_selector: str
    #: 표 **안에서** 행. 이 셀렉터가 0건이면 실패시킨다.
    row_selector: str
    #: 행 안에서 **보험약관** 링크. 문구로 고르는 것이 안전하다
    #: (열 순서는 회사마다 다르다 — 삼성화재 file1=약관, NH농협생명은 3번째였다).
    terms_link_selector: str
    #: 상품명이 들어 있는 셀.
    name_cell_selector: str
    #: 판매기간 셀. ★있으면 (상품명, 판매기간)으로 중복을 가린다.
    #: 상품명만 보면 **같은 상품의 다른 개정판까지** 건너뛴다(실측: 수집 0건이 됐다).
    period_cell_selector: str | None = None
    #: 검색이 필요한 경우.
    search_input_selector: str | None = None
    search_terms: tuple[str, ...] = ()
    submit_selector: str | None = None
    #: 판매중지 탭 등 추가로 눌러야 하는 것.
    pre_click_selectors: tuple[str, ...] = ()
    #: ★접힌 메뉴 안의 링크는 클릭이 안 된다(Playwright 가 가시성을 기다리다 타임아웃).
    #: 그럴 때 사이트가 제공하는 함수를 **그대로 호출**한다. 우리가 만든 요청이 아니라
    #: 화면이 부르는 그 함수다 — 페이로드를 위조하지 않는다.
    pre_eval_js: tuple[str, ...] = ()
    settle_ms: int = 4_000
    #: ★계단식 선택 사이트용. 여기 링크를 하나씩 눌러 상품을 바꿔 가며 표를 읽는다.
    #: (NH농협손보는 `상품군 → 상품구분 → 보험상품` 을 다 골라야 표가 채워진다.)
    product_link_selector: str | None = None
    #: ★브라우저 클릭으로 PDF 를 못 잡을 때, 링크의 인자를 읽어 **세션으로 직접 받는다.**
    #: 목록 탐색은 브라우저가 하고 파일만 직접 받는 하이브리드다.
    direct_download_url: str | None = None
    #: '다음 페이지' 버튼. 없으면 1페이지만 받는다.
    next_page_selector: str | None = None
    #: 페이지 **번호** 버튼들. 있으면 번호를 하나씩 눌러 넘기고,
    #: 번호가 안 보일 때만 `next_page_selector` 로 **묶음**을 넘긴다.
    #:
    #: ★왜 필요한가 — 삼성생명의 `btn-paging-next` 는 '다음 페이지'가 아니라
    #:   **다음 10페이지 묶음**이었다. 그것만 눌러서 1·11·21 페이지만 긁고
    #:   23페이지 중 3페이지에서 끝났다.
    #:   실측 근거: 첫 셀의 일련번호가 **223 → 123 → 23** 으로 100 씩 뛰었다.
    #:   DOM: `.pagination-number > ul > li > button` 이 1~10,
    #:        그 옆의 `btn-paging-next` 가 11~20 묶음으로 넘어간다.
    page_number_selector: str | None = None
    #: 현재 페이지 표시. 페이지가 **실제로 바뀌었는지**를 이걸로 확인한다.
    #: ★첫 행 텍스트 비교는 전환 중 옛 DOM 을 읽어 오탐이 난다(코덱스 지적).
    current_page_selector: str | None = None
    #: 페이지 상한. 무한 루프를 막는 안전장치이지 '여기까지만 받자'가 아니다.
    max_pages: int = 200


SITES: dict[str, SiteConfig] = {
    "samsunglife": SiteConfig(
        insurer="삼성생명",
        slug="samsunglife",
        host="www.samsunglife.com",
        entry_url="https://www.samsunglife.com/individual/products/disclosure/sales/PDO-PRPRI010110M",
        # ★페이지에 표가 3개 있다(전체/판매/판매중지). 첫 번째가 목록이다.
        #   `table tbody tr` 로 잡으면 다른 표의 1행짜리 껍데기를 문다(실측: 3행만 나왔다).
        table_selector="table",   # 첫 번째 표가 목록이다
        row_selector="tbody tr",
        # ★`btn-paging-next` 는 **10페이지 묶음** 이동이다. 번호 버튼이 먼저다.
        page_number_selector=".pagination-number ul li button",
        current_page_selector=".pagination-number li.current button",
        next_page_selector="button.btn-paging-next",
        # 약관 링크는 문구가 없고 **title 속성**에 '보험약관'이 있다.
        #   `a:has-text('약관')` 은 0건이었다 — 링크 텍스트가 상품명이기 때문이다.
        terms_link_selector="a.btn-file[title*='보험약관']",
        name_cell_selector="td:nth-child(3)",
        period_cell_selector="td:nth-child(4)",   # "2018-11-22 ~ 2018-11-22"
        # ★상품명 검색을 쓴다 — 전체 6,561건 중 **실손은 223건**이다.
        #   처음에 "검색이 안 된다"고 판단해 전체를 훑었는데, 그건 내가
        #   `input[type='text']` 로 잡아 **숨겨진 첫 번째 입력**(통합검색창)을 물었기 때문이다.
        #   실제 상품명 입력은 `#keywordSearch` 다.
        #   전수 조사는 남의 서버에 **30배 부하**를 주는 일이었다.
        #
        #   ★검색 실행은 **Enter** 로 한다. 돋보기 버튼(`button.icon.search`)은
        #     같은 셀렉터에 2개가 걸려 strict mode 위반이고, `.first` 로 좁혀도
        #     클릭이 타임아웃한다(실측). Enter 는 바로 먹는다.
        search_input_selector="#keywordSearch",
        search_terms=("실손",),
        settle_ms=9_000,
    ),
    "nhfire": SiteConfig(
        insurer="NH농협손해보험",
        slug="nhfire",
        host="www.nhfire.co.kr",
        entry_url="https://www.nhfire.co.kr/announce/productAnnounce/retrieveInsuranceProductsAnnounce.nhfire",
        # ★이 화면은 `상품군 → 상품구분 → 보험상품` 3단계를 다 고른 뒤에야 표가 채워진다.
        #   실측으로 확인한 연쇄:
        #     1 상품군   fnRetrievePdtDcd('01')          = 장기보험
        #     2 상품구분  fnRetrievePdtCd('Y','01','08')  = 단독실손의료보험
        #     3 보험상품  fnRetrievePdtInfo("<코드>")      = 개별 상품 → 표가 채워진다
        #
        #   막다른 길로 간 시도들(같은 실수를 반복하지 않기 위해 남긴다):
        #     - 폼을 직접 POST  -> 같은 기본 페이지만 돌아왔다(표가 AJAX 로 채워진다)
        #     - `fnRetrieveProductInfo('D71071B')` -> **상품 소개 페이지**로 가는 다른 함수였다.
        #                                            "잘못된 접근입니다"가 나왔다
        #     - Playwright `text=장기보험` 클릭 -> 접힌 메뉴 안이라 가시성 대기 중 타임아웃
        #     - 함수 직접 호출 -> 그 함수가 클릭 이벤트를 참조해 TypeError
        #   -> 남은 방법은 **요소를 JS 로 클릭**하는 것이다.
        #      CSS 안에 따옴표가 중첩돼 `querySelector` 가 깨지므로 onclick 문자열로 **필터**한다.
        pre_eval_js=(
            "[...document.querySelectorAll('a[onclick]')]"
            ".find(a => a.getAttribute('onclick').includes(\"fnRetrievePdtDcd('01')\")).click()",
            "[...document.querySelectorAll('a[onclick]')]"
            ".find(a => a.getAttribute('onclick').includes(\"'01', '08'\")).click()",
        ),
        product_link_selector="a[onclick^='fnRetrievePdtInfo']",
        # 상품 목록 표는 '판매개시일 … 약관 …' 헤더를 가진 표다.
        table_selector="table:has-text('판매개시일')",
        row_selector="tbody tr",
        # ★약관 링크는 `fnFileDownload("<fileId>","<seq>")` 이고, 그 함수는
        #   `POST /imageView/downloadFile.ajax` 로 폼을 보낸다.
        #   브라우저 클릭으로는 PDF 를 못 잡았다(응답·다운로드 이벤트 모두 비었다).
        #   그래서 **링크에서 인자만 읽어 세션으로 직접 받는다** — 하이브리드.
        #   seq 는 표의 열 순서와 같다: 1=약관, 2=상품요약서, 4=사업방법서(실측).
        terms_link_selector="table a[onclick^='fnFileDownload']",
        direct_download_url="https://www.nhfire.co.kr/imageView/downloadFile.ajax",
        name_cell_selector="td:nth-child(1)",
        settle_ms=6_000,
    ),
    "meritzfire": SiteConfig(
        insurer="메리츠화재",
        slug="meritzfire",
        host="www.meritzfire.com",
        entry_url="https://www.meritzfire.com/disclosure/product-announcement/product-list.do",
        # ★AngularJS SPA. 데이터는 스코프 `salPdLst` 에 있고 **DOM 에 토큰이 없다.**
        #   각 항목: ttlNm(상품명) putupStDdTm/EdDdTm(판매기간)
        #            file1(약관 경로) file1#[E](다운로드 토큰)
        #
        #   ★슬롯은 추측하지 않았다 — 화면의 `pdfDown(item, fileCnt, ttlNm)` 함수가
        #     직접 알려준다: file1→약관 file2→사업방법서 file3→요약서 file4→상품설명서
        #
        #   막다른 길(기록):
        #     - POST /hp/fileDownload.do 를 fetch 로 호출 -> {"resultMsg":""} 만 온다
        #     - file1 정적 경로 직접 요청 -> SPA 의 HTML(38KB)이 돌아온다
        #   -> 브라우저에서 **실제 클릭**해야 받아진다.
        #   ★기본 화면은 **자동차보험 분류형**이라 실손이 없다(401건 전부 자동차).
        #     `goSch()`(검색형)로 바꾸고 상품명으로 검색해야 실손이 나온다 -> 157건.
        #     검색 입력창은 name 이 아니라 **id** 가 `i_keyword` 다(name 으로 찾으면 null).
        pre_eval_js=(
            "[...document.querySelectorAll('a,button')]"
            ".find(e => e.getAttribute('data-ng-click') === 'goSch()').click()",
            "(() => { const i = document.querySelector('#i_keyword');"
            " const sc = angular.element(i).scope();"
            " if (sc) { sc.searchKeyword = '실손'; try { sc.$apply() } catch (e) {} }"
            " const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;"
            " set.call(i, '실손'); i.dispatchEvent(new Event('input', {bubbles: true}));"
            " [...document.querySelectorAll('a,button')]"
            ".find(e => e.getAttribute('data-ng-click') === 'titleSearch()').click(); })()",
        ),
        table_selector="table:has-text('판매개시일')",
        row_selector="tbody tr",
        # 링크 텍스트가 '해당 상품 약관 PDF파일 다운로드' 다. 첫 번째 파일 열이 약관이다.
        terms_link_selector="a:has-text('약관 PDF파일')",
        name_cell_selector="td:nth-child(1)",
        settle_ms=8_000,
    ),
}


@dataclass
class Collected:
    insurer: str
    url: str
    http_status: int
    content_type: str
    bytes: int
    sha256: str
    fetched_at: str
    saved_as: str
    product_code: str = ""
    product_name: str = ""
    sale_start: str = ""
    sale_end: str = ""
    filename_kind_hint: str = "policy_terms"
    identification: str = "unidentified"
    #: 브라우저로 받았다는 사실을 남긴다 — 나중에 방식별 성공률을 볼 수 있다.
    collector: str = "browser"


def _robots_allows(host: str, url: str) -> tuple[bool, str]:
    req = urllib.request.Request(f"https://{host}/robots.txt", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
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


def _fetched_urls() -> set[str]:
    if not _MANIFESTS.exists():
        return set()
    out: set[str] = set()
    for p in _MANIFESTS.glob("*.jsonl"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.add(json.loads(line)["url"])
    return out


def _recorded_shas(cfg: SiteConfig) -> set[str]:
    path = _MANIFESTS / f"{cfg.slug}.jsonl"
    if not path.exists():
        return set()
    return {
        json.loads(line).get("sha256", "")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _append_manifest(cfg: SiteConfig, rec) -> None:
    """★한 건 받을 때마다 즉시 기록한다.

    배치 끝에 몰아 쓰면 중간에 죽었을 때 **파일은 남고 기록만 사라진다**
    (실측: 삼성생명 파일 563 / 기록 44).
    """
    if rec.sha256 in _recorded_shas(cfg):
        return
    _MANIFESTS.mkdir(parents=True, exist_ok=True)
    with (_MANIFESTS / f"{cfg.slug}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")


def _save(cfg: SiteConfig, blob: bytes, url: str, name: str, ctype: str) -> Collected:
    if not blob.startswith(b"%PDF"):
        raise InfraError(f"PDF가 아닙니다(앞 8바이트={blob[:8]!r}): {name}")
    if len(blob) > MAX_BYTES:
        raise ValidationErr(f"상한 초과: {name}")
    digest = hashlib.sha256(blob).hexdigest()
    safe = "".join(c for c in name if c not in '\\/:*?"<>|')[:70] or "unnamed"
    out_dir = _RAW / cfg.slug
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = out_dir / f"{digest[:12]}_{safe}.pdf"
    saved.write_bytes(blob)
    return Collected(
        insurer=cfg.insurer,
        url=url,
        http_status=200,
        content_type=ctype or "application/pdf",
        bytes=len(blob),
        sha256=digest,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        saved_as=str(saved.relative_to(_ROOT)).replace("\\", "/"),
        product_name=name,
    )


def _direct_post(cfg: SiteConfig, page, file_id: str, seq: str) -> bytes:
    """브라우저 세션(쿠키)을 그대로 써서 파일만 직접 받는다."""
    resp = page.request.post(
        cfg.direct_download_url,
        form={"fileId": file_id, "afileSeqn": seq},
        headers={"Referer": cfg.entry_url},
        timeout=60_000,
    )
    if resp.status != 200:
        raise InfraError(f"다운로드 실패 HTTP {resp.status}")
    return resp.body()


def _current_page(cfg, page) -> str:
    """화면이 말하는 현재 페이지 번호. 못 읽으면 빈 문자열."""
    if not cfg.current_page_selector:
        return ""
    try:
        el = page.locator(cfg.current_page_selector).first
        if el.count() == 0:
            return ""
        #: 버튼 안에 `<span class="offscreen">현재 페이지</span>` 가 섞여 있다.
        return el.inner_text(timeout=3_000).split()[0].strip()
    except (PWTimeout, PWError, IndexError):
        return ""


def _goto_next_page(cfg, page, want: int) -> bool:
    """`want` 번 페이지로 넘긴다. 넘어갔으면 True.

    ★두 단계다 — 번호 버튼이 먼저, 없으면 묶음 이동.

        삼성생명은 번호 버튼이 한 번에 10개만 보인다(1~10). 11페이지로 가려면
        `btn-paging-next` 로 **묶음**을 넘긴 뒤에야 11 번호가 나타난다.
        묶음 버튼만 눌렀더니 1·11·21 페이지만 긁혔다(실측: 일련번호 223→123→23).

    ★넘어갔는지는 **페이지 번호**로 본다. 첫 행 텍스트 비교는 전환 중
      옛 DOM 을 읽어 "안 넘어갔다"고 오탐한다(코덱스 교차검증 지적).
    """
    before = _current_page(cfg, page)

    def _click_number() -> bool:
        if not cfg.page_number_selector:
            return False
        btns = page.locator(cfg.page_number_selector)
        for i in range(btns.count()):
            b = btns.nth(i)
            try:
                if b.inner_text(timeout=2_000).split()[0].strip() != str(want):
                    continue
                b.click(timeout=8_000)
                return True
            except (PWTimeout, PWError, IndexError):
                continue
        return False

    moved = _click_number()
    if not moved and cfg.next_page_selector:
        #: 번호가 안 보인다 = 다음 묶음이다. 묶음을 넘기고 번호를 다시 찾는다.
        nxt = page.locator(cfg.next_page_selector).first
        try:
            if nxt.count() == 0 or not nxt.is_enabled():
                return False
            nxt.click(timeout=8_000)
            page.wait_for_timeout(cfg.settle_ms)
        except (PWTimeout, PWError):
            return False
        #: 묶음 이동만으로 이미 `want` 로 갔을 수 있다. 그러면 번호 클릭은 불필요.
        if _current_page(cfg, page) == str(want):
            return True
        moved = _click_number()
        if not moved:
            #: 번호 방식이 아닌 사이트는 묶음 이동 자체가 '다음 페이지'다.
            return not cfg.page_number_selector

    if not moved:
        return False

    page.wait_for_timeout(cfg.settle_ms)
    after = _current_page(cfg, page)
    if before and after and before == after:
        print(f"  [중단] 페이지가 {before} 에서 안 넘어갔다.")
        return False
    return True


def _run_cascade(cfg, page, pdf_hits, done, out, *, limit: int, probe: bool):
    """상품을 하나씩 골라야 표가 채워지는 사이트를 순회한다.

    ★상품 링크를 **매번 다시 찾는다.** 상품을 클릭하면 화면이 다시 그려져
      앞서 잡아둔 요소 핸들이 죽는다(stale). 인덱스로 다시 찾아야 한다.
    """
    n = page.locator(cfg.product_link_selector).count()
    if n == 0:
        raise InfraError(
            f"상품 링크가 0건입니다: {cfg.product_link_selector!r}. "
            "계단식 선택(pre_eval_js)이 제대로 수행되지 않았을 수 있습니다."
        )
    print(f"상품 {n}개 발견")

    if probe:
        for i in range(min(n, 5)):
            print(f"  [{i + 1}] {page.locator(cfg.product_link_selector).nth(i).inner_text()[:44]!r}")
        return []

    for i in range(n):
        if limit and len(out) >= limit:
            break
        link = page.locator(cfg.product_link_selector).nth(i)
        try:
            nm = link.inner_text(timeout=3_000).strip()
            link.click(timeout=10_000)
            page.wait_for_timeout(cfg.settle_ms)
        except (PWTimeout, PWError) as e:
            print(f"  [SKIP] 상품{i + 1}: {type(e).__name__}")
            continue

        terms = page.locator(cfg.terms_link_selector)
        if terms.count() == 0:
            print(f"  [없음] {nm[:34]}: 약관 링크 없음")
            continue

        if cfg.direct_download_url:
            # ★첫 번째 링크가 '약관' 열이다(표 열 순서 = seq 순서).
            oc = terms.first.get_attribute("onclick") or ""
            m = re.search(r'fnFileDownload\(\s*"([^"]+)"\s*,\s*"([^"]+)"', oc)
            if not m:
                print(f"  [형식불일치] {nm[:34]}: onclick={oc[:50]!r}")
                continue
            file_id, seq = m.group(1), m.group(2)
            url = f"{cfg.direct_download_url}?fileId={file_id}&afileSeqn={seq}"
            if url in done:
                continue
            try:
                blob = _direct_post(cfg, page, file_id, seq)
                rec = _save(cfg, blob, url, nm, "application/pdf")
                out.append(rec)
                done.add(url)
                print(f"  [OK] {nm[:34]} {rec.bytes:,}B")
            except (InfraError, ValidationErr) as e:
                print(f"  [FAIL] {nm[:34]}: {e}")
            page.wait_for_timeout(ROW_DELAY_MS)
            continue

        before = len(pdf_hits)
        try:
            terms.first.click(timeout=10_000)
            page.wait_for_timeout(3_000)
        except (PWTimeout, PWError) as e:
            print(f"  [SKIP] {nm[:34]}: 약관 클릭 실패 {type(e).__name__}")
            continue
        got = pdf_hits[before:]
        if not got:
            # ★조용히 넘어가지 않는다. '약관이 없다'와 'PDF 를 못 잡았다'는 다르다.
            print(f"  [미포착] {nm[:34]}: 약관을 눌렀으나 PDF 응답이 없다(다운로드/새창 가능성)")
        for url, blob, ct in got:
            if url in done:
                continue
            try:
                rec = _save(cfg, blob, url, nm, ct)
                out.append(rec)
                done.add(url)
                print(f"  [OK] {nm[:34]} {rec.bytes:,}B")
            except (InfraError, ValidationErr) as e:
                print(f"  [FAIL] {nm[:34]}: {e}")
        page.wait_for_timeout(ROW_DELAY_MS)
    return out


def run(cfg: SiteConfig, *, limit: int, probe: bool) -> list[Collected]:
    allowed, verdict = _robots_allows(cfg.host, cfg.entry_url)
    if not allowed:
        raise InfraError(f"robots가 허용하지 않습니다: {cfg.host} ({verdict})")
    print(f"robots: {verdict}")

    done = _fetched_urls()
    out: list[Collected] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1366, "height": 950},
                                  locale="ko-KR", accept_downloads=True)
        ctx.set_default_navigation_timeout(NAV_TIMEOUT_MS)
        ctx.route("**/*", lambda r: r.abort() if r.request.resource_type in _BLOCK else r.continue_())

        #: ★PDF 는 클릭 결과로 **네트워크 응답**에 실려 온다. DOM 링크만 보면 놓친다.
        pdf_hits: list[tuple[str, bytes, str]] = []

        def _on_response(resp):
            try:
                ct = resp.headers.get("content-type", "")
                if "pdf" not in ct.lower():
                    return
                #: ★206(Range 부분 응답)을 버린다. **이게 65,536B 파일의 정체였다.**
                #: Chromium PDF 뷰어가 `Range: bytes=0-65535` 로 앞 64KiB 를 먼저 받는다.
                #:   206  content-range=bytes 0-65535/1028052  len=65536    <- 버린다
                #:   200  (같은 파일 전체)                      len=1028052  <- 받는다
                #: 나는 이걸 "스트리밍 중에 읽어서 잘렸다"고 오진했고, 그 오진 때문에
                #: `resp.finished()` 를 넣어 `Target closed` 를, 팝업을 안 닫아 `Page crashed` 를
                #: 만들었다. **status 만 보면 되는 문제**였다(코덱스 교차검증으로 밝혀짐).
                if resp.status == 206:
                    return
                pdf_hits.append((resp.url, resp.body(), ct))
            except Exception:  # noqa: BLE001
                pass

        ctx.on("response", _on_response)

        def _on_download(dl):
            """★PDF 가 응답이 아니라 **다운로드 이벤트**로 올 수 있다."""
            try:
                path = dl.path()
                if path:
                    blob = Path(path).read_bytes()
                    pdf_hits.append((dl.url, blob, "application/pdf"))
            except Exception:  # noqa: BLE001
                pass

        ctx.on("download", _on_download)

        def _on_page(pg):
            """★약관 클릭이 **새 창**을 여는 사이트가 있다(삼성생명).

            새 창을 방치하면 창이 쌓이다 브라우저가 죽는다
            (실측: `TargetClosedError: Target page, context or browser has been closed`).
            응답은 컨텍스트 수준에서 이미 잡히므로 창은 닫아도 된다.
            """
            try:
                pg.wait_for_timeout(2_500)
                if pg != page:
                    pg.close()
            except Exception:  # noqa: BLE001
                pass

        ctx.on("page", _on_page)
        page = ctx.new_page()

        try:
            page.goto(cfg.entry_url, wait_until="domcontentloaded")
            page.wait_for_timeout(cfg.settle_ms)

            for js in cfg.pre_eval_js:
                try:
                    page.evaluate(js)
                except PWError as e:
                    # ★"Execution context was destroyed" 는 **페이지가 전이됐다는 뜻**이다.
                    #   이 사이트의 함수는 폼을 submit 하므로 정상 동작이다.
                    #   다른 오류는 그대로 실패시킨다 — 구분하지 않으면 실패를 성공으로 읽는다.
                    if "destroyed" not in str(e) and "navigation" not in str(e).lower():
                        raise InfraError(f"사전 스크립트 실패({js}): {e}") from e
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=25_000)
                except (PWTimeout, PWError):
                    pass
                page.wait_for_timeout(cfg.settle_ms)

            for sel in cfg.pre_click_selectors:
                try:
                    page.click(sel, timeout=8_000)
                    page.wait_for_timeout(cfg.settle_ms)
                except (PWTimeout, PWError) as e:
                    raise InfraError(f"사전 클릭 실패({sel}): {type(e).__name__}") from e

            if cfg.search_input_selector and cfg.search_terms:
                box = page.locator(cfg.search_input_selector).first
                box.fill(cfg.search_terms[0], timeout=10_000)
                if cfg.submit_selector:
                    page.click(cfg.submit_selector, timeout=10_000)
                else:
                    box.press("Enter")
                page.wait_for_timeout(cfg.settle_ms)

            # ★표를 먼저 고른 뒤 그 안에서 행을 찾는다.
            #   `table tbody tr` 로 한 번에 잡으면 다른 표의 껍데기 행을 문다(실측).
            table = page.locator(cfg.table_selector).first
            if table.count() == 0:
                raise InfraError(f"표 셀렉터가 0건입니다: {cfg.table_selector!r}")
            if cfg.product_link_selector:
                return _run_cascade(cfg, page, pdf_hits, done, out, limit=limit, probe=probe)

            page_no = 0
            seen_first_cell = ""
            while page_no < cfg.max_pages:
                page_no += 1
                table = page.locator(cfg.table_selector).first
                rows = table.locator(cfg.row_selector)
                # ★행이 늦게 그려진다. 6초에서는 1행만 잡혔고 9초에서 10행이 나왔다(실측).
                try:
                    rows.nth(1).wait_for(timeout=15_000)
                except (PWTimeout, PWError):
                    pass
                n = rows.count()
                if n == 0:
                    if page_no == 1:
                        # ★'결과 없음'과 '셀렉터가 안 맞음'을 구분한다.
                        raise InfraError(
                            f"행 셀렉터가 0건입니다: {cfg.row_selector!r}. "
                            "화면 구조가 바뀌었거나 셀렉터가 틀렸습니다."
                        )
                    break

                # ★페이지가 실제로 넘어갔는지 확인한다. '다음' 버튼이 먹지 않아도
                #   같은 페이지를 계속 긁으면 조용히 중복만 쌓인다.
                try:
                    first = rows.nth(0).inner_text(timeout=3_000)[:60]
                except (PWTimeout, PWError):
                    first = ""
                #: ★페이지 번호를 읽을 수 있으면 그쪽이 정확하다. 첫 행 비교는
                #:   전환 중 옛 DOM 을 읽어 오탐한다 — 번호가 있을 땐 쓰지 않는다.
                if page_no > 1 and not cfg.current_page_selector and first == seen_first_cell:
                    print(f"  [중단] p{page_no}: 페이지가 넘어가지 않았다(첫 행이 동일).")
                    break
                seen_first_cell = first

                print(f"  p{page_no}: 행 {n}개")
                if probe:
                    for i in range(min(n, 5)):
                        row = rows.nth(i)
                        try:
                            nm = row.locator(cfg.name_cell_selector).first.inner_text(timeout=3_000)
                        except (PWTimeout, PWError):
                            nm = "(상품명 셀 못 읽음)"
                        links = row.locator(cfg.terms_link_selector).count()
                        print(f"    [{i + 1}] {nm.strip()[:40]!r} / 약관링크 {links}개")
                    return []

                for i in range(n):
                    if limit and len(out) >= limit:
                        return out
                    row = rows.nth(i)
                    try:
                        nm = row.locator(cfg.name_cell_selector).first.inner_text(timeout=3_000).strip()
                    except (PWTimeout, PWError):
                        nm = f"p{page_no}r{i + 1}"
                    link = row.locator(cfg.terms_link_selector).first
                    if link.count() == 0:
                        continue
                    before = len(pdf_hits)
                    try:
                        link.click(timeout=10_000)
                        page.wait_for_timeout(2_500)
                    except (PWTimeout, PWError) as e:
                        print(f"    [SKIP] {nm[:28]}: 클릭 실패 {type(e).__name__}")
                        continue
                    for url, blob, ct in pdf_hits[before:]:
                        if url in done:
                            continue
                        try:
                            rec = _save(cfg, blob, url, nm, ct)
                            out.append(rec)
                            done.add(url)
                            print(f"    [OK] {nm[:28]} {rec.bytes:,}B")
                        except (InfraError, ValidationErr) as e:
                            print(f"    [FAIL] {nm[:28]}: {e}")
                    page.wait_for_timeout(ROW_DELAY_MS)

                if not _goto_next_page(cfg, page, page_no + 1):
                    break
        finally:
            ctx.close()
            browser.close()

    if out:
        _MANIFESTS.mkdir(parents=True, exist_ok=True)
        path = _MANIFESTS / f"{cfg.slug}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True, help=f"대상: {', '.join(SITES)}")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--probe", action="store_true", help="받지 않고 셀렉터가 맞는지만 확인")
    args = ap.parse_args()

    cfg = SITES.get(args.site)
    if not cfg:
        raise ConfigError(f"등록되지 않은 사이트입니다: {args.site} (가능: {', '.join(SITES)})")

    got = run(cfg, limit=args.limit, probe=args.probe)
    print(f"\n수집 {len(got)}건")
    if got:
        print(f"→ data/raw/insurance_terms/{cfg.slug}/  ·  기록: data/raw/manifests/{cfg.slug}.jsonl")
    print("※ 받은 문서가 '무엇인지'는 판정하지 않았다(identification=unidentified).")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
