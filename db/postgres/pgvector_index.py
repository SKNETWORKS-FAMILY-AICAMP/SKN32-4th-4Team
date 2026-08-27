"""보험 PostgreSQL/pgvector 공용 연결 helper.

구형 커머스 ``rag_chunks`` 스키마와 코퍼스 적재 함수는 격리했다. 보험 조항 스키마·적재는
``db.postgres.pgvector_clause_index``와 ``scripts.index.build_clause_index``가 담당한다.
"""

from __future__ import annotations

from app.core import config as _config
from app.core.errors import InfraError


def get_settings():
    """설정을 **부를 때** 찾는다 — import 시점에 묶지 않는다.

    ★★왜 이렇게 하나 (2026-08-26, 기본 CI 순서 의존 실패 2건의 원인)

        전에는 `from app.core.config import get_settings` 로 **모듈 최상단에서 묶었다.**
        그런데 `tests/test_clause_search_route.py` 의 `_admin_app` 이
        `monkeypatch.setattr("app.core.config.get_settings", lambda: _S())` 로 갈아끼운
        **그 사이에 이 모듈이 처음 임포트되면**, 9번 줄이 그 **람다를 영구히** 붙잡는다.
        monkeypatch 가 되돌리는 것은 `app.core.config` 쪽 이름이지 여기 붙은 참조가 아니다.

        결과: 이 모듈의 `get_settings` 는 `cache_clear` 도 없는 람다가 되고,
        `_S` 프록시가 **그때 찍힌 설정 스냅샷**을 계속 돌려준다.
        `test_pgvector.py` 와 `test_pgvector_schema_pin.py` 가 `PGVECTOR_SCHEMA` 를
        바꾸고 `cache_clear()` 를 불러도 **여기엔 안 닿아** 두 시험이 전량 실행에서만 깨졌다.
        (실측: `m.get_settings` 가 `_admin_app.<locals>.<lambda>`, 읽히는 스키마 `'vec'`.)

        ★단독 실행은 통과하고 전량 실행에서만 깨진다 — 파일 단위로만 돌려 보면 안 잡힌다.

    ★부작용을 하나 감수한다 — 이제 `app.core.config.get_settings` 를 패치하면
      이 모듈도 **따라간다.** 그게 패치하는 쪽의 의도이므로 맞는 방향이다.
    """
    return _config.get_settings()


def _pin_search_path(raw) -> None:
    """이 연결이 볼 스키마를 **하나로 고정한다.**

    ★★왜 필요한가 (2026-08-26)

        조항 색인 SQL 120곳이 테이블을 **맨이름**으로 쓴다(`policy_clause_chunk` 등).
        기본 `search_path` 는 `"$user", public` 이라, 스키마를 옮기면 그 전부가
        어디를 볼지 모르는 상태가 된다.

    ★★**`public` 을 붙이되, 겹침을 직접 막는다.**

        처음엔 스키마 하나만 두었다(`SET search_path TO vec`). 그랬더니
        `register_vector` 가 **`vector` 타입을 못 찾아** 두 번째 연결부터 전부 터졌다
        (`vector type not found in the database`) — 확장이 `public` 에 설치돼 있기 때문이다.
        실측 2026-08-26: 판정 100건 중 **99건이 InfraError** 로 실패했다.

        그래서 `public` 을 뒤에 붙인다. 그러면 원래 걱정하던 것이 돌아온다 —
        **못 찾았을 때 조용히 옛 것으로 떨어지는 것.** 이 저장소가 여러 번 데인 유형이다
        (옛 세대 혼입 · 옛 모델 벡터 · 낡은 게이트 값).

        ★그래서 **이름이 겹치는지 직접 본다.** 겹치면 연결을 안 준다 —
          어느 쪽을 읽는지 모르는 채 도는 것보다 못 여는 편이 낫다.

    ★설정(`PGVECTOR_SCHEMA`)이 정한다. 기본은 `public` — 지금 운영이 그렇다.
    """
    schema = (get_settings().PGVECTOR_SCHEMA or "public").strip()
    if not schema.replace("_", "").isalnum():
        #: ★이름을 SQL 에 그대로 넣으므로 형태를 좁힌다.
        raise ValueError(f"PGVECTOR_SCHEMA 가 이상합니다: {schema!r}")
    if schema == "public":
        #: ★기본값이면 **아무것도 하지 않는다.** PostgreSQL 기본 `search_path` 가
        #:   이미 `"$user", public` 이라 바꿀 것이 없고, 연결마다 쿼리를 한 번 더
        #:   보낼 이유도 없다.
        #:   ★부수 효과 하나가 더 있다 — 이 함수가 커서를 안 열므로,
        #:     연결 객체를 가짜로 바꿔 끼우는 시험이 그대로 돈다.
        return
    with raw.cursor() as cur:
        if True:
            #: ★★**같은 이름이 두 스키마에 있으면 못 연다.**
            #:   `search_path` 순서에 기대면 「어느 쪽을 읽는지」가 설정에 숨는다.
            cur.execute(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'public' AND table_name IN ("
                "   SELECT table_name FROM information_schema.tables"
                "    WHERE table_schema = %s)",
                (schema,),
            )
            clash = [r[0] for r in cur.fetchall()]
            if clash:
                raise InfraError(
                    f"'{schema}' 와 'public' 에 같은 이름의 테이블이 있습니다: "
                    f"{sorted(clash)[:5]}. 어느 쪽을 읽는지 확실하지 않아 열지 않습니다."
                )
        #: `SET` 은 이 세션에만 걸린다. 풀에서 재사용돼도 연결마다 다시 건다.
        #: ★`public` 이 뒤에 오는 이유는 **확장(`vector`·`pg_trgm`)이 거기 있기 때문**이다.
        cur.execute(f"SET search_path TO {schema}, public")


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
        _pin_search_path(raw)
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
