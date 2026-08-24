"""시범 수집기 (S6) — 소수 문서만 받아 파이프라인을 검증한다.

이 도구가 **하는 일**: 지정한 URL을 robots 재확인 후 내려받고, 원본과 함께
`url·http상태·content-type·크기·sha256·수집시각`을 **불변 기록**한다.

이 도구가 **하지 않는 일**:
- 문서가 무엇인지 판정하지 않는다(어느 보험사·상품·세대·시행일인지). 그건 별도 식별 단계이며,
  여기서는 전부 `unidentified`로 남긴다. **받았다는 이유로 무엇인지 안다고 하지 않는다.**
- 사이트를 크롤링하지 않는다. 명시한 URL만 받는다(탐색은 사람이 확인한 뒤 넘긴다).

설계 원칙:
- **robots를 매번 재확인**한다. 이전 점검 결과를 믿고 넘어가지 않는다(정책은 바뀐다).
- 확인 실패·금지면 **받지 않고 명시적으로 실패**한다(RFC 9309, 무폴백).
- 식별 가능한 UA, 호스트별 지연, 재시도 백오프.
- 원문은 `data/raw/`에 저장하고 **저장소에 커밋하지 않는다** — 약관은 저작물이며
  권리 확인 없는 재배포를 금지한다(Codex). 커밋되는 것은 메타데이터뿐이다.

실행: python -m scripts.crawl.fetch_pilot
"""

from __future__ import annotations

import hashlib
import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from app.core.errors import InfraError, ValidationErr

USER_AGENT = "BarobomResearchBot/0.1 (+contact: set-before-deploy; purpose: insurance-terms-research)"
TIMEOUT = 30
RETRIES = 3
BACKOFF_SEC = 2.0
PER_HOST_DELAY = 3.0
MAX_BYTES = 60 * 1024 * 1024  # 단일 문서 상한(무제한 수신 금지)

_ROOT = Path(__file__).resolve().parents[2]
_RAW = _ROOT / "data" / "raw" / "insurance_terms"
_MANIFEST = _ROOT / "data" / "raw" / "fetch_manifest.jsonl"

#: 시범 대상 — 사람이 확인한 URL만 넣는다. 자동 탐색으로 늘리지 않는다.
PILOT_URLS: list[tuple[str, str]] = [
    ("삼성화재", "https://www.samsungfire.com/publication/pdf/ZPB293050_0_20250101_file1.pdf"),
]


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
    robots_verdict: str
    #: 이 문서가 무엇인지는 **아직 모른다**. 식별 단계에서 채운다.
    identification: str = "unidentified"


def _robots_allows(url: str) -> tuple[bool, str]:
    """수집 직전 robots를 재확인한다. 실패는 '허용'이 아니다."""
    host = urlparse(url).netloc
    robots_url = f"https://{host}/robots.txt"
    req = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
    last = "시도 없음"
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                rp = RobotFileParser()
                rp.parse(resp.read().decode("utf-8", errors="replace").splitlines())
                ok = rp.can_fetch(USER_AGENT, url)
                return ok, f"HTTP {resp.status} → {'allow' if ok else 'disallow'}"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return True, "robots.txt 없음(규칙 부재)"
            return False, f"robots HTTP {e.code}"
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            last = f"연결 실패: {type(e).__name__}"
        except Exception as e:  # noqa: BLE001
            last = f"오류: {type(e).__name__}"
        if attempt < RETRIES:
            time.sleep(BACKOFF_SEC * attempt)
    return False, f"robots 확인 불가({last}) → 금지로 취급"


def fetch_one(insurer: str, url: str) -> FetchRecord:
    allowed, verdict = _robots_allows(url)
    if not allowed:
        # 무폴백: 못 받으면 조용히 건너뛰지 않고 명시적으로 실패시킨다.
        raise InfraError(f"robots가 허용하지 않습니다: {url} ({verdict})")

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
            body = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise InfraError(f"수집 실패 HTTP {e.code}: {url}") from e
    except Exception as e:  # noqa: BLE001
        raise InfraError(f"수집 실패({type(e).__name__}): {url}") from e

    if len(body) > MAX_BYTES:
        raise ValidationErr(f"문서가 상한({MAX_BYTES}바이트)을 초과했습니다: {url}")
    if not body:
        raise InfraError(f"빈 응답: {url}")

    digest = hashlib.sha256(body).hexdigest()
    name = urlparse(url).path.rsplit("/", 1)[-1] or f"{digest[:16]}.bin"
    _RAW.mkdir(parents=True, exist_ok=True)
    saved = _RAW / f"{digest[:12]}_{name}"
    saved.write_bytes(body)

    return FetchRecord(
        insurer=insurer, url=url, http_status=status, content_type=ctype,
        bytes=len(body), sha256=digest,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        saved_as=str(saved.relative_to(_ROOT)).replace("\\", "/"),
        robots_verdict=verdict,
    )


def main() -> None:
    records: list[FetchRecord] = []
    failures: list[tuple[str, str]] = []
    for i, (insurer, url) in enumerate(PILOT_URLS):
        if i:
            time.sleep(PER_HOST_DELAY)
        try:
            rec = fetch_one(insurer, url)
            records.append(rec)
            print(f"[OK] {insurer} {rec.bytes:,}B sha256={rec.sha256[:12]} -> {rec.saved_as}")
        except (InfraError, ValidationErr) as e:
            failures.append((url, str(e)))
            print(f"[FAIL] {insurer} {e}")

    if records:
        _MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        with _MANIFEST.open("a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        print(f"\n메타데이터 기록: {_MANIFEST.relative_to(_ROOT)} (+{len(records)}건)")

    print(f"성공 {len(records)} / 실패 {len(failures)}")
    if failures:
        print("실패 목록:")
        for url, why in failures:
            print(f"  - {url}: {why}")
    print("\n※ 받은 문서가 '무엇인지'는 아직 판정하지 않았다(identification=unidentified).")
    print("※ 원문은 data/raw/ 에 저장되며 저장소에 커밋하지 않는다(저작물·재배포 금지).")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
