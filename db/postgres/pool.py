"""Shared PostgreSQL connection leases.

The application adapters historically opened one physical connection for every
operation.  This module keeps their existing ``with connection`` shape while
returning connections to a process-local psycopg pool.  The direct-connection
fallback is intentionally only for developer/test environments where the
optional pool extra has not yet been installed; production dependencies include
it and release checks report the configured runtime separately.
"""

from __future__ import annotations

import atexit
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
            _register_shutdown()
        return pool


_shutdown_registered = False


def _register_shutdown() -> None:
    """프로세스가 끝날 때 풀을 닫는다.

    ★★왜 (2026-08-26 실측)

        `min_size=1` 이라 풀은 **배경 스레드로 유휴 연결을 채운다.** 그래서
        프로세스가 끝나 DB 를 못 붙는 상황이 되면 그 스레드가

            error connecting in 'pool-2': connection timeout expired

        를 계속 찍는다. 무해하지만 **매 실행 끝에 두 줄씩 붙어** 진짜 오류를 가린다.
        실제로 이 프로젝트의 시험 출력이 그 문구로 덮여 있었다.

    ★조용히 끄지 않는다 — 로그를 죽이는 것이 아니라 **풀을 닫는다.**
      원인을 없애는 쪽이다(연결을 안 만들면 실패할 것도 없다).
    """
    global _shutdown_registered
    if _shutdown_registered:
        return
    import atexit

    def _close_all() -> None:
        with _lock:
            for pool in list(_pools.values()):
                try:
                    pool.close()
                except Exception:  # noqa: BLE001 — 종료 중이다. 더 할 것이 없다.
                    pass
            _pools.clear()

    atexit.register(_close_all)
    _shutdown_registered = True


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
        #: 종료 경로에서 한 풀이 실패해도 나머지는 닫아야 한다.
        #: 여기서 예외가 나가면 atexit 체인이 끊겨 남은 풀의 워커가 그대로 남는다.
        try:
            pool.close()
        except Exception:  # noqa: BLE001 — 종료 중이라 되살릴 것이 없다
            pass


#: ★★**프로세스가 끝날 때 반드시 닫는다.**
#:
#:   `close_all()` 은 오래전부터 있었는데 **아무도 부르지 않았다**(2026-08-26 실측:
#:   저장소 전체 grep 0건). `ConnectionPool(..., open=True)` 는 워커 스레드를 띄우므로,
#:   닫지 않으면 그 스레드들이 `connect() → selectors._select` 안에 있는 채로
#:   인터프리터 종료와 겹친다. Windows 에서 **access violation(exit 139)** 로 죽었다 —
#:   기본 CI 가 54~60% 에서 2회 연속 segfault 했다.
#:
#:   ★시험 하네스가 postgres 로 옮겨 오면서(커밋 `9cd48a3f`) 이 경로를 처음 밟아
#:   드러난 것이지, 그 커밋이 만든 결함이 아니다.
#:
#:   ★`atexit` 로 거는 이유는 `tests/conftest.py` 가 스키마 정리를 atexit 에 건 것과 같다 —
#:   **정상 종료 경로가 아닌 곳(`--collect-only`·import 실패)에서도 한 번은 불린다.**
#:   앱은 shutdown 훅에서 명시적으로 부르는 편이 낫고, 이건 마지막 그물이다.
atexit.register(close_all)


__all__ = ["ConnectionLease", "close_all", "connection", "pool_status"]
