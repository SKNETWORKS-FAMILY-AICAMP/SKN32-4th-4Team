"""금융감독원 분쟁조정례·판례 게시판 4종 수집기.

이 도구가 **하는 일**: 아래 4개 게시판의 목록을 페이지 단위로 훑어 메타데이터를
`data/legal/fss/index.json` 에 쌓고, 실손의료보험 관련으로 판정된 항목의 상세 페이지
원문을 `data/legal/raw/fss/<id>.html` 에 저장한다.

  dcsn  분쟁조정결정례  /fss/bbs/B0000390/list.do?menuNo=201193
  case  분쟁조정사례    /fss/job/fncCnflCase/list.do?menuNo=201195
  mprc  주요판례        /fss/job/fncCnflMainPrcdnt/list.do?menuNo=201196
  fvst  금융감독판례    /fss/job/fvsttPrcdnt/list.do?menuNo=200179

2026-08-11 추가: 저장해 둔 상세 페이지에서 **첨부파일(hwp·pdf) 링크를 찾아 내려받는다**
(`attach` 하위명령). `dcsn`(분쟁조정결정례)은 본문이 「첨부파일 참조」 한 줄뿐인 건이 많아
상세 HTML 만으로는 내용을 쓸 수 없기 때문이다.

이 도구가 **하지 않는 일**:
- 실손 여부를 단정하지 않는다. 키워드 매칭 결과와 **매칭된 키워드를 함께** 남기며,
  판정 근거 없이 분류하지 않는다.
- 첨부를 받았다고 **읽었다고 하지 않는다.** 텍스트 추출은 별도 단계(`extract`)이고
  실패하면 "받았지만 파싱 못 함"으로 기록한다.

설계 원칙:
- **동시 연결 1개, 요청 간격 4~6초 랜덤.** 병렬 요청 금지.
- HTTP 429/403/5xx 는 **즉시 중단**한다. 재시도로 밀어붙이지 않는다(무폴백).
- 파싱 실패·네트워크 실패는 **세어서 기록**한다. 조용한 skip 금지.
- 원문은 `data/legal/raw/` 아래에만 둔다(.gitignore 로 차단됨). 저작물이다.
- 첨부는 **단일 30MB 상한**을 둔다. 무제한 수신 금지.

실행:
    python -m scripts.crawl.fss_dispute lists     # 목록만 수집 → index.json
    python -m scripts.crawl.fss_dispute bodies    # index.json 의 실손 항목 본문 수집
    python -m scripts.crawl.fss_dispute attach    # 저장된 상세 HTML 의 첨부 내려받기
    python -m scripts.crawl.fss_dispute extract   # 받은 첨부에서 텍스트 추출(네트워크 안 씀)
"""

from __future__ import annotations

import hashlib
import http.client
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

_ROOT = Path(__file__).resolve().parents[2]
_INDEX_PATH = _ROOT / "data" / "legal" / "fss" / "index.json"
_RAW_DIR = _ROOT / "data" / "legal" / "raw" / "fss"
#: 첨부 원본과 추출 텍스트. `data/legal/raw/` 는 .gitignore:201 로 통째로 차단돼 있다.
_ATTACH_DIR = _RAW_DIR / "attach"
_ATTACH_MANIFEST = _ATTACH_DIR / "_manifest.json"

BASE = "https://www.fss.or.kr"

#: 식별 가능한 UA(위장 금지). ★**저장소 표준과 다르다** —
#: `check_robots.py:30` · `fetch_pilot.py:37` 은 `BarobomResearchBot/0.1 (+contact: ...)` 인데
#: 여기만 `Bot` 세 글자를 뺀 `BarobomResearch/0.1` 이다. 왜 다른지 아래에 남긴다.
#: **값을 표준으로 되돌리기 전에 이 주석을 끝까지 읽어라.**
#:
#: ── (1) 실측: 금감원 WAF 는 UA 에 `bot` 이 들어가면 TCP 연결을 끊는다 (2026-08-11)
#: `www.fss.or.kr` 은 UA 문자열에 `bot`(대소문자 무관)이 있으면 **HTTP 응답조차 주지 않고**
#: 연결을 끊는다(`http.client.RemoteDisconnected`). `robots.txt` 한 파일에 대고 UA 만 바꿔
#: 5초 간격 9회 대조한 결과다 —
#:   `BarobomResearchBot/0.1 (+contact: ...)`  → RemoteDisconnected
#:   `BarobomResearchbot/0.1` (소문자 b)        → RemoteDisconnected
#:   `SomeBot/0.1` (우리와 무관한 이름)          → RemoteDisconnected
#:   `BarobomResearch/0.1 (+contact: ...)`      → HTTP 200
#:   `Python-urllib/3.x` (기본값)               → HTTP 200
#: 즉 **특정 봇을 지목한 차단이 아니라 `bot` 부분문자열 규칙**이다.
#: ★모르는 것: WAF 제품·정확한 규칙 표현식은 확인하지 못했다. 위는 관측된 입출력에서 세운
#: 추론이지 설정을 본 것이 아니다. `bot` 말고 어떤 토큰이 걸리는지도 전수 조사하지 않았다.
#:
#: ── (2) robots.txt 는 이 경로들을 막지 않는다 (2026-08-11 사용자 직접 확인)
#: `https://www.fss.or.kr/robots.txt` 는 네이버 `Yeti` 에 대해서만 4개 경로를 막고 있고,
#: 이 수집기가 쓰는 4개 게시판 경로는 **어떤 UA 에도 금지돼 있지 않다.**
#: 목록·상세 페이지에 수집 금지·복제 금지 문구도 없다(사용자 직접 확인).
#:
#: ── (3) ★이 값은 **사용자 결정**이다 (2026-08-11)
#: 앞 세션은 표준 UA 로 교정했다가 첫 요청에서 끊겨 **첨부 0건**으로 멈췄고,
#: `Bot` 을 빼는 선택지(리포트 §6.1 안 B)를 **모델이 임의로 고르지 않고 사람에게 넘겼다.**
#: 사용자가 위 (2)를 직접 확인한 뒤 이 UA 로 진행하도록 결정했다.
#: 근거는 "신원을 감추지 않는다"는 것이다 — 제품명·버전·연락처·목적을 그대로 밝히므로
#: **브라우저 사칭이 아니다.** 앞 세션이 쓰던 Chrome UA 위장과는 성격이 다르다.
#: ★그러나 이것이 WAF 의 의사 표시를 우회하는 성격을 갖는다는 반론이 사라진 것은 아니다.
#: 두 신호(robots 허용 / WAF 거절)가 어긋나 있고, 어느 쪽이 사이트의 진짜 의사인지는 모른다.
#:
#: ── (4) ★미확인 사항 — 「저작권 정책」 전문을 읽지 못했다
#: `https://www.fss.or.kr/fss/main/contents.do?menuNo=200701` 은 JS 렌더링이라
#: 본문을 확보하지 못했다. **공공누리 유형과 영리 이용 조건은 미확인이다.**
#: 그러므로 이 수집물의 재배포·영리 이용 가부는 아직 판단할 수 없다.
#: 받은 원문은 `data/legal/raw/` 밖으로 내보내지 않는다(.gitignore:201).
#:
#: ── 연락처를 배포 전에 반드시 실제 주소로 바꿔라(`set-before-deploy`).
USER_AGENT = "BarobomResearch/0.1 (+contact: set-before-deploy; purpose: insurance-terms-research)"
TIMEOUT_SEC = 30
#: 요청 간격. 2026-08-11 첨부 회수 지시로 하한을 3.5 → 4.0 으로 올렸다(동시 연결 1개 유지).
DELAY_MIN_SEC = 4.0
DELAY_MAX_SEC = 6.0
#: 첨부 단일 파일 상한. 무제한 수신 금지(fetch_pilot.py:42 와 같은 원칙, 값만 다르다).
MAX_ATTACH_BYTES = 30 * 1024 * 1024

#: 즉시 중단해야 하는 응답 코드(차단 신호). 재시도하지 않는다.
STOP_STATUSES = (403, 429)


class Blocked(RuntimeError):
    """차단 신호를 받았다. 재시도하지 않고 전체를 중단한다."""


def _is_block_signal(exc: BaseException) -> bool:
    """TCP 단계에서 끊긴 것도 **차단 신호**로 본다.

    ★2026-08-11 실측 — 이 사이트(WAF)는 UA 에 `bot` 이 들어가면 **HTTP 응답을 주지 않고
    연결을 끊는다**(`RemoteDisconnected`). 상태코드가 없으니 STOP_STATUSES 로는 안 잡힌다.
    이걸 "그냥 네트워크 오류"로 세고 다음 항목으로 넘어가면 **거절당한 문을 52번 두드리게 된다.**
    거절은 세어서 넘길 실패가 아니라 멈출 신호다.
    """
    seen = exc
    for _ in range(4):  # urllib 이 URLError 로 한 겹 싸므로 reason 을 따라 내려간다
        if isinstance(seen, (http.client.RemoteDisconnected, ConnectionResetError,
                             http.client.BadStatusLine)):
            return True
        seen = getattr(seen, "reason", None)
        if seen is None:
            return False
    return False


@dataclass(frozen=True)
class Board:
    key: str
    name: str
    list_path: str
    view_path: str
    id_param: str


BOARDS: tuple[Board, ...] = (
    Board("dcsn", "분쟁조정결정례",
          "/fss/bbs/B0000390/list.do?menuNo=201193",
          "/fss/bbs/B0000390/view.do", "nttId"),
    Board("case", "분쟁조정사례",
          "/fss/job/fncCnflCase/list.do?menuNo=201195",
          "/fss/job/fncCnflCase/view.do", "caseSlno"),
    Board("mprc", "주요판례",
          "/fss/job/fncCnflMainPrcdnt/list.do?menuNo=201196",
          "/fss/job/fncCnflMainPrcdnt/view.do", "prcdntSlno"),
    Board("fvst", "금융감독판례",
          "/fss/job/fvsttPrcdnt/list.do?menuNo=200179",
          "/fss/job/fvsttPrcdnt/view.do", "incdnSlno"),
)

#: 실손 판정 키워드. **강함**은 실손 제도를 직접 가리키는 말이고,
#: **약함**은 실손 분쟁에서 자주 나오지만 다른 담보에서도 쓰이는 말이다.
#: 두 층을 나누는 이유: 약한 키워드만으로 실손이라고 단정하면 틀린다.
KEYWORDS_STRONG = ("실손", "실비보험", "의료실비")
KEYWORDS_WEAK = (
    "본인부담상한", "비급여", "다초점", "인공수정체", "백내장", "도수치료",
    "맘모톰", "체외충격파", "요양병원", "통원의료비", "입원의료비", "의료비",
    "치료비", "하이푸", "무릎 주사", "관절강내", "영양제", "비밸브재건술",
)


#: 요청 **시작 시각**(monotonic) 기록. 「4~6초로 설정했다」가 아니라 「실제로 몇 초 벌어졌나」를
#: 리포트에 적기 위해서다. 설정값과 실측값은 다를 수 있다 — 다운로드 자체에 걸린 시간이 더해진다.
_REQUEST_STARTS: list[float] = []


def _note_request() -> None:
    _REQUEST_STARTS.append(time.monotonic())


def _interval_stats() -> dict:
    """연속한 요청 **시작 시각 사이의 간격**을 잰다. 요청이 2회 미만이면 빈 통계."""
    gaps = [round(b - a, 2) for a, b in zip(_REQUEST_STARTS, _REQUEST_STARTS[1:])]
    if not gaps:
        return {"requests": len(_REQUEST_STARTS), "gaps_measured": 0}
    return {
        "requests": len(_REQUEST_STARTS),
        "gaps_measured": len(gaps),
        "min_sec": min(gaps),
        "max_sec": max(gaps),
        "mean_sec": round(sum(gaps) / len(gaps), 2),
        "under_4s": sum(1 for g in gaps if g < 4.0),
        "configured_range_sec": [DELAY_MIN_SEC, DELAY_MAX_SEC],
    }


def _polite_sleep() -> None:
    time.sleep(random.uniform(DELAY_MIN_SEC, DELAY_MAX_SEC))


def _fetch(url: str, referer: str) -> str:
    """한 페이지를 받는다. 차단 신호면 Blocked 를 올려 전체를 멈춘다."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Referer": referer,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Connection": "close",
    })
    _note_request()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            if resp.status != 200:
                raise Blocked(f"HTTP {resp.status} at {url}")
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in STOP_STATUSES or exc.code >= 500:
            raise Blocked(f"HTTP {exc.code} at {url}") from exc
        raise
    except Exception as exc:  # noqa: BLE001
        if _is_block_signal(exc):
            raise Blocked(
                f"응답 없이 연결이 끊겼다({type(exc).__name__}) at {url} "
                f"— UA={USER_AGENT!r}") from exc
        raise


def _last_page(soup: BeautifulSoup) -> int | None:
    """페이지네이션의 '끝 목록' 링크에서 마지막 페이지 번호를 읽는다."""
    for a in soup.select("a"):
        href = a.get("href") or ""
        m = re.fullmatch(r"javascript:fnSearch\((\d+)\)", href.strip())
        if m and "끝" in a.get_text():
            return int(m.group(1))
    return None


def _total_count(soup: BeautifulSoup) -> int | None:
    m = re.search(r"전체\s*([0-9,]+)\s*건", soup.get_text(" ", strip=True))
    return int(m.group(1).replace(",", "")) if m else None


def _parse_rows(soup: BeautifulSoup, board: Board) -> tuple[list[dict], int]:
    """목록 표에서 행을 뽑는다. 반환: (행 목록, 파싱 실패 건수)."""
    table = soup.find("table")
    if table is None:
        raise ValueError("목록 표를 찾지 못했다")
    headers = [th.get_text(" ", strip=True) for th in table.select("thead th")]
    rows: list[dict] = []
    failed = 0
    for tr in table.select("tbody tr"):
        cells = tr.find_all(["td", "th"])
        link = None
        for a in tr.select("a"):
            href = a.get("href") or ""
            if "view.do" in href:
                link = href
                break
        if link is None:
            failed += 1
            continue
        m = re.search(rf"{board.id_param}=(\d+)", link)
        if m is None:
            failed += 1
            continue
        cols = {}
        for i, cell in enumerate(cells):
            name = headers[i] if i < len(headers) else f"col{i}"
            cols[name] = cell.get_text(" ", strip=True)
        rows.append({
            "board": board.key,
            "board_name": board.name,
            "site_id": m.group(1),
            "id": f"{board.key}_{m.group(1)}",
            "url": f"{BASE}{board.view_path}?{board.id_param}={m.group(1)}&menuNo="
                   f"{board.list_path.split('menuNo=')[1]}&pageIndex=1",
            "columns": cols,
        })
    return rows, failed


def _match_silson(item: dict) -> dict:
    """제목·유형·요지에서 실손 키워드를 찾는다. **매칭된 말을 그대로 남긴다.**"""
    cols = item["columns"]
    haystack = " ".join(
        str(cols.get(k, "")) for k in ("제목", "유형", "권역", "요지", "사건번호")
    )
    strong = [k for k in KEYWORDS_STRONG if k in haystack]
    weak = [k for k in KEYWORDS_WEAK if k in haystack]
    tier = "strong" if strong else ("weak" if weak else "none")
    return {"silson_tier": tier, "silson_hits": strong + weak}


def collect_lists() -> dict:
    started = datetime.now(timezone.utc).isoformat()
    result: dict = {
        "collected_at_utc": started,
        "source": "금융감독원 www.fss.or.kr",
        "note": "목록 메타데이터만. 상세 원문은 data/legal/raw/fss/ 에 있고 커밋하지 않는다.",
        "boards": {},
        "items": [],
    }
    for board in BOARDS:
        list_url = f"{BASE}{board.list_path}"
        _polite_sleep()
        html = _fetch(list_url, BASE + "/")
        soup = BeautifulSoup(html, "lxml")
        last = _last_page(soup)
        total = _total_count(soup)
        if last is None:
            raise ValueError(f"{board.name}: 마지막 페이지 번호를 읽지 못했다")
        rows, failed = _parse_rows(soup, board)
        page_failed = 0
        print(f"[{board.key}] {board.name} 총 {total}건 / {last}페이지", flush=True)
        print(f"  p1 {len(rows)}건 (행파싱실패 {failed})", flush=True)
        for page in range(2, last + 1):
            url = f"{list_url}&pageIndex={page}"
            _polite_sleep()
            try:
                page_html = _fetch(url, list_url)
            except Blocked:
                raise
            except Exception as exc:  # noqa: BLE001 — 세어서 기록한다
                page_failed += 1
                print(f"  p{page} 실패: {type(exc).__name__}: {exc}", flush=True)
                continue
            try:
                page_rows, page_row_failed = _parse_rows(
                    BeautifulSoup(page_html, "lxml"), board)
            except Exception as exc:  # noqa: BLE001
                page_failed += 1
                print(f"  p{page} 파싱실패: {exc}", flush=True)
                continue
            failed += page_row_failed
            rows.extend(page_rows)
            print(f"  p{page} {len(page_rows)}건 (누적 {len(rows)})", flush=True)
        for item in rows:
            item.update(_match_silson(item))
        result["boards"][board.key] = {
            "name": board.name,
            "list_url": list_url,
            "total_reported": total,
            "pages": last,
            "collected": len(rows),
            "row_parse_failed": failed,
            "page_fetch_failed": page_failed,
            "silson_strong": sum(1 for r in rows if r["silson_tier"] == "strong"),
            "silson_weak": sum(1 for r in rows if r["silson_tier"] == "weak"),
        }
        result["items"].extend(rows)
        print(f"[{board.key}] 완료: {len(rows)}건, 실손(강) "
              f"{result['boards'][board.key]['silson_strong']}건\n", flush=True)
    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    _INDEX_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {_INDEX_PATH}")
    return result


def collect_bodies(tiers: tuple[str, ...] = ("strong",)) -> dict:
    if not _INDEX_PATH.exists():
        raise FileNotFoundError(
            f"{_INDEX_PATH} 가 없다. 먼저 `python -m scripts.crawl.fss_dispute lists` 를 돌려라.")
    index = json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
    targets = [i for i in index["items"] if i["silson_tier"] in tiers]
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    saved, skipped, failed = 0, 0, []
    board_by_key = {b.key: b for b in BOARDS}
    print(f"대상 {len(targets)}건 (tier={tiers})", flush=True)
    for n, item in enumerate(targets, 1):
        out = _RAW_DIR / f"{item['id']}.html"
        if out.exists():
            skipped += 1
            continue
        referer = f"{BASE}{board_by_key[item['board']].list_path}"
        _polite_sleep()
        try:
            html = _fetch(item["url"], referer)
        except Blocked:
            raise
        except Exception as exc:  # noqa: BLE001 — 세어서 기록한다
            failed.append({"id": item["id"], "error": f"{type(exc).__name__}: {exc}"})
            print(f"  [{n}/{len(targets)}] {item['id']} 실패: {exc}", flush=True)
            continue
        out.write_text(html, encoding="utf-8")
        saved += 1
        print(f"  [{n}/{len(targets)}] {item['id']} 저장 ({len(html)}자)", flush=True)
    summary = {"targets": len(targets), "saved": saved,
               "skipped_existing": skipped, "failed": failed}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


# --------------------------------------------------------------------------
# 첨부파일 회수 (2026-08-11 추가)
# --------------------------------------------------------------------------

def _fetch_bytes(url: str, referer: str) -> tuple[bytes, str, str]:
    """첨부 하나를 받는다. 반환: (본문, Content-Type, 서버가 알려준 파일명).

    상한을 **읽는 단계에서** 건다. 다 받고 나서 길이를 재면 이미 다 받은 뒤다.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Referer": referer,
        "Accept": "*/*",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Connection": "close",
    })
    _note_request()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            if resp.status != 200:
                raise Blocked(f"HTTP {resp.status} at {url}")
            ctype = resp.headers.get("Content-Type", "")
            disp = resp.headers.get("Content-Disposition", "") or ""
            body = resp.read(MAX_ATTACH_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in STOP_STATUSES or exc.code >= 500:
            raise Blocked(f"HTTP {exc.code} at {url}") from exc
        raise
    except Exception as exc:  # noqa: BLE001
        if _is_block_signal(exc):
            raise Blocked(
                f"응답 없이 연결이 끊겼다({type(exc).__name__}) at {url} "
                f"— UA={USER_AGENT!r}") from exc
        raise
    if len(body) > MAX_ATTACH_BYTES:
        raise ValueError(f"첨부가 상한({MAX_ATTACH_BYTES}바이트)을 넘었다: {url}")
    if not body:
        raise ValueError(f"빈 응답: {url}")
    server_name = ""
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disp)
    if m:
        server_name = urllib.parse.unquote(m.group(1)).strip()
    return body, ctype, server_name


def _attachment_links(html: str) -> list[dict]:
    """상세 페이지 HTML 에서 첨부 링크를 뽑는다. 못 찾으면 빈 목록(= 첨부 없음)."""
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for a in soup.select("dl.file-list a"):
        href = a.get("href") or ""
        if "fileDown.do" not in href:
            continue  # 문서뷰어(docView) 링크는 파일이 아니다
        name_el = a.select_one("span.name")
        raw = (name_el.get_text(" ", strip=True) if name_el
               else a.get_text(" ", strip=True))
        # "이름.hwp (파일크기: 40KB)" / "이름.hwp [40KB]" 두 조판이 다 나온다.
        name = re.sub(r"\s*[\(\[]\s*(파일크기\s*:)?\s*[\d.,]+\s*[KMG]?B\s*[\)\]]\s*$",
                      "", raw).strip()
        out.append({
            "url": urllib.parse.urljoin(BASE, href.replace("&amp;", "&")),
            "name": name,
            "label_raw": raw,
        })
    return out


def _safe_stem(item_id: str, seq: int) -> str:
    """한글 파일명을 그대로 쓰지 않는다(RULE.md §3.3 — tar 왕복에서 379건이 깨진 적 있다).

    원래 이름은 매니페스트에 남기고, 디스크에는 ASCII 이름으로 평평하게 둔다.
    """
    return f"{item_id}_{seq}"


def collect_attachments() -> dict:
    """★저장된 상세 HTML 만 읽는다. 상세 페이지를 다시 받지 않는다(CLAUDE.md §2)."""
    pages = sorted(_RAW_DIR.glob("*.html"))
    if not pages:
        raise FileNotFoundError(
            f"{_RAW_DIR} 에 상세 HTML 이 없다. 먼저 `bodies` 를 돌려라.")
    _ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    board_by_key = {b.key: b for b in BOARDS}

    records: list[dict] = []
    failures: list[dict] = []
    no_attach: list[str] = []
    parse_failed: list[str] = []
    skipped = 0
    planned = 0

    # 1) 먼저 전량 파싱해 대상 수를 확정한다. 받아가며 세면 분모가 흔들린다.
    plan: list[tuple[str, int, dict]] = []
    for page in pages:
        item_id = page.stem
        try:
            links = _attachment_links(page.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — 세어서 기록한다
            parse_failed.append(f"{item_id}: {type(exc).__name__}: {exc}")
            continue
        if not links:
            no_attach.append(item_id)
            continue
        for seq, link in enumerate(links, 1):
            plan.append((item_id, seq, link))
            planned += 1
    print(f"상세 HTML {len(pages)}건 / 첨부 있는 항목 "
          f"{len(pages) - len(no_attach) - len(parse_failed)}건 / 첨부 링크 {planned}개",
          flush=True)

    # ★중간에 차단당해도 **여기까지 받은 것**은 매니페스트에 남긴다. 앞 판은 Blocked 를 그대로
    #   위로 던져서 다운로드 기록이 통째로 사라졌다(그때는 0건이라 표가 안 났을 뿐이다).
    blocked: Blocked | None = None
    attempted = 0
    for n, (item_id, seq, link) in enumerate(plan, 1):
        board_key = item_id.split("_", 1)[0]
        referer = f"{BASE}{board_by_key[board_key].view_path}" if board_key in board_by_key else BASE
        ext = Path(link["name"]).suffix.lower() or ".bin"
        out = _ATTACH_DIR / f"{_safe_stem(item_id, seq)}{ext}"
        if out.exists():
            skipped += 1
            print(f"  [{n}/{planned}] {out.name} 이미 있음", flush=True)
            continue
        _polite_sleep()
        attempted += 1
        try:
            body, ctype, server_name = _fetch_bytes(link["url"], referer)
        except Blocked as exc:
            # 재시도하지 않는다. 남은 항목도 시도하지 않는다(거절당한 문을 다시 두드리지 않는다).
            blocked = exc
            print(f"  [{n}/{planned}] {item_id}#{seq} ★차단 신호 — 중단: {exc}", flush=True)
            break
        except Exception as exc:  # noqa: BLE001 — 세어서 기록한다
            failures.append({"id": item_id, "seq": seq, "name": link["name"],
                             "url": link["url"],
                             "error": f"{type(exc).__name__}: {exc}"})
            print(f"  [{n}/{planned}] {item_id}#{seq} 실패: {exc}", flush=True)
            continue
        out.write_bytes(body)
        records.append({
            "id": item_id,
            "seq": seq,
            "original_name": link["name"],
            "server_filename": server_name,
            "url": link["url"],
            "saved_as": str(out.relative_to(_ROOT)).replace("\\", "/"),
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "content_type": ctype,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "text_status": "not_attempted",
        })
        print(f"  [{n}/{planned}] {out.name} {len(body):,}B  ← {link['name'][:50]}",
              flush=True)

    summary = {
        "collected_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_agent": USER_AGENT,
        "pages_scanned": len(pages),
        "items_with_attachment": len(pages) - len(no_attach) - len(parse_failed),
        "items_without_attachment": no_attach,
        "html_parse_failed": parse_failed,
        "attachment_links_found": planned,
        "downloaded": len(records),
        "skipped_existing": skipped,
        "attempted": attempted,
        "not_attempted": planned - skipped - attempted,
        "blocked": str(blocked) if blocked else None,
        "request_intervals": _interval_stats(),
        "failed": failures,
        "records": records,
    }
    if _ATTACH_MANIFEST.exists():
        # 이어받기 — 기존 기록을 잃지 않는다.
        old = json.loads(_ATTACH_MANIFEST.read_text(encoding="utf-8"))
        by_key = {(r["id"], r["seq"]): r for r in old.get("records", [])}
        by_key.update({(r["id"], r["seq"]): r for r in records})
        summary["records"] = sorted(by_key.values(), key=lambda r: (r["id"], r["seq"]))
    _ATTACH_MANIFEST.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n첨부 {len(records)}건 저장 / 기존 {skipped}건 / 실패 {len(failures)}건 "
          f"/ 미시도 {summary['not_attempted']}건")
    print(f"요청 간격 실측: {json.dumps(summary['request_intervals'], ensure_ascii=False)}")
    print(f"매니페스트: {_ATTACH_MANIFEST}")
    if blocked is not None:
        raise blocked  # 매니페스트를 남긴 뒤에 올린다 — 종료 코드로 차단을 알린다
    return summary


# --------------------------------------------------------------------------
# 첨부 텍스트 추출 (2026-08-11 추가) — **네트워크를 쓰지 않는다.**
# --------------------------------------------------------------------------

#: 파일 매직. 확장자를 믿지 않는다 — 게시판이 `.hwp` 로 올려 둔 것이 실제로는 다른 포맷일 수 있고,
#: 무엇보다 **HWP 3.0(비 OLE)과 HWP 5.x(OLE)는 완전히 다른 파일 형식**이다. pyhwp 는 5.x 전용이다.
_MAGIC_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"   # OLE 복합문서 → HWP 5.x
_MAGIC_HWP3 = b"HWP Document File V3.00"           # 구형 HWP 3.0 → pyhwp 미지원
_MAGIC_ZIP = b"PK\x03\x04"                          # HWPX(OWPML) 또는 잘못된 파일
_MAGIC_PDF = b"%PDF-"


def _sniff_format(head: bytes) -> str:
    if head.startswith(_MAGIC_OLE):
        return "hwp5-ole"
    if head.startswith(_MAGIC_HWP3):
        return "hwp3"
    if head.startswith(_MAGIC_ZIP):
        return "zip(hwpx?)"
    if head.startswith(_MAGIC_PDF):
        return "pdf"
    if head[:6].lower() == b"<html>" or b"<html" in head[:512].lower():
        return "html(!)"   # 다운로드가 실패해 오류 페이지를 받은 경우다
    return "unknown"


def _extract_pdf(path: Path) -> str:
    import fitz  # PyMuPDF
    with fitz.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


def _extract_hwp5(path: Path, out_txt: Path) -> str:
    """`hwp5txt` CLI 로 뽑는다. 파이썬 API 직접 호출보다 실패 지점이 명확하다."""
    import subprocess
    proc = subprocess.run(
        ["hwp5txt", "--output", str(out_txt), str(path)],
        capture_output=True, timeout=120,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(f"hwp5txt rc={proc.returncode}: {err[-1] if err else '(stderr 비어 있음)'}")
    return out_txt.read_text(encoding="utf-8", errors="replace")


def extract_attachments() -> dict:
    """받아 둔 첨부에서 텍스트를 뽑는다. **성공·실패를 건별로 센다.**

    ★실패를 조용히 넘기지 않는다(CLAUDE.md §3). 특히 `dcsn` 첨부는 2003~2020년 문서라
    구형 HWP 3.0 이 섞여 있을 수 있고, 그건 pyhwp 로 못 연다 — 못 연 것은 못 열었다고 적는다.
    """
    files = sorted(p for p in _ATTACH_DIR.glob("*")
                   if p.is_file() and p.suffix.lower() in (".hwp", ".pdf", ".bin"))
    if not files:
        raise FileNotFoundError(f"{_ATTACH_DIR} 에 첨부가 없다. 먼저 `attach` 를 돌려라.")
    text_dir = _ATTACH_DIR / "text"
    text_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for path in files:
        head = path.open("rb").read(1024)
        fmt = _sniff_format(head)
        out_txt = text_dir / f"{path.stem}.txt"
        rec = {"file": path.name, "bytes": path.stat().st_size,
               "format_sniffed": fmt, "status": "", "chars": 0, "error": ""}
        try:
            if fmt == "pdf":
                text = _extract_pdf(path)
                out_txt.write_text(text, encoding="utf-8")
            elif fmt == "hwp5-ole":
                text = _extract_hwp5(path, out_txt)
            elif fmt == "hwp3":
                raise NotImplementedError(
                    "구형 HWP 3.0(비 OLE) — pyhwp 는 HWP 5.x 전용이라 열 수 없다")
            else:
                raise NotImplementedError(f"지원하지 않는/알 수 없는 포맷: {fmt}")
            rec["chars"] = len(text.strip())
            #: 파일은 열렸는데 글자가 거의 없으면 **성공이라고 하지 않는다.**
            #: 스캔 이미지만 든 문서일 수 있다(OCR 미적용). 다음 사람이 속지 않게 따로 센다.
            rec["status"] = "ok" if rec["chars"] >= 200 else "empty_or_scanned"
        except Exception as exc:  # noqa: BLE001 — 세어서 기록한다
            rec["status"] = "failed"
            rec["error"] = f"{type(exc).__name__}: {exc}"
        results.append(rec)
        print(f"  {path.name:<22} {fmt:<12} {rec['status']:<18} "
              f"{rec['chars']:>7}자 {rec['error'][:70]}", flush=True)

    by_status: dict[str, int] = {}
    by_fmt: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_fmt[r["format_sniffed"]] = by_fmt.get(r["format_sniffed"], 0) + 1
    summary = {
        "extracted_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attempted": len(results),
        "by_status": by_status,
        "by_format": by_fmt,
        "results": results,
    }
    out = _ATTACH_DIR / "_extract_report.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n시도 {len(results)}건 → {by_status}")
    print(f"포맷 분포: {by_fmt}")
    print(f"리포트: {out}")

    # 매니페스트의 text_status 를 실제 결과로 갱신한다("not_attempted" 로 남겨두지 않는다).
    if _ATTACH_MANIFEST.exists():
        man = json.loads(_ATTACH_MANIFEST.read_text(encoding="utf-8"))
        status_by_stem = {Path(r["file"]).stem: r["status"] for r in results}
        for rec in man.get("records", []):
            stem = Path(rec.get("saved_as", "")).stem
            if stem in status_by_stem:
                rec["text_status"] = status_by_stem[stem]
        _ATTACH_MANIFEST.write_text(
            json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("lists", "bodies", "attach", "extract"):
        print(__doc__)
        return 2
    try:
        if sys.argv[1] == "lists":
            collect_lists()
        elif sys.argv[1] == "attach":
            collect_attachments()
        elif sys.argv[1] == "extract":
            extract_attachments()
        else:
            tiers = tuple(sys.argv[2].split(",")) if len(sys.argv) > 2 else ("strong",)
            collect_bodies(tiers)
    except Blocked as exc:
        print(f"★차단 신호로 중단: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
