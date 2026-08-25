"""Shared PostgreSQL connection leases.

The application adapters historically opened one physical connection for every
operation.  This module keeps their existing ``with connection`` shape while
returning connections to a process-local psycopg pool.  The direct-connection
fallback is intentionally only for developer/test environments where the
optional pool extra has not yet been installed; production dependencies include
it and release checks report the configured runtime separately.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from threading import RLock
from typing import Any

import psycopg


_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 10
_pools: dict[tuple[str, int, int], Any] = {}
_lock = RLock()


def _pool_for(dsn: str, *, connect_timeout: int, max_size: int) -> Any | None:
    try:
        from psycopg_pool import ConnectionPool
    except ImportError:
        return None

    key = (dsn, connect_timeout, max_size)
    with _lock:
        pool = _pools.get(key)
        if pool is None:
            pool = ConnectionPool(
                conninfo=dsn,
                min_size=_POOL_MIN_SIZE,
                max_size=max_size,
                timeout=connect_timeout,
                kwargs={"connect_timeout": connect_timeout},
                open=True,
            )
            _pools[key] = pool
        return pool


class ConnectionLease(AbstractContextManager):
    """A connection-shaped lease whose ``close`` returns it to the pool."""

    def __init__(self, dsn: str, *, connect_timeout: int = 5, max_size: int = _POOL_MAX_SIZE):
        self._pool = _pool_for(
            dsn, connect_timeout=connect_timeout, max_size=max_size
        )
        self._manager: Any | None = None
        self._connection: Any | None = None
        self._closed = False
        try:
            if self._pool is None:
                self._connection = psycopg.connect(
                    dsn, connect_timeout=connect_timeout
                )
            else:
                self._manager = self._pool.connection()
                self._connection = self._manager.__enter__()
            # Tiny fake connections used by adapter unit tests need only the
            # connection/close contract; real psycopg connections always have
            # execute and receive the session safety limits.
            if hasattr(self._connection, "execute"):
                self._connection.execute("SET statement_timeout = '10s'")
                self._connection.execute("SET lock_timeout = '3s'")
        except Exception:
            self.close(error=True)
            raise

    @property
    def connection(self) -> Any:
        if self._connection is None:
            raise RuntimeError("PostgreSQL connection lease is not open")
        return self._connection

    def __enter__(self) -> Any:
        return self.connection

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.close(error=exc_type is not None, exc_info=(exc_type, exc_value, traceback))
        return False

    def close(
        self,
        *,
        error: bool = False,
        exc_info: tuple[Any, Any, Any] | None = None,
    ) -> None:
        if self._closed:
            return
        self._closed = True
        if self._manager is not None:
            info = exc_info if exc_info is not None else ((Exception, Exception(), None) if error else (None, None, None))
            self._manager.__exit__(*info)
            return
        if self._connection is not None and hasattr(self._connection, "close"):
            self._connection.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)


def connection(
    dsn: str, *, connect_timeout: int = 5, max_size: int = _POOL_MAX_SIZE
) -> Any:
    """Return a pooled connection lease with the existing adapter contract."""

    if not (dsn or "").strip():
        raise RuntimeError("PostgreSQL DSN is not configured")
    if _pool_for(dsn, connect_timeout=connect_timeout, max_size=max_size) is None:
        conn = psycopg.connect(dsn, connect_timeout=connect_timeout)
        if hasattr(conn, "execute"):
            conn.execute("SET statement_timeout = '10s'")
            conn.execute("SET lock_timeout = '3s'")
        return conn
    return ConnectionLease(
        dsn, connect_timeout=connect_timeout, max_size=max_size
    )


def pool_status() -> dict[str, Any]:
    """Return non-secret pool state for diagnostics and release reports."""

    with _lock:
        return {
            "pool_dependency": _pool_dependency_available(),
            "pool_count": len(_pools),
            "max_size": _POOL_MAX_SIZE,
        }


def _pool_dependency_available() -> bool:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        return False
    return True


def close_all() -> None:
    """Close all process-local pools during application shutdown."""

    with _lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        pool.close()


__all__ = ["ConnectionLease", "close_all", "connection", "pool_status"]
