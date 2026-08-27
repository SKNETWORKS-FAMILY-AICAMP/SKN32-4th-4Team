"""가상 에이전트 시뮬레이터 — 실행기 한 벌.

★왜 어댑터에 두나

    CLI(`scripts/simulate_agents.py`)와 관리자 화면이 **각자 구현하면 반드시 어긋난다.**
    "화면에서 돌린 것"과 "CLI 로 돌린 것"의 결과가 다르면 어느 쪽이 맞는지 아무도 모른다.
    그래서 생성 규칙·승격 방법·분포를 여기 한 벌만 둔다.

★★**실제 HTTP 로 붙는다.** 저장소를 직접 부르지 않는다.

    관리 프로세스 안에서 `demo_submission_store.store()` 를 바로 부르면 편하지만,
    그러면 라우터·스키마 검증·멱등·관측 이벤트를 전부 건너뛴다. 그건
    "가상 에이전트가 접속해서 쌓는다"의 시연이 아니라 **파일을 늘리는 것**이다.
    고객 서버가 안 떠 있으면 `InfraError` 로 **명시적으로 실패**한다(폴백 금지).

★상태는 프로세스 메모리에 있다

    서버를 재시작하면 "실행 중"이 사라진다. 그건 결함이 아니라 사실이므로
    상태 응답에 `volatile: true` 로 밝힌다. 이미 만들어진 데이터는 파일에 남는다.

★정지는 **협조적 취소**다

    스레드를 강제로 죽이지 않는다. 루프가 매 건마다 플래그를 확인하고 스스로 멈춘다.
    강제 종료하면 파일을 쓰는 도중에 끊겨 반쪽 레코드가 남는다.
"""

from __future__ import annotations

import json
import random
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.core.errors import ConflictErr, InfraError, ValidationErr

from app.core.domain.synthetic_validation import (
    ALLOWED_AGE_BANDS,
    ALLOWED_INSURERS,
    ALLOWED_KCD_CODES,
)

INSURERS = sorted(ALLOWED_INSURERS)
CODES = sorted(ALLOWED_KCD_CODES)
AGE_BANDS = sorted(ALLOWED_AGE_BANDS)

#: ★결과 분포를 **일부러 부지급 쪽으로 채운다.**
#:   전부 `paid` 면 "승인율 100%"라는 거짓 화면이 나오고,
#:   계획서 §5-2(생존 편향) 경고가 무슨 말인지 화면에서 확인할 수 없다.
OUTCOMES = ["paid"] * 5 + ["denied"] * 3 + ["partial"] * 2

#: 한 번에 만들 수 있는 상한. 화면에서 실수로 큰 수를 넣어 서버를 묶지 않게.
MAX_AGENTS = 200
MAX_CASES = 50
MAX_TOTAL = 2000


@dataclass
class SimState:
    running: bool = False
    started_at: str = ""
    finished_at: str = ""
    planned: int = 0
    submitted: int = 0
    promoted: int = 0
    rejected: int = 0
    duplicated: int = 0
    failed: int = 0
    stop_requested: bool = False
    #: ★멈춘 이유를 남긴다. "그냥 끝남"과 "오류로 끊김"과 "사람이 멈춤"은 다르다.
    last_error: str = ""
    stopped_by: str = ""
    #: 같은 seed로 다시 실행해도 **새 실행**임을 구분한다. 사례 재시도 멱등성과
    #: 시뮬레이션 재실행을 같은 것으로 취급하면 두 번째 실행이 전부 중복된다.
    run_id: str = ""
    params: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "planned": self.planned,
            "submitted": self.submitted,
            "promoted": self.promoted,
            "rejected": self.rejected,
            "duplicated": self.duplicated,
            "failed": self.failed,
            "stop_requested": self.stop_requested,
            "last_error": self.last_error,
            "stopped_by": self.stopped_by,
            "run_id": self.run_id,
            "params": self.params,
            "data_source": "synthetic",
            #: ★프로세스 메모리다. 재시작하면 이 상태는 사라진다(데이터는 남는다).
            "volatile": True,
        }


_state = SimState()
_lock = threading.Lock()
_thread: threading.Thread | None = None


def status() -> dict:
    with _lock:
        return _state.as_dict()


def _post(base: str, path: str, body: dict, timeout: int = 20) -> tuple[int, dict]:
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"detail": raw[:200]}
    except urllib.error.URLError as e:
        raise InfraError(f"고객 서버에 연결할 수 없습니다({base}): {e.reason}") from e


def _validate(agents: int, cases: int, codes: list[str], delay_ms: int) -> None:
    if not 1 <= agents <= MAX_AGENTS:
        raise ValidationErr(f"에이전트 수는 1~{MAX_AGENTS} 이어야 합니다: {agents}")
    if not 1 <= cases <= MAX_CASES:
        raise ValidationErr(f"에이전트당 건수는 1~{MAX_CASES} 이어야 합니다: {cases}")
    if agents * cases > MAX_TOTAL:
        raise ValidationErr(
            f"총 생성량이 상한을 넘습니다: {agents}×{cases}={agents * cases} > {MAX_TOTAL}"
        )
    if not 0 <= delay_ms <= 5000:
        raise ValidationErr(f"간격은 0~5000ms 이어야 합니다: {delay_ms}")
    for c in codes:
        if not c.strip():
            raise ValidationErr("빈 질병기호가 들어 있습니다.")


def _run(*, base: str, agents: int, cases: int, codes: list[str], delay_ms: int,
         auto_verify: bool, seed: int, run_id: str) -> None:
    rnd = random.Random(seed)
    delay = delay_ms / 1000.0

    try:
        for i in range(1, agents + 1):
            ref = f"sim-agent-{i:03d}"
            for case_no in range(1, cases + 1):
                with _lock:
                    if _state.stop_requested:
                        return

                body = {
                    "client_ref": ref,
                    "insurer": rnd.choice(INSURERS),
                    "enrolled_on": f"20{rnd.randint(15, 24)}{rnd.randint(1, 12):02d}"
                                   f"{rnd.randint(1, 28):02d}",
                    "kcd_codes": [rnd.choice(codes)],
                    "age_band": rnd.choice(AGE_BANDS),
                    "outcome": rnd.choice(OUTCOMES),
                    "outcome_reason": "시뮬레이션 생성",
                    # 실행별 ID + 실행 안의 사례 순번이다. 같은 요청을 재전송하면
                    # 같은 키라 중복 차단되고, 같은 seed로 새로 실행하면 run_id가
                    # 달라져 새 사례로 접수된다.
                    "idempotency_key": f"sim-{run_id}-{i:03d}-{case_no:03d}",
                    "simulation_run_id": run_id,
                    "simulation_case_no": case_no,
                    "auto_validate": auto_verify,
                }
                status_code, res = _post(base, "/v1/demo/observations", body)

                with _lock:
                    if status_code != 202:
                        _state.failed += 1
                        #: ★실패를 세기만 하지 않고 **마지막 사유를 남긴다.**
                        _state.last_error = f"HTTP {status_code}: {str(res)[:120]}"
                        continue
                    if res.get("duplicate"):
                        _state.duplicated += 1
                        continue
                    _state.submitted += 1
                    if res.get("promoted"):
                        _state.promoted += 1
                    elif auto_verify and res.get("verification") == "rejected":
                        _state.rejected += 1

                if delay:
                    #: 정지 요청에 빨리 반응하도록 잘게 쪼개 잔다.
                    slept = 0.0
                    while slept < delay:
                        with _lock:
                            if _state.stop_requested:
                                return
                        threading.Event().wait(min(0.05, delay - slept))
                        slept += 0.05
    except (InfraError, ValidationErr) as e:
        with _lock:
            _state.last_error = str(e)[:200]
            _state.stopped_by = "error"
    except Exception as e:  # noqa: BLE001
        with _lock:
            _state.last_error = f"{type(e).__name__}: {str(e)[:180]}"
            _state.stopped_by = "error"
    finally:
        with _lock:
            _state.running = False
            _state.finished_at = datetime.now(timezone.utc).isoformat()
            if not _state.stopped_by:
                _state.stopped_by = "user" if _state.stop_requested else "completed"


def start(*, base: str, agents: int, cases: int, codes: list[str],
          delay_ms: int, auto_verify: bool, seed: int) -> dict:
    """시뮬레이션을 시작한다. 이미 돌고 있으면 **거절한다**(조용히 덮어쓰지 않는다)."""
    global _thread

    codes = [c.strip().upper() for c in codes if c.strip()] or list(CODES)
    _validate(agents, cases, codes, delay_ms)

    with _lock:
        if _state.running:
            raise ConflictErr("이미 시뮬레이션이 실행 중입니다. 먼저 정지하세요.")
        _state.__init__()  # type: ignore[misc]  # 상태 초기화
        _state.running = True
        _state.started_at = datetime.now(timezone.utc).isoformat()
        _state.planned = agents * cases
        _state.run_id = uuid.uuid4().hex[:12]
        _state.params = {
            "base": base, "agents": agents, "cases": cases, "codes": codes,
            "delay_ms": delay_ms, "auto_verify": auto_verify, "seed": seed,
        }
        if auto_verify:
            from app.core.domain.synthetic_validation import RULE_VERSION

            _state.params["validation_rule_version"] = RULE_VERSION

    _thread = threading.Thread(
        target=_run, kwargs=dict(base=base, agents=agents, cases=cases, codes=codes,
                                 delay_ms=delay_ms, auto_verify=auto_verify, seed=seed,
                                 run_id=_state.run_id),
        name="demo-simulator", daemon=True,
    )
    _thread.start()
    return status()


def stop() -> dict:
    """정지를 **요청**한다. 루프가 다음 건에서 스스로 멈춘다(강제 종료 없음)."""
    with _lock:
        if not _state.running:
            raise ConflictErr("실행 중인 시뮬레이션이 없습니다.")
        _state.stop_requested = True
    return status()


def reset() -> dict:
    """합성 트랙을 비운다. **실행 중에는 거절한다** — 쓰는 도중에 지우면 반쪽이 남는다."""
    with _lock:
        if _state.running:
            raise ConflictErr("시뮬레이션 실행 중에는 초기화할 수 없습니다. 먼저 정지하세요.")

    from app.adapters import demo_submission_store as demo

    result = demo.reset()

    with _lock:
        _state.__init__()  # type: ignore[misc]
    return result


__all__ = [
    "AGE_BANDS", "CODES", "INSURERS", "MAX_AGENTS", "MAX_CASES", "MAX_TOTAL",
    "OUTCOMES", "SimState", "reset", "start", "status", "stop",
]
