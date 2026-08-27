"""리랭커 **별도 프로세스** 워커 — 진짜로 취소할 수 있는 유일한 방법.

★왜 프로세스인가

    스레드 워커(`rerank_worker.py`)는 시한이 지나도 **추론을 못 멈춘다.**
    파이썬 스레드에는 강제 종료가 없고 torch 추론은 중간에 끊기지 않는다.
    그래서 거기서는 「이 요청을 포기한다」까지만 하고, 계산이 끝날 때까지
    새 요청을 503 으로 거절했다 — 정직하지만 **한 건이 막히면 한동안 멈춘다.**

    프로세스는 **죽일 수 있다.** 시한이 지나면 SIGKILL 로 끊고 새로 띄운다.
    OOM 도 마찬가지다 — 프로세스가 죽어도 서버는 산다.

★대가를 정확히 적는다
    · 무게추를 **다시** 올려야 한다(4B ≈ 수십 초). 죽이는 것은 싸지 않다.
    · 후보를 프로세스 경계로 넘겨야 한다(피클). 큰 본문은 그 비용이 든다.
    · 그래서 **기본은 스레드 워커**다. 이쪽은 시한 초과가 잦거나 OOM 이
      실제로 관측될 때 켠다(`CLAUSE_RERANK_WORKER=process`).

★자식 정리 — `daemon=True` 로 두고 종료 때 반드시 `stop()` 한다.
  안 그러면 GPU 를 문 유령 프로세스가 남는다.

★★**그래도 남는 경우가 있다(2026-08-25 실측).** `daemon=True` 는 파이썬이
  **정상 종료할 때** 자식을 정리한다는 뜻이지, 부모가 어떻게 죽어도 따라 죽는다는
  뜻이 아니다. 부모가 `SIGKILL`(또는 Windows 의 강제 종료)로 사라지면 정리 훅이
  안 돌아 **자식이 고아로 남는다** — 실제로 배경 작업을 끊었더니 자식이 696 MB 를
  문 채 살아 있었다.

  운영에서 이것이 문제가 되면(GPU 를 문 유령이 다음 적재를 OOM 시킨다)
  프로세스 그룹·job object 로 묶어야 한다. 지금은 **그 한계를 알고 쓴다** —
  서버를 정상 종료(`SIGTERM`)하면 정리된다.
"""

from __future__ import annotations

import multiprocessing as mp
import queue as _q
import threading
import time
from dataclasses import dataclass, field


class ProcessRerankTimeout(TimeoutError):
    """시한 초과 — **자식 프로세스를 죽였다.** 계산은 실제로 멈췄다."""


class ProcessRerankUnavailable(RuntimeError):
    """자식을 띄우지 못했거나 무게추 적재에 실패했다."""


# ── 자식 프로세스에서 도는 것 ────────────────────────────────────────

def _child_main(spec: dict, jobs, results) -> None:
    """자식 프로세스의 본체.

    ★여기서 예외를 **문자열로** 돌려보낸다. 예외 객체를 그대로 피클하면
      부모에 없는 클래스일 때 되살리다 또 터진다.
    """
    try:
        #: ★자식은 부모의 monkeypatch 를 물려받지 않는다(spawn). 그래서 **무엇을 만들지**
        #:   경로로 받아 여기서 import 한다 — 시험이 가짜를 끼울 수 있는 유일한 자리다.
        import importlib

        mod_name, _, cls_name = spec.get(
            "reranker_class", "app.adapters.reranker:CrossEncoderReranker").partition(":")
        cls = getattr(importlib.import_module(mod_name), cls_name)

        model = cls(
            spec["model"], device=spec["device"], batch_size=spec["batch_size"],
            max_length=spec["max_length"], dtype=spec["dtype"],
            trust_remote_code=spec["trust_remote_code"],
        )
        #: 예열까지 자식에서 끝낸다 — 첫 실제 일감이 적재 시간을 물면 안 된다.
        from app.application.ports import Evidence

        model.rerank("예열", [
            Evidence(content="예열용 본문입니다", source="w", locator="w1",
                     score=0.0, backend="warmup"),
            Evidence(content="다른 예열 본문입니다", source="w", locator="w2",
                     score=0.0, backend="warmup"),
        ])
        results.put(("ready", None))
    except BaseException as exc:  # noqa: BLE001 — 적재 실패를 부모에 알린다
        results.put(("load_error", f"{type(exc).__name__}: {exc}"[:300]))
        return

    while True:
        item = jobs.get()
        if item is None:
            return
        job_id, query, evidence, top_n = item
        try:
            out = model.rerank(query, evidence, top_n=top_n)
            results.put(("ok", (job_id, out)))
        except BaseException as exc:  # noqa: BLE001
            results.put(("error", (job_id, f"{type(exc).__name__}: {exc}"[:300])))


# ── 부모 쪽 ──────────────────────────────────────────────────────────

@dataclass
class _Stats:
    starts: int = 0
    loaded: bool = False
    load_seconds: float | None = None
    load_error: str | None = None
    submitted: int = 0
    completed: int = 0
    failed: int = 0
    timeouts: int = 0
    kills: int = 0
    latencies_ms: list = field(default_factory=list)


class RerankProcessWorker:
    """자식 프로세스 하나를 붙들고, 시한이 지나면 **죽인다.**"""

    def __init__(self, spec: dict, *, start_timeout: float = 600.0) -> None:
        self._spec = dict(spec)
        self._start_timeout = start_timeout
        self._ctx = mp.get_context("spawn")   # fork 는 CUDA 와 함께 쓰면 깨진다
        self._proc = None
        self._jobs = None
        self._results = None
        self._lock = threading.Lock()
        self._stats = _Stats()
        self._next_id = 0

    # ── 수명 ────────────────────────────────────────────────────────
    def start(self) -> None:
        with self._lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            return
        t0 = time.perf_counter()
        self._jobs = self._ctx.Queue()
        self._results = self._ctx.Queue()
        self._proc = self._ctx.Process(
            target=_child_main, args=(self._spec, self._jobs, self._results),
            name="rerank-proc", daemon=True,
        )
        try:
            self._proc.start()
        except RuntimeError as exc:
            #: ★★spawn 은 자식에서 **`__main__` 을 다시 임포트한다.** 그래서 진입점이
            #:   `if __name__ == "__main__":` 가드 없이 실행 코드를 두고 있으면
            #:   파이썬이 여기서 막는다(안 막으면 서버가 재귀로 뜬다).
            #:
            #:   그대로 두면 날 것의 multiprocessing 역추적이 요청 경로로 새어 나가
            #:   호출부가 `ProcessRerankUnavailable` 을 못 잡는다 — 실제로 그랬다.
            #:   **무엇을 고쳐야 하는지** 말해 주는 예외로 바꾼다.
            self._proc = None
            self._stats.load_error = (
                f"리랭크 자식 프로세스를 띄우지 못했습니다: {exc}"[:300]
                + " ─ 진입 스크립트를 `if __name__ == \"__main__\":` 로 감싸거나 "
                  "`CLAUSE_RERANK_WORKER=thread` 로 두세요."
            )
            raise ProcessRerankUnavailable(self._stats.load_error) from exc
        self._stats.starts += 1
        self._stats.loaded = False
        self._stats.load_error = None
        #: ★★**죽은 자식을 기다리지 않는다.**
        #:   전에는 `results.get(timeout=start_timeout)` 한 번으로 기다렸다. 그런데
        #:   자식이 적재 중 **세그폴트로 죽으면 큐에 아무것도 안 들어온다** — 부모는
        #:   시한(600초)을 꽉 채우고서야 「준비되지 않았습니다」라고 말한다.
        #:   실측 2026-08-25: RAM 이 모자라 자식이 죽었는데 부모가 10분을 그냥 기다렸고,
        #:   밖에서는 **아무 일도 안 일어나는 것처럼** 보였다.
        #:
        #:   짧게 끊어 기다리며 **살아 있는지 확인**한다. 죽었으면 즉시 말한다.
        deadline = time.perf_counter() + self._start_timeout
        while True:
            try:
                kind, payload = self._results.get(timeout=0.5)
                break
            except _q.Empty:
                if not self._proc.is_alive():
                    code = self._proc.exitcode
                    self._kill_locked()
                    self._stats.load_error = (
                        f"리랭크 자식 프로세스가 무게추 적재 중 죽었습니다"
                        f"(종료코드 {code}). 메모리 부족(OOM)일 가능성이 큽니다 — "
                        f"더 작은 모델이나 dtype, 또는 여유 있는 기계가 필요합니다."
                    )
                    raise ProcessRerankUnavailable(self._stats.load_error) from None
                if time.perf_counter() >= deadline:
                    self._kill_locked()
                    self._stats.load_error = f"자식이 {self._start_timeout:.0f}초 안에 준비되지 않았습니다"
                    raise ProcessRerankUnavailable(self._stats.load_error) from None
        if kind == "load_error":
            self._kill_locked()
            self._stats.load_error = payload
            raise ProcessRerankUnavailable(payload)
        self._stats.loaded = True
        self._stats.load_seconds = round(time.perf_counter() - t0, 1)

    def _kill_locked(self) -> None:
        """★유령을 남기지 않는다. GPU 를 문 채 살아 있으면 다음 적재가 OOM 이다."""
        p = self._proc
        self._proc = None
        if p is None:
            return
        try:
            p.kill()
            p.join(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        self._stats.kills += 1

    def stop(self, timeout: float = 10.0) -> None:
        with self._lock:
            if self._proc is None:
                return
            try:
                self._jobs.put(None)
                self._proc.join(timeout=timeout)
            except Exception:  # noqa: BLE001
                pass
            if self._proc is not None and self._proc.is_alive():
                self._kill_locked()
            else:
                self._proc = None

    # ── 사용 ────────────────────────────────────────────────────────
    def rerank(self, query: str, evidence: list, *, top_n: int | None,
               timeout: float) -> list:
        """일감을 자식에 넘긴다. 시한이 지나면 **자식을 죽이고** 예외를 올린다."""
        with self._lock:
            if self._proc is None or not self._proc.is_alive():
                self._start_locked()
            self._next_id += 1
            job_id = self._next_id
            self._stats.submitted += 1
            self._jobs.put((job_id, query, evidence, top_n))
            started = time.perf_counter()

            deadline = started + timeout
            while True:
                left = deadline - time.perf_counter()
                if left <= 0:
                    #: ★★여기가 스레드 워커와 갈리는 지점 — **실제로 멈춘다.**
                    self._kill_locked()
                    self._stats.timeouts += 1
                    raise ProcessRerankTimeout(
                        f"리랭킹이 {timeout:.0f}초를 넘겨 자식 프로세스를 종료했습니다. "
                        f"다음 요청에서 무게추를 다시 적재합니다."
                    )
                try:
                    kind, payload = self._results.get(timeout=min(left, 0.5))
                except _q.Empty:
                    if not self._proc.is_alive():
                        #: 자식이 스스로 죽었다(OOM 등). 조용히 넘기지 않는다.
                        self._proc = None
                        self._stats.failed += 1
                        raise ProcessRerankUnavailable(
                            "리랭크 자식 프로세스가 죽었습니다(OOM 등). 다음 요청에서 다시 띄웁니다."
                        ) from None
                    continue

                if kind == "ok":
                    got_id, out = payload
                    if got_id != job_id:
                        continue          # 지난 일감의 늦은 결과 — 버린다
                    elapsed = (time.perf_counter() - started) * 1000
                    self._stats.completed += 1
                    self._stats.latencies_ms.append(elapsed)
                    del self._stats.latencies_ms[:-200]
                    return out
                if kind == "error":
                    got_id, msg = payload
                    if got_id != job_id:
                        continue
                    self._stats.failed += 1
                    raise RuntimeError(msg)

    def stats(self) -> dict:
        import statistics

        with self._lock:
            lat = sorted(self._stats.latencies_ms)
            alive = bool(self._proc is not None and self._proc.is_alive())
            s = {
                "mode": "process",
                "alive": alive,
                "loaded": self._stats.loaded and alive,
                "load_seconds": self._stats.load_seconds,
                "load_error": self._stats.load_error,
                "starts": self._stats.starts,
                "kills": self._stats.kills,
                "submitted": self._stats.submitted,
                "completed": self._stats.completed,
                "failed": self._stats.failed,
                "timeouts": self._stats.timeouts,
                #: 프로세스 워커는 시한에 자식을 죽이므로 **버려진 일감이 남지 않는다.**
                "rejected_busy": 0,
                "abandoned_in_flight": 0,
                "busy_with_abandoned": False,
                "queue_depth": 0,
            }
        if lat:
            s["latency_ms"] = {
                "p50": round(statistics.median(lat), 1),
                "p95": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 1),
                "n": len(lat),
            }
        return s
