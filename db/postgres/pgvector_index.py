"""보험 PostgreSQL/pgvector 공용 연결 helper.

구형 커머스 ``rag_chunks`` 스키마와 코퍼스 적재 함수는 격리했다. 보험 조항 스키마·적재는
``db.postgres.pgvector_clause_index``와 ``scripts.index.build_clause_index``가 담당한다.
"""

from __future__ import annotations

from app.core.config import get_settings


def get_conn(dsn: str | None = None):
    """psycopg 연결 + pgvector 타입 등록. 연결 실패는 InfraError로 전파."""
    from pgvector.psycopg import register_vector
    from db.postgres.pool import ConnectionLease, connection

    from app.core.errors import InfraError

    dsn = dsn or get_settings().PGVECTOR_DSN
    conn = None
    try:
        conn = connection(dsn)
        # pgvector performs a strict ``Connection`` type check.  Register on
        # the raw checked-out connection while returning the lease so callers
        # still return it to the shared pool on ``close``/context exit.
        raw = conn.connection if isinstance(conn, ConnectionLease) else conn
        register_vector(raw)
        return conn
    except Exception as exc:  # noqa: BLE001 - connection setup is one public boundary
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - preserve the sanitized setup failure
                pass
        raise InfraError(
            "pgvector(PostgreSQL)에 연결할 수 없습니다. "
            "연결 설정과 서버 상태를 확인하세요."
        ) from exc
