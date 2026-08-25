"""보험 PostgreSQL/pgvector 공용 연결 helper 회귀."""

from __future__ import annotations

import pytest


def test_get_conn_uses_timeout_and_does_not_echo_dsn(monkeypatch):
    import psycopg
    from pgvector import psycopg as pgvector_psycopg

    from app.adapters.pgvector_index import get_conn
    from app.core.errors import InfraError

    captured: dict[str, object] = {}
    sentinel = object()
    monkeypatch.setattr("db.postgres.pool._pool_for", lambda *args, **kwargs: None)

    def connect_ok(dsn, **kwargs):
        captured.update(dsn=dsn, **kwargs)
        return sentinel

    monkeypatch.setattr(psycopg, "connect", connect_ok)
    monkeypatch.setattr(pgvector_psycopg, "register_vector", lambda conn: None)
    dsn = "host=db.internal user=runtime password=top-secret dbname=insurance"

    assert get_conn(dsn) is sentinel
    assert captured["connect_timeout"] == 5

    def connect_fail(_dsn, **_kwargs):
        raise psycopg.OperationalError("connection failed")

    monkeypatch.setattr(psycopg, "connect", connect_fail)
    with pytest.raises(InfraError) as exc_info:
        get_conn(dsn)

    message = str(exc_info.value)
    assert "top-secret" not in message
    assert "db.internal" not in message

    def connect_parse_fail(_dsn, **_kwargs):
        raise psycopg.ProgrammingError("invalid DSN contains top-secret")

    monkeypatch.setattr(psycopg, "connect", connect_parse_fail)
    with pytest.raises(InfraError) as parse_exc:
        get_conn(dsn)
    assert "top-secret" not in str(parse_exc.value)


def test_get_conn_closes_connection_when_vector_registration_fails(monkeypatch):
    import psycopg
    from pgvector import psycopg as pgvector_psycopg

    from app.adapters.pgvector_index import get_conn
    from app.core.errors import InfraError

    class FakeConnection:
        closed = False

        def close(self):
            self.closed = True

    conn = FakeConnection()
    monkeypatch.setattr("db.postgres.pool._pool_for", lambda *args, **kwargs: None)
    monkeypatch.setattr(psycopg, "connect", lambda *_args, **_kwargs: conn)
    monkeypatch.setattr(
        pgvector_psycopg,
        "register_vector",
        lambda _conn: (_ for _ in ()).throw(RuntimeError("top-secret")),
    )

    with pytest.raises(InfraError) as exc_info:
        get_conn("host=db.internal password=top-secret")

    assert conn.closed is True
    assert "top-secret" not in str(exc_info.value)


@pytest.mark.pg
def test_get_conn_failure_is_infra_error():
    from app.adapters.pgvector_index import get_conn
    from app.core.errors import InfraError

    with pytest.raises(InfraError):
        get_conn("host=127.0.0.1 port=9 user=x dbname=nope connect_timeout=2")
