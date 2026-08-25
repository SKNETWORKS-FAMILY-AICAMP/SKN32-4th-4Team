"""메트릭 노출 — **긁는 요청은 싸야 하고, 모르는 것을 0 으로 내지 않는다.**

★여기서 고정하는 것
  · Prometheus 텍스트 형식이 문법에 맞는다(HELP/TYPE 가 이름마다 한 번)
  · 라벨 값이 이스케이프된다
  · **없는 값은 줄을 내지 않는다** — 0 으로 내면 「0 건」과 「모른다」가 섞인다
  · 스크레이프가 **워커를 만들지 않고 DB 를 세지 않는다**
  · 고객 앱에는 경로가 없다
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.obs import metrics as mx


def _parse(text: str) -> dict:
    """이름 → 값 목록. 형식이 깨지면 여기서 걸린다."""
    out: dict[str, list] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{.*\})? (-?[\d.eE+]+)$", line)
        assert m, f"형식이 깨진 줄: {line!r}"
        out.setdefault(m.group(1), []).append((m.group(2) or "", m.group(3)))
    return out


# ── 형식 ────────────────────────────────────────────────────────────

def test_HELP_와_TYPE_이_이름마다_한_번씩만_나온다():
    """★두 번 나오면 Prometheus 가 스크레이프를 통째로 버린다."""
    text = mx.render_metrics()
    helps = re.findall(r"^# HELP (\S+)", text, re.M)
    types = re.findall(r"^# TYPE (\S+)", text, re.M)
    assert len(helps) == len(set(helps)), f"HELP 중복: {helps}"
    assert len(types) == len(set(types)), f"TYPE 중복: {types}"
    assert set(helps) == set(types), "HELP 와 TYPE 이 짝이 맞지 않는다"


def test_모든_줄이_형식에_맞는다():
    _parse(mx.render_metrics())


def test_라벨_값을_이스케이프한다():
    m = mx.Metrics()
    m.add("t", 1, kind="gauge", help_="설명", labels={"k": 'a"b\\c\nd'})
    line = [x for x in m.render().splitlines() if x.startswith("t{")][0]
    assert line == 't{k="a\\"b\\\\c\\nd"} 1'


def test_설명의_줄바꿈도_이스케이프한다():
    m = mx.Metrics()
    m.add("t", 1, kind="gauge", help_="첫 줄\n둘째 줄")
    assert "\\n" in m.render().splitlines()[0]


def test_없는_값은_줄을_내지_않는다():
    """★0 으로 내면 「0 건」과 「모른다」가 섞인다.

    적재 시간을 모르는데 0 으로 내면 그래프가 「즉시 적재」로 보인다.
    """
    m = mx.Metrics()
    m.add("t_known", 3, kind="gauge", help_="있음")
    m.add("t_unknown", None, kind="gauge", help_="없음")
    text = m.render()
    assert "t_known 3" in text
    assert "t_unknown" not in text


def test_참거짓은_1과_0으로_나간다():
    m = mx.Metrics()
    m.add("t_on", True, kind="gauge", help_="x")
    m.add("t_off", False, kind="gauge", help_="x")
    assert "t_on 1" in m.render() and "t_off 0" in m.render()


# ── 내용 ────────────────────────────────────────────────────────────

def test_워커가_없으면_up_이_0이고_워커를_만들지_않는다():
    """★스크레이프가 4B 무게추를 올리면 안 된다."""
    from app.adapters import rerank_worker as rw

    rw.reset_worker()
    got = _parse(mx.render_metrics())
    assert got["clause_rerank_worker_up"][0][1] == "0"
    assert rw.peek_worker() is None, "스크레이프가 워커를 만들었다"


def test_워커가_있으면_상태를_그대로_낸다(monkeypatch):
    from app.adapters import rerank_worker as rw

    class _W:
        def stats(self):
            return {"alive": True, "loaded": True, "load_seconds": 12.5,
                    "submitted": 7, "completed": 5, "failed": 1, "timeouts": 1,
                    "rejected_busy": 2, "abandoned_in_flight": 1,
                    "busy_with_abandoned": True, "queue_depth": 3,
                    "latency_ms": {"p50": 2292.0, "p95": 3100.5, "n": 5}}

        def stop(self, timeout=None):
            return None

    rw.reset_worker()
    rw._WORKER = _W()
    try:
        got = _parse(mx.render_metrics())
        assert got["clause_rerank_worker_loaded"][0][1] == "1"
        assert got["clause_rerank_worker_load_seconds"][0][1] == "12.5"
        assert got["clause_rerank_queue_depth"][0][1] == "3"
        #: ★시한 초과 일감이 도는 상태가 밖에서 보여야 503 의 원인을 안다.
        assert got["clause_rerank_busy_with_abandoned"][0][1] == "1"

        #: 결과는 한 이름에 라벨로 — 이름을 쪼개면 합계를 못 낸다.
        by_result = {re.search(r'result="(\w+)"', lbl).group(1): val
                     for lbl, val in got["clause_rerank_jobs_total"]}
        assert by_result == {"completed": "5", "failed": "1", "timeout": "1",
                             "rejected_busy": "2", "abandoned": "1"}

        by_q = {re.search(r'quantile="([\d.]+)"', lbl).group(1): val
                for lbl, val in got["clause_rerank_latency_ms"]}
        assert by_q == {"0.5": "2292.0", "0.95": "3100.5"}
    finally:
        rw.reset_worker()


def test_지연_표본이_없으면_분위수_줄이_안_나온다():
    """아직 한 건도 안 돌았는데 p50 을 0 으로 내면 「빠르다」로 읽힌다."""
    from app.adapters import rerank_worker as rw

    class _W:
        def stats(self):
            return {"alive": True, "loaded": True, "load_seconds": None,
                    "submitted": 0, "completed": 0, "failed": 0, "timeouts": 0,
                    "rejected_busy": 0, "abandoned_in_flight": 0,
                    "busy_with_abandoned": False, "queue_depth": 0}

        def stop(self, timeout=None):
            return None

    rw.reset_worker()
    rw._WORKER = _W()
    try:
        text = mx.render_metrics()
        assert "clause_rerank_latency_ms" not in text
        assert "clause_rerank_worker_load_seconds" not in text, "모르는 적재시간을 내면 안 된다"
    finally:
        rw.reset_worker()


# ── 노출 경로 ───────────────────────────────────────────────────────

def test_고객앱에는_메트릭_경로가_없다():
    """★운영 지표를 무인증으로 노출하지 않는다."""
    r = TestClient(create_app("customer")).get("/api/admin/metrics")
    assert r.status_code == 404


def test_운영앱에서_관리자_인증으로_받는다():
    from app.auth.roles import require_admin

    app = create_app("admin")
    app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "ADMIN"}
    try:
        r = TestClient(app).get("/api/admin/metrics")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "clause_rerank_enabled" in r.text
    _parse(r.text)


def test_무인증이면_401이다():
    r = TestClient(create_app("admin")).get("/api/admin/metrics")
    assert r.status_code == 401
