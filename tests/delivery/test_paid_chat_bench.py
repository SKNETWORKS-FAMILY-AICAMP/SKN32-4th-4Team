"""Paid chat driver safety tests.  No socket or application provider is used."""

from __future__ import annotations

import argparse
import asyncio
import json

import pytest

pytestmark = pytest.mark.delivery


class Response:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {
            "llm": {"used": True, "provider": "fake", "model": "fake-v1"},
            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        }

    def json(self):
        return self._body


class Client:
    def __init__(self, responses, observed):
        self.responses = iter(responses)
        self.observed = observed

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, url, json):
        self.observed.append((url, json))
        return next(self.responses, Response())


def args(tmp_path, **changes):
    values = dict(allow_paid_llm=True, base_url="http://fake.local", message="not persisted",
                  mode="load", stage_call_cap=1, total_call_cap=4, max_seconds=60.0,
                  timeout=1.0, out=str(tmp_path / "result.json"))
    values.update(changes)
    return argparse.Namespace(**values)


def execute(ns, responses=()):
    observed = []
    result = asyncio.run(__import__("delivery.bench.paid_chat", fromlist=["run"]).run(
        ns, client_factory=lambda: Client(responses, observed)))
    return result, observed


def test_explicit_consent_is_required_before_client_creation(tmp_path):
    import delivery.bench.paid_chat as paid

    created = False
    def factory():
        nonlocal created
        created = True
        return Client([], [])

    with pytest.raises(PermissionError, match="allow-paid-llm"):
        asyncio.run(paid.run(args(tmp_path, allow_paid_llm=False), client_factory=factory))
    assert created is False


def test_exact_hard_cap_and_1_2_4_8_ramp_with_summaries(tmp_path):
    result, observed = execute(args(tmp_path))

    assert len(observed) == 4
    assert [stage["concurrency"] for stage in result["stages"]] == [1, 2, 4, 8]
    assert result["summary"]["latency_ms"].keys() == {"p50", "p95", "p99"}
    assert result["summary"]["status_counts"] == {"200": 4}
    assert result["summary"]["providers"] == {"fake": 4}
    assert result["summary"]["models"] == {"fake-v1": 4}
    assert result["summary"]["tokens"] == {"input": 8, "output": 12, "total": 20}
    saved = json.loads((tmp_path / "result.json").read_text())
    assert saved == result
    assert "not persisted" not in (tmp_path / "result.json").read_text()


def test_429_stops_immediately_and_preserves_increment(tmp_path):
    result, observed = execute(args(tmp_path, stage_call_cap=20, total_call_cap=20), [Response(429)])

    assert len(observed) == 1
    assert result["aborted_reason"] == "HTTP 429 received"
    assert json.loads((tmp_path / "result.json").read_text())["records"][0]["status"] == 429


def test_first_error_exceeds_five_percent_and_stops(tmp_path):
    result, observed = execute(args(tmp_path, stage_call_cap=20, total_call_cap=20), [Response(503)])

    assert len(observed) == 1
    assert "5%" in result["aborted_reason"]


def test_semantic_evaluator_failure_stops_immediately(tmp_path):
    bad = Response(body={"llm": {"provider": "fake", "model": "fake-v1"},
                         "semantic_safety": {"passed": False, "reason": "unsupported amount"}})
    result, observed = execute(args(tmp_path, stage_call_cap=20, total_call_cap=20), [bad])

    assert len(observed) == 1
    assert "unsupported amount" in result["aborted_reason"]


def test_budget_caps_rejected_without_creating_client(tmp_path):
    import delivery.bench.paid_chat as paid

    for ns in (args(tmp_path, total_call_cap=81), args(tmp_path, mode="quality", total_call_cap=37),
               args(tmp_path, stage_call_cap=21)):
        with pytest.raises(ValueError):
            asyncio.run(paid.run(ns, client_factory=lambda: pytest.fail("client created")))


def test_elapsed_cap_prevents_first_call_and_persists_reason(tmp_path):
    import delivery.bench.paid_chat as paid

    ticks = iter((0.0, 61.0))
    result = asyncio.run(paid.run(args(tmp_path, max_seconds=60),
                                  client_factory=lambda: Client([], []), clock=lambda: next(ticks)))
    assert result["records"] == []
    assert result["aborted_reason"] == "maximum elapsed time reached"
