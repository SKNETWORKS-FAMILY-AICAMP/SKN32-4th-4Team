"""리랭크 워커 — **취소하는 척하지 않는다.**

★여기서 고정하는 것
  · 무게추는 **워커 스레드에서** 한 번만 적재된다(요청 경로가 아니다)
  · 적재 실패는 상태에 남고 `RerankUnavailable` 로 올라온다
  · 시한을 넘기면 `RerankTimeout` — 그리고 **추론은 계속 돈다**
  · 버려진 일감이 도는 동안 새 일감을 **거절**한다(`RerankBusy`)
  · 대기열이 차면 거절한다 — 쌓아 두면 밖에서 안 보인다
  · `stats()` 가 그 전부를 말한다

모델을 내려받지 않는다. 가짜 채점기를 주입한다.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.adapters import rerank_worker as rw
from app.application.ports import Evidence


def _ev(n: int) -> list:
    return [Evidence(content=f"본문{i}", source="s", locator=str(i), score=0.0, backend="x")
            for i in range(n)]


class _Fake:
    """지정한 시간만큼 걸리는 가짜 리랭커.

    ★`fail_after` 는 **예열을 통과시키고** 그 뒤 일감부터 실패시킨다.
      처음엔 `fail=True` 로 항상 실패하게 했더니 예열에서 먼저 죽어
      적재 실패가 됐고, 「일감 실패」를 재는 시험이 아니게 됐다.
    """

    def __init__(self, delay: float = 0.0, fail_after: int | None = None):
        self.delay, self.fail_after = delay, fail_after
        self.calls = 0

    def rerank(self, query, evidence, top_n=None):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("constant scores")
        return list(reversed(evidence))[:top_n] if top_n else list(reversed(evidence))


@pytest.fixture(autouse=True)
def _clean():
    rw.reset_worker()
    yield
    rw.reset_worker()


def _started(fake, **kw) -> rw.RerankWorker:
    w = rw.RerankWorker(lambda: fake, **kw)
    w.start()
    for _ in range(200):                      # 적재·예열을 기다린다
        if w.stats()["loaded"] or w.stats()["load_error"]:
            break
        time.sleep(0.01)
    return w


# ── 적재 ────────────────────────────────────────────────────────────

def test_무게추는_워커_스레드에서_한_번만_적재된다():
    """★요청 경로에서 4B 를 올리면 첫 요청이 수십 초를 문다."""
    built = []

    def build():
        built.append(threading.current_thread().name)
        return _Fake()

    w = rw.RerankWorker(build)
    assert built == [], "start() 전에는 적재하지 않는다"
    w.start()
    for _ in range(200):
        if w.stats()["loaded"]:
            break
        time.sleep(0.01)
    try:
        assert len(built) == 1
        assert built[0] == "rerank-worker", "요청 스레드가 아니라 워커 스레드에서 적재한다"
        assert w.stats()["loaded"] is True
        assert w.stats()["load_seconds"] is not None
    finally:
        w.stop()


def test_예열까지_적재_단계에서_끝낸다():
    """첫 실제 질의가 적재 시간을 물면 「리랭킹이 40초」로 잘못 읽힌다."""
    fake = _Fake()
    w = _started(fake)
    try:
        assert fake.calls == 1, "예열 1회가 돌아 있어야 한다"
    finally:
        w.stop()


def test_적재_실패는_상태에_남고_올라온다():
    def boom():
        raise RuntimeError("no CUDA")

    w = rw.RerankWorker(boom)
    w.start()
    for _ in range(200):
        if w.stats()["load_error"]:
            break
        time.sleep(0.01)
    try:
        assert "no CUDA" in w.stats()["load_error"]
        with pytest.raises(rw.RerankUnavailable, match="no CUDA"):
            w.rerank("질의", _ev(2), top_n=2, timeout=1.0)
    finally:
        w.stop()


# ── 정상 ────────────────────────────────────────────────────────────

def test_일감을_넣으면_재정렬해_돌려준다():
    w = _started(_Fake())
    try:
        out = w.rerank("질의", _ev(3), top_n=2, timeout=5.0)
        assert [e.locator for e in out] == ["2", "1"]
        s = w.stats()
        assert s["completed"] == 1 and s["timeouts"] == 0
        assert s["latency_ms"]["n"] == 1
    finally:
        w.stop()


def test_채점_실패는_그대로_올라온다():
    """★조용히 원래 순서로 되돌리지 않는다."""
    #: 예열(1회)은 통과시키고 그다음 일감부터 실패시킨다.
    w = _started(_Fake(fail_after=1))
    try:
        assert w.stats()["loaded"] is True, "예열은 통과해야 적재 실패와 구분된다"
        with pytest.raises(RuntimeError, match="constant scores"):
            w.rerank("질의", _ev(2), top_n=2, timeout=5.0)
        assert w.stats()["failed"] == 1
        assert w.stats()["load_error"] is None, "일감 실패를 적재 실패로 세면 안 된다"
    finally:
        w.stop()


# ── 시한 · 바쁨 ─────────────────────────────────────────────────────

def test_시한을_넘기면_RerankTimeout_이고_추론은_계속_돈다():
    """★★취소하는 척하지 않는다.

    파이썬 스레드는 강제 종료가 없고 torch 추론은 중간에 끊기지 않는다.
    시한은 「이 요청을 포기한다」이지 「계산을 멈춘다」가 아니다.
    """
    fake = _Fake(delay=0.6)
    w = _started(fake)
    try:
        with pytest.raises(rw.RerankTimeout):
            w.rerank("질의", _ev(2), top_n=2, timeout=0.15)
        s = w.stats()
        assert s["timeouts"] == 1
        assert s["busy_with_abandoned"] is True, "버려진 일감이 아직 돈다고 말해야 한다"

        #: 도는 동안은 새 일감을 받지 않는다 — 받아 봐야 쌓이기만 한다.
        with pytest.raises(rw.RerankBusy):
            w.rerank("질의2", _ev(2), top_n=2, timeout=5.0)
        assert w.stats()["rejected_busy"] == 1

        #: 계산이 끝나면 결과는 버려지고, 버려졌다는 사실만 센다.
        for _ in range(200):
            if not w.stats()["busy_with_abandoned"]:
                break
            time.sleep(0.01)
        s = w.stats()
        assert s["abandoned_in_flight"] == 1
        assert s["completed"] == 0, "버려진 일감을 완료로 세면 지표가 거짓이 된다"

        #: 풀린 뒤에는 다시 받는다.
        out = w.rerank("질의3", _ev(2), top_n=1, timeout=5.0)
        assert len(out) == 1
    finally:
        w.stop()


def test_대기열이_차면_거절한다():
    """쌓아 두면 밖에서 안 보인다 — 그럴 바엔 거절하고 센다."""
    fake = _Fake(delay=0.4)
    w = _started(fake, queue_size=1)
    try:
        done = []

        def slow():
            try:
                w.rerank("느린질의", _ev(2), top_n=2, timeout=5.0)
                done.append(True)
            except Exception:  # noqa: BLE001
                done.append(False)

        t = threading.Thread(target=slow)
        t.start()
        time.sleep(0.05)                       # 첫 일감이 워커에 잡히도록
        # 큐(1칸)를 채우고, 그 다음 것을 거절시킨다
        fillers = [threading.Thread(target=lambda: _swallow(w)) for _ in range(3)]
        for f in fillers:
            f.start()
        for f in fillers:
            f.join(timeout=5)
        t.join(timeout=5)
        assert w.stats()["rejected_busy"] >= 1
    finally:
        w.stop()


def _swallow(w):
    try:
        w.rerank("채움", _ev(2), top_n=2, timeout=2.0)
    except Exception:  # noqa: BLE001
        pass


# ── 전역 워커 ───────────────────────────────────────────────────────

def test_전역_워커는_하나만_만들어진다():
    """★두 벌 올라가면 GPU 가 터진다."""
    built = []

    def build():
        built.append(1)
        return _Fake()

    a = rw.get_worker(build)
    b = rw.get_worker(build)
    assert a is b
    for _ in range(200):
        if a.stats()["loaded"]:
            break
        time.sleep(0.01)
    assert len(built) == 1


def test_peek_는_워커를_만들지_않는다():
    """준비 상태를 물었을 뿐인데 무게추가 올라가면 안 된다."""
    assert rw.peek_worker() is None
    rw.get_worker(lambda: _Fake())
    assert rw.peek_worker() is not None
