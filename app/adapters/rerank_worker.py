"""리랭커 전용 워커 — **무게추를 요청 경로에서 뺀다.**

★왜 필요한가 (2026-08-05 실측 → 2026-08-25 구현)

    앞서는 요청마다 `CrossEncoderReranker` 를 새로 만들어 `asyncio.to_thread` 로 돌렸다.
    그러면 세 가지가 요청 안에서 일어난다 —

      · **무게추 적재**(4B fp16 ≈ 9GB VRAM, 수십 초). 첫 요청이 그 시간을 다 문다.
      · **동시 실행**. 겹치면 GPU 가 OOM 이다.
      · **취소 불가**. 클라이언트가 끊어도 추론은 계속 돈다.

    전용 워커는 모델을 **한 번만** 적재해 자기 스레드에서 붙들고, 요청은 일감만 넣는다.

★★**시한이 지나도 추론은 안 멈춘다.** 파이썬 스레드는 강제 종료가 없고
  torch 추론은 중간에 끊기지 않는다. 그래서 이 워커는 **취소하는 척하지 않는다** —

    · 시한이 지나면 호출자에게 `RerankTimeout` 을 올린다(504 로 나간다).
    · 그 일감은 **버려진 것으로 표시**하고, 워커는 끝까지 계산한 뒤 결과를 버린다.
    · 버려진 일감이 도는 동안 **새 일감을 받지 않는다**(`RerankBusy` → 503).
      받아 봐야 큐에 쌓이기만 하고, 쌓이는 것은 밖에서 안 보인다.

  진짜 취소가 필요하면 **별도 프로세스**로 띄워 죽여야 한다. 지금은 그러지 않으므로
  「시한 초과 = 그 요청은 실패, 워커는 한동안 바쁨」이라고 **정직하게 말한다.**

★상태를 숨기지 않는다. `stats()` 가 적재 여부·처리 수·시한초과·버려진 일감·
  지연 분포를 그대로 낸다. `readiness` 가 이것을 읽는다.
"""

from __future__ import annotations

import statistics
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from queue import Empty, Queue


class RerankTimeout(TimeoutError):
    """시한 안에 끝나지 않았다. **추론은 계속 돌고 있다.**"""


class RerankBusy(RuntimeError):
    """앞선 일감이 시한을 넘겨 아직 돌고 있다. 새 일감을 받지 않는다."""


class RerankUnavailable(RuntimeError):
    """워커를 띄우지 못했다(무게추 적재 실패 등)."""


@dataclass
class _Job:
    query: str
    evidence: list
    top_n: int | None
    deadline: float
    future: Future
    abandoned: bool = False


@dataclass
class _Stats:
    loaded: bool = False
    load_seconds: float | None = None
    load_error: str | None = None
    submitted: int = 0
    completed: int = 0
    failed: int = 0
    timeouts: int = 0
    rejected_busy: int = 0
    abandoned_in_flight: int = 0
    latencies_ms: list = field(default_factory=list)


class RerankWorker:
    """모델을 붙들고 있는 **단일 스레드** 워커.

    `build_reranker` 는 무게추를 실제로 받아오는 함수다(테스트는 가짜를 준다).
    """

    def __init__(self, build_reranker, *, queue_size: int = 8) -> None:
        self._build = build_reranker
        self._q: Queue = Queue(maxsize=max(1, queue_size))
        self._stats = _Stats()
        self._lock = threading.Lock()
        self._reranker = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        #: 시한을 넘겨 버려진 일감이 아직 도는 중인가. 도는 동안은 새 일감을 안 받는다.
        self._in_flight_abandoned = threading.Event()

    # ── 수명 ────────────────────────────────────────────────────────
    def start(self) -> None:
        """워커 스레드를 띄운다. **무게추는 여기서 받는다** — 요청 경로가 아니다."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="rerank-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._q.put(None)  # 깨우기
        if self._thread:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        t0 = time.perf_counter()
        try:
            self._reranker = self._build()
            #: ★**예열까지 여기서 한다.** 첫 실제 질의가 적재 시간을 물면
            #:   「리랭킹이 40초 걸린다」로 잘못 읽힌다.
            self._warmup()
            with self._lock:
                self._stats.loaded = True
                self._stats.load_seconds = round(time.perf_counter() - t0, 1)
        except Exception as exc:  # noqa: BLE001 — 원인을 상태에 그대로 남긴다
            with self._lock:
                self._stats.load_error = f"{type(exc).__name__}: {exc}"[:300]
            return

        while not self._stop.is_set():
            try:
                job = self._q.get(timeout=0.5)
            except Empty:
                continue
            if job is None:
                break
            self._run_job(job)

    def _warmup(self) -> None:
        from app.application.ports import Evidence

        probe = [Evidence(content="예열용 본문입니다", source="w", locator="w1",
                          score=0.0, backend="warmup"),
                 Evidence(content="다른 예열 본문입니다", source="w", locator="w2",
                          score=0.0, backend="warmup")]
        self._reranker.rerank("예열", probe)

    def _run_job(self, job: _Job) -> None:
        started = time.perf_counter()
        try:
            out = self._reranker.rerank(job.query, job.evidence, top_n=job.top_n)
            ok, err = True, None
        except Exception as exc:  # noqa: BLE001
            out, ok, err = None, False, exc
        elapsed_ms = (time.perf_counter() - started) * 1000

        with self._lock:
            if job.abandoned:
                #: ★버려진 일감이다. 결과를 버리고, **버려졌다는 사실만 센다.**
                self._stats.abandoned_in_flight += 1
                self._in_flight_abandoned.clear()
                return
            if ok:
                self._stats.completed += 1
                self._stats.latencies_ms.append(elapsed_ms)
                del self._stats.latencies_ms[:-200]   # 최근 것만 들고 있는다
            else:
                self._stats.failed += 1
        if ok:
            job.future.set_result(out)
        else:
            job.future.set_exception(err)

    # ── 사용 ────────────────────────────────────────────────────────
    def rerank(self, query: str, evidence: list, *, top_n: int | None,
               timeout: float) -> list:
        """일감을 넣고 시한만큼 기다린다.

        시한이 지나면 `RerankTimeout` — **추론은 계속 돈다.** 위 모듈 설명 참조.
        """
        if self._in_flight_abandoned.is_set():
            with self._lock:
                self._stats.rejected_busy += 1
            raise RerankBusy(
                "앞선 요청이 시한을 넘겨 아직 계산 중입니다. "
                "추론은 중간에 끊을 수 없어 새 요청을 받지 않습니다."
            )
        if not self._thread or not self._thread.is_alive():
            with self._lock:
                err = self._stats.load_error
            raise RerankUnavailable(err or "리랭크 워커가 떠 있지 않습니다")
        with self._lock:
            if self._stats.load_error:
                raise RerankUnavailable(self._stats.load_error)

        fut: Future = Future()
        job = _Job(query=query, evidence=evidence, top_n=top_n,
                   deadline=time.monotonic() + timeout, future=fut)
        try:
            self._q.put_nowait(job)
        except Exception:  # noqa: BLE001 — 큐가 찼다
            with self._lock:
                self._stats.rejected_busy += 1
            raise RerankBusy("리랭크 대기열이 찼습니다") from None
        with self._lock:
            self._stats.submitted += 1

        try:
            return fut.result(timeout=timeout)
        except TimeoutError:
            with self._lock:
                job.abandoned = True
                self._stats.timeouts += 1
            self._in_flight_abandoned.set()
            raise RerankTimeout(
                f"리랭킹이 {timeout:.0f}초 안에 끝나지 않았습니다. "
                f"추론은 중간에 끊을 수 없어 계속 돌고 있으며, 그동안 새 요청을 받지 않습니다."
            ) from None

    def stats(self) -> dict:
        with self._lock:
            lat = sorted(self._stats.latencies_ms)
            s = {
                "loaded": self._stats.loaded,
                "load_seconds": self._stats.load_seconds,
                "load_error": self._stats.load_error,
                "alive": bool(self._thread and self._thread.is_alive()),
                "submitted": self._stats.submitted,
                "completed": self._stats.completed,
                "failed": self._stats.failed,
                "timeouts": self._stats.timeouts,
                "rejected_busy": self._stats.rejected_busy,
                "abandoned_in_flight": self._stats.abandoned_in_flight,
                "busy_with_abandoned": self._in_flight_abandoned.is_set(),
                "queue_depth": self._q.qsize(),
            }
        if lat:
            s["latency_ms"] = {
                "p50": round(statistics.median(lat), 1),
                "p95": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 1),
                "n": len(lat),
            }
        return s


# ── 프로세스에 하나만 둔다 ──────────────────────────────────────────
_WORKER: RerankWorker | None = None
_WORKER_LOCK = threading.Lock()


def get_worker(build_reranker=None) -> RerankWorker:
    """프로세스 전역 워커. 없으면 만들어 띄운다.

    ★두 번 만들면 무게추가 두 벌 올라가 GPU 가 터진다. 그래서 잠금으로 하나만 둔다.
    """
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is None:
            if build_reranker is None:
                raise RerankUnavailable("워커가 없고 build_reranker 도 주지 않았습니다")
            _WORKER = RerankWorker(build_reranker)
            _WORKER.start()
        return _WORKER


def peek_worker() -> "RerankWorker | None":
    """**만들지 않고** 들여다본다. 준비 상태 조회가 무게추를 올리면 안 된다."""
    return _WORKER


def reset_worker() -> None:
    """테스트 전용 — 워커를 내리고 지운다.

    ★시험이 심어 둔 **가짜**일 수도 있다. `stop()` 이 없다고 여기서 터지면
      정리가 안 돼 다음 시험이 남의 워커를 물려받는다 — 실제로 그랬다.
    """
    global _WORKER
    with _WORKER_LOCK:
        stop = getattr(_WORKER, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:  # noqa: BLE001 — 정리는 실패해도 지운다
                pass
        _WORKER = None
