"""부하 하네스 안전장치 — 실제 요청 없이 실행 흐름만 고정한다."""

from __future__ import annotations

import argparse

import pytest

pytestmark = pytest.mark.delivery


def _args(**overrides):
    values = {
        "base_url": "http://unused.invalid",
        "requests": 1,
        "concurrency": 8,
        "repeats": 1,
        "warmup": 0,
        "timeout": 1.0,
        "seed": 1,
        "scenarios": "noop_async",
        "no_keepalive": False,
        "include_writes": False,
        "allow_blocking": False,
        "precheck_body": None,
        "ramp": True,
        "abort_error_rate": 0.05,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.fixture
def _no_network(monkeypatch):
    import httpx
    import delivery.bench.load as load
    import delivery.bench.profile as profile

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def no_warmup(client, base, scenarios, n):
        return None

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(load, "_warmup", no_warmup)
    monkeypatch.setattr(
        profile,
        "describe",
        lambda: {"blocking": [], "delivery_mode": "direct"},
    )
    monkeypatch.setattr(profile, "target_base_url", lambda: "http://unused.invalid")


def test_ramp는_각_단계를_따로_기록하고_진행을_즉시_출력한다(
    monkeypatch, _no_network, capsys
):
    import asyncio
    import delivery.bench.load as load

    async def successful_run(client, base, sc, requests, concurrency, repeat):
        result = load.RunResult(sc.name, sc.layer, repeat, concurrency)
        result.latencies_ms = [float(concurrency)]
        result.status_counts = {"200": 1}
        result.wall_seconds = 1.0
        return result

    monkeypatch.setattr(load, "_one_run", successful_run)
    out = asyncio.run(load.run(_args()))

    assert [stage["concurrency"] for stage in out["stages"]] == [1, 2, 4, 8]
    assert all(stage["error_rate"] == 0 for stage in out["stages"])
    assert all(stage["p95_ms"] is not None for stage in out["stages"])
    progress = capsys.readouterr().out
    assert "[진행] 동시성=1" in progress
    assert "[단계 완료] 동시성=8" in progress
    assert "오류율=" in progress and "p50=" in progress


def test_오류율_초과시_즉시_중단하고_사유와_완료결과를_저장한다(
    monkeypatch, _no_network, tmp_path
):
    import asyncio
    import json
    import delivery.bench.load as load

    calls = []

    async def failing_run(client, base, sc, requests, concurrency, repeat):
        calls.append((concurrency, repeat, sc.name))
        result = load.RunResult(sc.name, sc.layer, repeat, concurrency)
        result.latencies_ms = [1.0]
        result.status_counts = {"503": 1}
        result.wall_seconds = 1.0
        return result

    monkeypatch.setattr(load, "_one_run", failing_run)
    out = asyncio.run(load.run(_args(repeats=5)))

    assert calls == [(1, 1, "noop_async")]
    assert len(out["per_run"]) == 1
    assert out["per_run"][0]["error_rate"] == 1.0
    assert "오류율" in out["aborted_reason"]
    assert out["stages"][0]["concurrency"] == 1

    result_path = tmp_path / "aborted.json"
    load._save_result(out, result_path)
    saved = json.loads(result_path.read_text(encoding="utf-8"))
    assert saved["aborted_reason"] == out["aborted_reason"]
    assert saved["per_run"] == out["per_run"]


def test_자동중단_기본값은_5퍼센트다():
    from delivery.bench.load import DEFAULT_ABORT_ERROR_RATE

    assert DEFAULT_ABORT_ERROR_RATE == 0.05
