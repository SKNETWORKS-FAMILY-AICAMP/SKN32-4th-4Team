"""16개 보험사·협회 공시의 robots.txt 자동수집 허용 여부 점검 (수집 전 필수 관문).

설계 원칙(Codex 논의 반영):
- **robots.txt 접속 실패 = 허용 아님.** RFC 9309에 따라 가져오지 못하면 **전면 금지**로 취급한다.
  "응답이 없으니 일단 긁자"는 전형적인 폴백이며, 이 저장소 원칙에 정면으로 어긋난다.
- robots는 **접근 허가가 아니다.** 통과해도 이용약관·저작권·재배포 조건을 별도로 확인해야 한다
  (이 스크립트는 그 확인을 대신하지 않는다 — 리포트에 항상 경고를 남긴다).
- 식별 가능한 User-Agent를 쓰고, 호스트당 1회만 요청하며, 지연을 둔다.

이 스크립트는 **조회만 한다.** 약관 PDF를 내려받지 않는다.

실행: python -m scripts.crawl.check_robots
결과: docs/reports/ 아래에 마크다운 표로 저장 + 콘솔 요약
"""

from __future__ import annotations

import hashlib
import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.robotparser import RobotFileParser

# 식별 가능한 UA(위장 금지 — Codex). 연락처는 배포 시 실제 값으로 교체한다.
USER_AGENT = "BarobomResearchBot/0.1 (+contact: set-before-deploy; purpose: insurance-terms-research)"
TIMEOUT = 15
DELAY_SEC = 2.0  # 호스트 간 예의 지연

_ROOT = Path(__file__).resolve().parents[2]
_OUT = _ROOT / "docs" / "reports" / f"{date.today().isoformat()}_robots_점검.md"
#: ★판정의 **근거 원문**을 보관한다. 판정만 남기면 "왜 허용인가"를 나중에 증명할 수 없다.
#: (Codex 원칙: 원본·해시·URL·수집시각·응답상태를 불변 저장)
_RAW_DIR = _ROOT / "docs" / "reports" / f"{date.today().isoformat()}_robots_raw"

#: 점검 대상. (구분, 회사명, 호스트, 확인해볼 대표 경로)
TARGETS: list[tuple[str, str, str, str]] = [
    # --- 손해보험 9 ---
    ("손보", "삼성화재", "www.samsungfire.com", "/"),
    ("손보", "메리츠화재", "www.meritzfire.com", "/"),
    ("손보", "DB손해보험", "www.idbins.com", "/"),
    ("손보", "현대해상", "www.hi.co.kr", "/"),
    ("손보", "KB손해보험", "www.kbinsure.co.kr", "/"),
    ("손보", "한화손해보험", "www.hwgeneralins.com", "/"),
    ("손보", "흥국화재", "www.heungkukfire.co.kr", "/"),
    ("손보", "롯데손해보험", "www.lotteins.co.kr", "/"),
    ("손보", "NH농협손해보험", "www.nhfire.co.kr", "/"),
    # --- 생명보험 7 ---
    ("생보", "삼성생명", "www.samsunglife.com", "/"),
    ("생보", "한화생명", "www.hanwhalife.com", "/"),
    ("생보", "교보생명", "www.kyobo.co.kr", "/"),
    ("생보", "흥국생명", "www.heungkuklife.co.kr", "/"),
    ("생보", "DB생명", "www.dblife.co.kr", "/"),
    ("생보", "동양생명", "www.myangel.co.kr", "/"),
    ("생보", "NH농협생명", "www.nhlife.co.kr", "/"),
    # --- 협회·공공 공시(1차 카탈로그 후보 — Codex 권고) ---
    ("협회", "손해보험협회 공시실", "kpub.knia.or.kr", "/"),
    ("협회", "생명보험협회 공시실", "pub.insure.or.kr", "/"),
    ("공공", "금융소비자정보포털 파인", "fine.fss.or.kr", "/"),
]

# 판정 값 — 셋뿐이며 '알 수 없음'을 허용으로 접지 않는다.
ALLOWED = "allowed"
DISALLOWED = "disallowed"
UNKNOWN_BLOCKED = "unknown_treated_as_blocked"


@dataclass
class Result:
    group: str
    name: str
    host: str
    verdict: str
    http_status: str = ""
    detail: str = ""
    crawl_delay: str | None = None
    sitemaps: list[str] = field(default_factory=list)
    has_rules: bool = False


#: 일시적 실패를 영구 판정으로 굳히지 않기 위한 재시도 횟수.
#: ★실측 근거: 1회 시도만 했을 때 현대해상이 '확인불가'로 나왔으나 재시도하니 HTTP 200이었다.
#: 네트워크 순간 오류를 "자동수집 금지"로 확정하면 잘못된 결론을 문서에 남기게 된다.
RETRIES = 3
BACKOFF_SEC = 2.0


def _fetch_robots(host: str) -> tuple[str | None, str]:
    """robots.txt 본문과 상태를 돌려준다. 예외를 삼키지 않고 사유를 그대로 반환.

    일시적 오류는 백오프하며 재시도한다. 재시도를 모두 소진해도 실패하면 그때 '확인불가'로
    판정한다(그리고 확인불가는 허용이 아니라 금지로 취급한다).
    """
    url = f"https://{host}/robots.txt"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last = "시도 없음"
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                suffix = "" if attempt == 1 else f" (재시도 {attempt}회째 성공)"
                return body, f"HTTP {resp.status}{suffix}"
        except urllib.error.HTTPError as e:
            # HTTP 응답을 받은 것이므로 재시도하지 않는다(404=robots 없음 등 확정 신호).
            return None, f"HTTP {e.code}"
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            last = f"연결 실패: {type(e).__name__}"
        except Exception as e:  # noqa: BLE001
            last = f"오류: {type(e).__name__}"
        if attempt < RETRIES:
            time.sleep(BACKOFF_SEC * attempt)
    return None, f"{last} ({RETRIES}회 재시도 후)"


def check(group: str, name: str, host: str, path: str) -> Result:
    body, status = _fetch_robots(host)

    if body is None:
        if status == "HTTP 404":
            # robots 없음 = 규칙 없음. 기술적으로는 허용이나 '규칙 부재'임을 명시한다.
            return Result(group, name, host, ALLOWED, status,
                          "robots.txt 없음(규칙 부재). 허용이지만 이용약관 별도 확인 필요", has_rules=False)
        # ★접속 실패를 허용으로 해석하지 않는다(RFC 9309).
        return Result(group, name, host, UNKNOWN_BLOCKED, status,
                      "robots.txt를 가져오지 못함 → 자동수집 금지로 취급(허용 아님). 다른 망에서 재점검 필요")

    # 판정 근거(robots.txt 원문)를 해시·수집시각과 함께 보관한다.
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    header = "\n".join([
        f"# source: https://{host}/robots.txt",
        f"# fetched_at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"# http: {status}",
        f"# sha256: {digest}",
        "# ---- 이하 원문(수정 없음) ----",
        "",
    ])
    (_RAW_DIR / f"{host}.txt").write_text(header + body, encoding="utf-8")

    rp = RobotFileParser()
    rp.parse(body.splitlines())
    can = rp.can_fetch(USER_AGENT, f"https://{host}{path}")
    delay = rp.crawl_delay(USER_AGENT)
    sitemaps = rp.site_maps() or []
    # Disallow 규칙이 하나라도 있으면 '규칙 있음'으로 본다.
    has_rules = any(line.strip().lower().startswith("disallow:") and line.split(":", 1)[1].strip()
                    for line in body.splitlines())

    return Result(
        group, name, host,
        ALLOWED if can else DISALLOWED,
        status,
        f"경로 {path} 기준" + ("" if can else " — Disallow에 걸림"),
        crawl_delay=str(delay) if delay is not None else None,
        sitemaps=list(sitemaps),
        has_rules=has_rules,
    )


def main() -> None:
    results: list[Result] = []
    for i, (group, name, host, path) in enumerate(TARGETS):
        if i:
            time.sleep(DELAY_SEC)
        r = check(group, name, host, path)
        results.append(r)
        print(f"[{r.verdict:28s}] {name} ({host}) {r.http_status}")

    mark = {ALLOWED: "허용", DISALLOWED: "금지", UNKNOWN_BLOCKED: "확인불가(금지 취급)"}
    lines = [
        f"# 보험사 robots.txt 자동수집 허용 여부 점검 — {date.today().isoformat()}",
        "",
        f"- 도구: `scripts/crawl/check_robots.py` (조회만 수행, **약관 PDF를 내려받지 않음**)",
        f"- User-Agent: `{USER_AGENT}`",
        "- ★**접속 실패는 '허용'이 아니라 '금지'로 취급**한다(RFC 9309). 응답이 없으니 일단 긁는 것은 폴백이다.",
        "- ★**robots 통과 = 수집 허가 아님.** 사이트 이용약관·저작권·재배포 조건은 별도 확인이 필요하다.",
        "",
        f"### 판정 근거 원문",
        "",
        f"각 호스트의 **robots.txt 원문**을 `{_RAW_DIR.name}/<호스트>.txt`에 보관한다"
        " (출처 URL·수집시각·HTTP 상태·sha256 헤더 포함). 판정만 남기면 나중에 근거를 제시할 수 없다.",
        f"기계 판독용 판정 요약은 `{_OUT.with_suffix('.json').name}`.",
        "",
        "| 구분 | 회사 | 호스트 | 판정 | 응답 | crawl-delay | 규칙 有 | 비고 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r.group} | {r.name} | `{r.host}` | **{mark[r.verdict]}** | {r.http_status} | "
            f"{r.crawl_delay or '-'} | {'예' if r.has_rules else '아니오'} | {r.detail} |"
        )

    from collections import Counter
    c = Counter(r.verdict for r in results)
    lines += [
        "",
        "## 요약",
        "",
        f"- 허용: **{c[ALLOWED]}건** / 금지: **{c[DISALLOWED]}건** / 확인불가(금지 취급): **{c[UNKNOWN_BLOCKED]}건**",
        f"- 총 {len(results)}개 호스트 점검",
        "",
        "## 다음 단계 (수집 전 필수)",
        "",
        "1. `확인불가`는 다른 네트워크에서 재점검한다. 재점검 전에는 **수집 대상에서 제외**한다.",
        "2. `허용`이어도 **사이트 이용약관·저작권 표시**를 확인한다. 약관 PDF는 저작물이다.",
        "3. 협회 공시실을 **1차 카탈로그**로, 보험사 원문을 최종 근거로 쓴다(Codex 권고).",
        "4. 수집 시 호스트당 저속·연속 요청 금지·`ETag/Last-Modified` 활용·킬스위치를 둔다.",
        "",
        "## 하지 않을 것",
        "",
        "CAPTCHA·인증·차단 우회 · User-Agent 위장 · 무제한 병렬 수집 · 불확실 문서의 자동 확정 ·"
        " 현행 약관을 가입 시점 약관으로 대체 · 권리 확인 없는 원문 재배포",
    ]
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 기계 판독용 사본
    js = _OUT.with_suffix(".json")
    js.write_text(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n허용 {c[ALLOWED]} / 금지 {c[DISALLOWED]} / 확인불가 {c[UNKNOWN_BLOCKED]}")
    print(f"저장: {_OUT.relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
