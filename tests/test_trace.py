"""TEST-OBS-001 — trace_id 미들웨어."""

from __future__ import annotations


def test_response_has_trace_header(client):
    r = client.get("/api/health")
    assert r.headers.get("X-Trace-ID")


def test_incoming_trace_id_is_propagated(client):
    r = client.get("/api/health", headers={"X-Trace-ID": "trace-abc-123"})
    assert r.headers.get("X-Trace-ID") == "trace-abc-123"


def test_each_request_gets_distinct_trace(client):
    a = client.get("/api/health").headers.get("X-Trace-ID")
    b = client.get("/api/health").headers.get("X-Trace-ID")
    assert a and b and a != b
