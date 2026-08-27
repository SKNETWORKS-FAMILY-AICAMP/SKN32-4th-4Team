"""관리자 콘솔(admin_app) 접근을 **출발지 IP로도** 제한하는 스위치.

★왜 필요한가

    관리자 라우터는 이미 `require_admin`(로그인)으로 막혀 있다. 그런데 로그인
    자격증명이 유출되면 그걸로 끝이다 — 네트워크 레벨에서 한 번 더 막아 두면
    자격증명 유출과 별개로 접근 표면이 줄어든다.

    공유기 포트포워딩(NAT)은 보통 출발지 IP 필터링을 지원하지 않는다(기종마다
    다르고 신뢰할 수 없다) — 그래서 애플리케이션 레벨에서 강제한다.

★파일에 남긴다 — `llm_provider_override.py`와 같은 이유

    admin_app과 customer_app은 별도 프로세스로 뜬다. 관리자 화면에서 허용목록을
    바꾸면 그 값을 프로세스 메모리에만 두면 재시작 전까지는 최신값을 못 본다.
    파일로 공유하면 다음 요청부터 바로 반영된다(재시작 불필요 — 미들웨어가
    요청마다 `current()`를 읽는다).

★★**빈 목록 = 전체 허용**(잠금 방지)

    허용목록을 비워 두면 아무도 못 들어오는 게 아니라 **제한이 꺼진 것**이다.
    관리자가 첫 IP를 등록하기 전까지 스스로를 잠그지 않게 하기 위한 의도적 기본값
    이다 — `identification_mode.py`류의 "폴백 금지" 원칙과는 다른 축이다(그쪽은
    판정 정확성, 이쪽은 잠금 방지). 화면에는 이 사실을 반드시 드러낸다.
"""

from __future__ import annotations

import ipaddress
import json
from datetime import datetime, timezone
from pathlib import Path

from app.core.errors import InfraError, ValidationErr

_ROOT = Path(__file__).resolve().parents[3]
_ALLOWLIST_FILE = _ROOT / "config" / "admin_ip_allowlist.json"

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def _parse_entry(raw: str) -> _Network:
    """개별 IP("1.2.3.4")도 CIDR("1.2.3.0/24")도 받는다 — 둘 다 네트워크로 정규화한다."""
    text = raw.strip()
    if not text:
        raise ValidationErr("빈 IP 항목은 넣을 수 없습니다.")
    try:
        if "/" in text:
            return ipaddress.ip_network(text, strict=False)
        return ipaddress.ip_network(f"{ipaddress.ip_address(text)}/32"
                                     if ipaddress.ip_address(text).version == 4
                                     else f"{ipaddress.ip_address(text)}/128")
    except ValueError as e:
        raise ValidationErr(f"IP 형식이 아닙니다: {text!r} ({e})") from e


def current() -> list[str]:
    """현재 허용목록(정규화된 문자열). 파일이 없으면 빈 목록 — 즉 전체 허용
    (파일을 새로 만들지 않는다 — 읽기는 부작용이 없어야 한다)."""
    if not _ALLOWLIST_FILE.exists():
        return []
    try:
        raw = json.loads(_ALLOWLIST_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        #: ★깨진 설정을 조용히 "전체 허용"으로 때우지 않는다 — 그러면 잠금 목적
        #:   자체가 조용히 무력화된다. 읽기 실패는 명시적으로 알린다.
        raise InfraError(f"관리자 IP 허용목록을 읽지 못했습니다: {e}") from e
    ips = raw.get("ips")
    if not isinstance(ips, list) or not all(isinstance(x, str) for x in ips):
        raise InfraError(f"관리자 IP 허용목록 형식이 잘못됐습니다(설정 오염): {ips!r}")
    return ips


def is_allowed(client_ip: str) -> bool:
    """허용목록이 비어 있으면 True(전체 허용). 아니면 목록의 어느 항목에라도 속하면 True."""
    entries = current()
    if not entries:
        return True
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for e in entries:
        try:
            if addr in ipaddress.ip_network(e, strict=False):
                return True
        except ValueError:
            continue
    return False


def set_allowlist(ips: list[str], *, actor: str) -> dict:
    """허용목록을 통째로 바꾼다. **호출자가 이미 허용목록을 통과했음을 전제한다**
    (미들웨어가 먼저 걸러야 자기 자신을 실수로 잠그는 걸 요청 단계에서 막을 수 없다 —
    UI가 "현재 접속 IP"를 항상 같이 보여줘 실수를 줄인다, §화면).
    """
    if not (actor or "").strip():
        raise ValidationErr("허용목록을 바꾼 사람을 비워 둘 수 없습니다.")

    normalized = sorted({str(_parse_entry(ip)) for ip in ips})
    changed_at = datetime.now(timezone.utc).isoformat()
    state = {"ips": normalized, "changed_at": changed_at, "changed_by": actor}
    try:
        _ALLOWLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        _ALLOWLIST_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except OSError as e:
        raise InfraError(f"관리자 IP 허용목록을 저장하지 못했습니다: {e}") from e
    return state


__all__ = ["current", "is_allowed", "set_allowlist"]
