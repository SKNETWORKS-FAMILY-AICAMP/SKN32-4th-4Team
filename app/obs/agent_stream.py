"""에이전트 상호작용 스트림 — 대시보드가 "지금 누가 무엇을 하고 있나"를 본다.

★★**프로세스가 둘이다.** 그래서 인메모리로는 안 된다.

    처음엔 인메모리 링버퍼로 만들었다. 단일 프로세스 개발 서버에서는 잘 돌았는데,
    실제 배치(`run_customer_server.py` 8080 / `run_admin_server.py` 8081)에서
    **관리 대시보드가 고객 포트의 트래픽을 하나도 못 봤다**(2026-08-04 실측:
    에이전트 6대가 제출했는데 화면에는 3대만 — 관리 프로세스가 자기 이벤트만 알았다).

    발표 중에 화면이 비어 보이면 "에이전트가 안 붙었나"로 오해한다.
    그래서 **append-only 파일 한 벌**을 두고 두 프로세스가 같은 것을 본다.
    이 프로젝트가 제출·코호트를 파일로 다루는 방식과 같다.

★그래도 이것은 **감사 기록이 아니다**

    쓰기 실패를 예외로 올리지 않고(관측이 제품을 죽이면 안 된다), 오래된 것은 잘라 읽는다.
    "여기 없다"가 "그런 일이 없었다"는 뜻이 아니다 — 감사는 `run_events`(DB)다.
    화면에도 그 사실을 적어 둔다.

★민감정보를 담지 않는다

    `detail` 에는 요약값만 넣는다. 처방전·본문·개인식별정보는 들어오지 않는다.
"""

from __future__ import annotations

import itertools
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_EVENTS = _ROOT / "data" / "obs" / "agent_events.jsonl"

#: 화면에 보일 만큼만 읽는다. 파일은 계속 자라도 읽는 양은 일정하다.
_TAIL_LIMIT = 300

#: 뒤에서부터 이만큼만 읽는다(≈300줄 여유). 전체를 메모리에 올리지 않는다.
_TAIL_BYTES = 256 * 1024

_seq = itertools.count(1)


@dataclass(frozen=True)
class AgentEvent:
    seq: int
    at: str
    kind: str
    client_ref: str
    #: `synthetic` / `verified_real` / `""`(해당 없음). 화면 배지가 이 값으로 갈린다.
    track: str = ""
    detail: dict = field(default_factory=dict)


def publish(kind: str, *, client_ref: str = "", track: str = "",
            detail: dict | None = None) -> AgentEvent:
    """이벤트 하나를 남긴다. **실패해도 요청을 깨뜨리지 않는다.**

    ★관측이 제품을 죽이면 안 된다. 다만 조용히 삼키지도 않는다 —
      쓰기에 실패하면 그 사실을 `write_failed` 로 표시해 돌려준다.
    """
    ev = AgentEvent(
        seq=next(_seq),
        at=datetime.now(timezone.utc).isoformat(),
        kind=kind,
        client_ref=client_ref or "-",
        track=track,
        detail=detail or {},
    )
    try:
        _EVENTS.parent.mkdir(parents=True, exist_ok=True)
        #: ★한 줄 append. 여러 프로세스가 동시에 써도 줄이 섞이지 않도록
        #:   한 번의 write 로 끝낸다(O_APPEND 는 원자적으로 끝에 붙인다).
        with _EVENTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(ev), ensure_ascii=False) + "\n")
    except OSError as e:  # noqa: BLE001
        return AgentEvent(ev.seq, ev.at, ev.kind, ev.client_ref, ev.track,
                          {**ev.detail, "write_failed": str(e)[:80]})
    return ev


def _tail_lines(limit: int) -> list[dict]:
    """파일 끝에서 최대 `limit` 줄. 없으면 빈 목록(장애가 아니라 '아직 없음')."""
    if not _EVENTS.exists():
        return []
    try:
        size = _EVENTS.stat().st_size
        with _EVENTS.open("rb") as f:
            if size > _TAIL_BYTES:
                f.seek(size - _TAIL_BYTES)
                f.readline()  # 잘린 첫 줄은 버린다
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []

    out: list[dict] = []
    for line in raw.splitlines()[-limit:]:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            #: ★깨진 줄 하나 때문에 화면 전체를 죽이지 않는다. 대신 보이게 남긴다.
            out.append({"seq": -1, "at": "", "kind": "_corrupt_line",
                        "client_ref": "-", "track": "", "detail": {}})
    return out


def recent(limit: int = 100) -> list[dict]:
    """최근 이벤트(새 것이 앞)."""
    return list(reversed(_tail_lines(min(limit, _TAIL_LIMIT))))


def read_from(offset: int) -> tuple[int, list[dict]]:
    """`offset` 바이트 이후에 새로 붙은 이벤트. SSE 꼬리읽기용.

    Returns:
        (다음 offset, 새 이벤트 목록)

    ★파일이 줄어들었으면(비워졌으면) 처음부터 다시 읽는다 —
      `--reset` 이나 회전 후에도 화면이 멈추지 않게.
    """
    if not _EVENTS.exists():
        return 0, []
    try:
        size = _EVENTS.stat().st_size
        if size < offset:
            offset = 0
        if size == offset:
            return offset, []
        with _EVENTS.open("rb") as f:
            f.seek(offset)
            chunk = f.read()
    except OSError:
        return offset, []

    #: 마지막 줄이 아직 다 안 써졌을 수 있다 — 완결된 줄까지만 소비한다.
    cut = chunk.rfind(b"\n")
    if cut == -1:
        return offset, []
    usable, consumed = chunk[: cut + 1], cut + 1

    out: list[dict] = []
    for line in usable.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return offset + consumed, out


def current_offset() -> int:
    try:
        return _EVENTS.stat().st_size if _EVENTS.exists() else 0
    except OSError:
        return 0


def agents(idle_after_s: int = 30) -> list[dict]:
    """`client_ref` 별 집계 — 누가 붙어 있고 무엇을 얼마나 했나.

    ★"접속 중"이라는 말을 쓰지 않는다. HTTP 는 연결을 유지하지 않으므로
      우리가 아는 것은 **마지막 요청이 언제였나** 뿐이다. `idle_s` 로 말한다.
    """
    now = datetime.now(timezone.utc)
    by: dict[str, dict] = {}
    for e in _tail_lines(_TAIL_LIMIT):
        ref = e.get("client_ref") or "-"
        a = by.setdefault(ref, {"events": 0, "kinds": Counter(),
                                "last_at": e.get("at", ""), "track": e.get("track", "")})
        a["events"] += 1
        a["kinds"][e.get("kind", "?")] += 1
        a["last_at"] = e.get("at", a["last_at"])
        if e.get("track"):
            a["track"] = e["track"]

    out = []
    for ref, a in by.items():
        try:
            idle = (now - datetime.fromisoformat(a["last_at"])).total_seconds()
        except (ValueError, TypeError):
            idle = -1.0
        out.append({
            "client_ref": ref,
            "events": a["events"],
            "kinds": dict(a["kinds"]),
            "last_at": a["last_at"],
            "idle_s": round(idle, 1),
            "active": 0 <= idle <= idle_after_s,
            "track": a["track"],
        })
    out.sort(key=lambda x: x["last_at"], reverse=True)
    return out


def sse(event: dict) -> str:
    """SSE 한 프레임. 개행이 프레임 경계라 본문에 날것으로 들어가면 안 된다."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def reset() -> None:
    """관측 파일을 비운다(테스트·시연 초기화용). 감사 기록은 건드리지 않는다."""
    try:
        if _EVENTS.exists():
            os.remove(_EVENTS)
    except OSError:
        pass


__all__ = [
    "AgentEvent", "agents", "current_offset", "publish", "read_from",
    "recent", "reset", "sse",
]
