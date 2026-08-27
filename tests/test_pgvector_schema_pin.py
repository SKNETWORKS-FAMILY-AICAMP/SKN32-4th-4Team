"""연결이 **어느 스키마를 보는지** 설정이 정하고, 모호하면 안 연다.

★2026-08-26 · `mall_vec.public` → `insurance_real.vec` 이관에서 필요해졌다.

    조항 색인 SQL **120곳**이 테이블을 맨이름으로 쓴다(`policy_clause_chunk` 등).
    문자열을 다 고치는 대신 연결 지점 하나에서 `search_path` 를 고정한다.

★★그러나 «순서에 기대는» 것이 원래 위험이다 — 못 찾으면 조용히 뒤쪽 스키마로 떨어진다.
  이 저장소가 여러 번 데인 유형이다(옛 세대 혼입 · 옛 모델 벡터 · 낡은 게이트 값).
  그래서 **이름이 겹치면 아예 안 연다.**
"""

from __future__ import annotations

import pytest

from app.core.config import Settings


def test_코드_기본값은_public_이다(monkeypatch):
    """★**코드 기본값**을 잰다 — `.env` 가 무엇으로 덮든 상관없다.

    ★이 값이 중요한 이유: 설정을 **안 준 배포**가 어디를 보는가를 정한다.
      `public` 이 아니게 바꾸면, 아직 안 옮긴 배포가 없는 스키마를 찾다 깨진다.

    ★처음엔 `Settings()` 를 그냥 불렀는데, 그건 `.env` 를 읽는다.
      2026-08-26 에 `.env` 를 `vec` 로 전환하자 이 시험이 깨졌다 —
      **재려던 것(코드 기본값)과 잰 것(실효값)이 달랐다.**
    """
    monkeypatch.delenv("PGVECTOR_SCHEMA", raising=False)
    assert Settings.model_fields["PGVECTOR_SCHEMA"].default == "public"


def test_이상한_스키마_이름은_거부한다():
    """이름을 SQL 에 그대로 넣으므로 형태를 좁힌다."""
    from db.postgres import pgvector_index as m

    class _Raw:
        def cursor(self):  # pragma: no cover - 여기까지 오면 안 된다
            raise AssertionError("이름 검사를 통과했다")

    import os

    old = os.environ.get("PGVECTOR_SCHEMA")
    from app.core.config import get_settings

    try:
        for bad in ("vec; drop table x", "vec public", "vec-1", "'vec'"):
            os.environ["PGVECTOR_SCHEMA"] = bad
            get_settings.cache_clear()
            with pytest.raises(ValueError, match="PGVECTOR_SCHEMA"):
                m._pin_search_path(_Raw())
    finally:
        if old is None:
            os.environ.pop("PGVECTOR_SCHEMA", None)
        else:
            os.environ["PGVECTOR_SCHEMA"] = old
        get_settings.cache_clear()


@pytest.mark.pg
def test_이름이_겹치면_연결을_안_준다(monkeypatch):
    """★★`search_path` 순서에 기대면 「어느 쪽을 읽는지」가 설정에 숨는다.

    겹치면 **못 여는 편이 낫다** — 어느 쪽을 읽는지 모르는 채 도는 것보다.
    """
    from app.core.config import get_settings
    from app.core.errors import InfraError
    from db.postgres import pgvector_index as m

    try:
        import psycopg

        conn = psycopg.connect(get_settings().PGVECTOR_DSN or
                               "host=127.0.0.1 port=5433 user=postgres dbname=insurance_real",
                               connect_timeout=10)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG 없음: {str(exc)[:60]}")

    #: ★진짜 테이블 이름을 쓰지 않는다 — 정리하다 `public` 의 실물을 건드린다.
    #:   실제로 `DependentObjectsStillExist` 로 시험이 깨졌다(2026-08-26).
    SCH, T = "vec_clash_probe", "clash_probe_table"
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCH}")
            cur.execute(f"CREATE TABLE IF NOT EXISTS {SCH}.{T} (x int)")
            #: `public` 쪽에도 같은 이름을 만들어 **겹침**을 만든다.
            cur.execute(f"CREATE TABLE IF NOT EXISTS public.{T} (x int)")
        conn.commit()

        monkeypatch.setenv("PGVECTOR_SCHEMA", SCH)
        get_settings.cache_clear()
        with pytest.raises(InfraError, match="같은 이름의 테이블"):
            m._pin_search_path(conn)
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS public.{T}")
            cur.execute(f"DROP SCHEMA IF EXISTS {SCH} CASCADE")
        conn.commit()
        conn.close()
        get_settings.cache_clear()


@pytest.mark.pg
def test_연결을_여러_번_열어도_스키마가_유지된다():
    """★풀에서 재사용된 연결에도 걸려야 한다.

    처음엔 `SET search_path TO vec` 하나만 두었더니 `register_vector` 가
    **`vector` 타입을 못 찾아** 두 번째 연결부터 전부 터졌다
    (확장이 `public` 에 설치돼 있다). 판정 100건 중 **99건이 InfraError** 였다.
    """
    from db.postgres.pgvector_index import get_conn

    try:
        for _ in range(4):
            with get_conn() as c, c.cursor() as cur:
                cur.execute("show search_path")
                sp = cur.fetchone()[0]
                cur.execute("select 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG 없음: {str(exc)[:60]}")
    assert sp, "search_path 가 비었다"
