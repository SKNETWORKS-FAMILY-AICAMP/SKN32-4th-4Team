"""별도 프로세스 워커 — **시한이 지나면 실제로 멈춘다.**

★스레드 워커와 갈리는 지점만 잰다.
  스레드는 「포기했지만 계산은 계속」이고, 프로세스는 **죽여서 멈춘다.**

★모델을 내려받지 않는다. 자식이 import 하는 `CrossEncoderReranker` 를
  가짜 모듈로 바꿔치기해 spawn 된 자식에서도 가짜가 걸리게 한다.
"""

from __future__ import annotations

import time

import pytest

from app.adapters.rerank_process import (
    ProcessRerankTimeout,
    ProcessRerankUnavailable,
    RerankProcessWorker,
)
from app.application.ports import Evidence

def _ev(n: int) -> list:
    return [Evidence(content=f"본문{i}", source="s", locator=str(i), score=0.0, backend="x")
            for i in range(n)]


def _spec() -> dict:
    """★자식이 만들 클래스를 **경로로** 준다 — spawn 은 monkeypatch 를 안 물려받는다."""
    return {"model": "fake", "device": "cpu", "batch_size": 1,
            "max_length": 128, "dtype": "float32", "trust_remote_code": False,
            "reranker_class": "tests._fake_reranker:FakeReranker"}


def test_시한을_넘기면_자식을_죽인다(monkeypatch):
    """★★여기가 스레드 워커와 갈리는 지점 — 계산이 **실제로** 멈춘다."""
    monkeypatch.setenv("FAKE_RERANK_DELAY", "20")
    w = RerankProcessWorker(_spec(), start_timeout=120)
    try:
        w.start()
        assert w.stats()["loaded"] is True
        proc = w._proc
        pid = proc.pid

        t0 = time.perf_counter()
        with pytest.raises(ProcessRerankTimeout, match="종료했습니다"):
            w.rerank("질의", _ev(2), top_n=2, timeout=2.0)
        elapsed = time.perf_counter() - t0

        #: 시한 근처에서 끝나야 한다 — 20초를 다 기다리면 안 죽인 것이다.
        assert elapsed < 8, f"시한이 지나도 안 끊었다({elapsed:.1f}초)"
        s = w.stats()
        assert s["timeouts"] == 1 and s["kills"] >= 1
        assert s["alive"] is False, "자식이 죽어 있어야 한다"
        #: ★스레드 워커와 달리 **버려진 일감이 남지 않는다.**
        assert s["busy_with_abandoned"] is False
        assert s["abandoned_in_flight"] == 0

        #: 죽은 프로세스가 정말 사라졌는지 — `multiprocessing` 이 직접 답한다.
        #:   ★`os.kill(pid, 0)` 로 확인하려다 실패했다. Windows 에서 그 호출은
        #:     죽은 PID 에도 예외를 안 낼 수 있고, PID 재사용도 있어 **이식성이 없다.**
        assert w._proc is None, "부모가 자식 참조를 놓아야 한다"
        assert not proc.is_alive(), f"자식(pid {pid})이 아직 살아 있다"
        assert proc.exitcode is not None, "종료 코드가 정해져 있어야 한다"
    finally:
        w.stop()


def test_죽인_뒤에도_다음_요청이_된다(monkeypatch):
    """죽였으면 다시 띄운다 — 한 번 시한 초과로 영구 불능이 되면 안 된다."""
    monkeypatch.setenv("FAKE_RERANK_DELAY", "0")
    w = RerankProcessWorker(_spec(), start_timeout=120)
    try:
        w.start()
        out = w.rerank("질의", _ev(3), top_n=2, timeout=30.0)
        assert [e.locator for e in out] == ["2", "1"]
        first_starts = w.stats()["starts"]

        w._kill_locked()                      # 죽었다고 가정
        out2 = w.rerank("질의2", _ev(2), top_n=1, timeout=60.0)
        assert len(out2) == 1
        assert w.stats()["starts"] > first_starts, "다시 띄웠어야 한다"
    finally:
        w.stop()


def test_적재_실패는_ProcessRerankUnavailable(monkeypatch):
    monkeypatch.setenv("FAKE_RERANK_LOAD_FAIL", "1")
    w = RerankProcessWorker(_spec(), start_timeout=120)
    try:
        with pytest.raises(ProcessRerankUnavailable, match="no CUDA"):
            w.start()
        assert "no CUDA" in w.stats()["load_error"]
        assert w.stats()["alive"] is False
    finally:
        w.stop()


def test_진입점이_import_안전하지_않으면_계약대로_알린다(monkeypatch):
    """★spawn 은 자식에서 `__main__` 을 다시 임포트한다.

    진입 스크립트가 `if __name__ == "__main__":` 가드 없이 실행 코드를 두면
    파이썬이 자식 기동을 막는다(안 막으면 서버가 재귀로 뜬다).

    그때 **날 것의 multiprocessing 역추적이 새어 나가면 안 된다** — 실제로 그랬다.
    호출부는 `ProcessRerankUnavailable` 만 잡으므로, 그 밖의 예외는 요청 경로를
    엉뚱한 오류로 물들인다. 무엇을 고쳐야 하는지 말해 주는 예외로 바꾼다.
    """
    w = RerankProcessWorker(_spec(), start_timeout=30)

    class _Boom:
        def __init__(self, *a, **k): pass
        def start(self):
            raise RuntimeError("An attempt has been made to start a new process before "
                               "the current process has finished its bootstrapping phase.")
        def is_alive(self): return False

    monkeypatch.setattr(w._ctx, "Process", _Boom)
    with pytest.raises(ProcessRerankUnavailable) as ei:
        w.start()
    msg = str(ei.value)
    assert "__main__" in msg, "무엇을 고쳐야 하는지 말해야 한다"
    assert "thread" in msg, "빠져나갈 길(thread 워커)도 알려 준다"
    assert w.stats()["alive"] is False and w.stats()["load_error"]


def test_적재중_자식이_죽으면_시한을_안_기다리고_말한다(monkeypatch):
    """★★자식이 세그폴트로 죽으면 **큐에 아무것도 안 들어온다.**

    전에는 `results.get(timeout=start_timeout)` 한 번으로 기다려서, 부모가
    시한(기본 600초)을 꽉 채우고서야 「준비되지 않았습니다」라고 말했다.
    실측 2026-08-25: RAM 이 모자라 자식이 죽었는데 부모가 10분을 그냥 기다렸고
    밖에서는 **아무 일도 안 일어나는 것처럼** 보였다.

    지금은 살아 있는지 확인하며 기다린다 — 죽었으면 즉시, 원인 추측까지 붙여 말한다.
    """
    monkeypatch.setenv("FAKE_RERANK_LOAD_CRASH", "1")
    w = RerankProcessWorker(_spec(), start_timeout=60)   # ← 이만큼 기다리면 실패다
    try:
        t0 = time.perf_counter()
        with pytest.raises(ProcessRerankUnavailable, match="적재 중 죽었습니다"):
            w.start()
        elapsed = time.perf_counter() - t0
        assert elapsed < 25, f"죽은 자식을 {elapsed:.0f}초나 기다렸다"
        err = w.stats()["load_error"]
        assert "종료코드 139" in err, err
        assert "OOM" in err, "원인 추측이 없으면 다음 사람이 또 헤맨다"
    finally:
        w.stop()


def test_stats_가_스레드워커와_같은_모양이다():
    """메트릭이 두 방식을 같은 이름으로 읽는다 — 모양이 다르면 그래프가 깨진다."""
    w = RerankProcessWorker(_spec())
    s = w.stats()
    for k in ("alive", "loaded", "load_seconds", "load_error", "submitted",
              "completed", "failed", "timeouts", "rejected_busy",
              "abandoned_in_flight", "busy_with_abandoned", "queue_depth"):
        assert k in s, f"메트릭이 읽는 키가 없다: {k}"
    assert s["mode"] == "process"


def test_설정이_방식을_고른다(monkeypatch):
    from app.core.config import Settings

    assert Settings().CLAUSE_RERANK_WORKER == "thread", "기본은 thread — 죽이는 것은 싸지 않다"
    monkeypatch.setenv("CLAUSE_RERANK_WORKER", "process")
    assert Settings().CLAUSE_RERANK_WORKER == "process"
    monkeypatch.setenv("CLAUSE_RERANK_WORKER", "nonsense")
    with pytest.raises(Exception):
        Settings()
