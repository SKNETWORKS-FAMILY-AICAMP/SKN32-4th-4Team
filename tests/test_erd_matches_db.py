"""ERD 문서(`docs/submission/05A_DB_스키마.md`)가 **실제 DB 와 맞나.**

★2026-08-26 에 어긋난 것이 드러나서 만든다.

    스키마를 고쳐 놓고 ERD 를 안 고치면, 다음 사람은 문서를 믿고 짠다.
    실제로 그날 세 군데가 어긋나 있었다 —
      · 외래키를 둘 세웠는데 DDL 에는 **하나도 없었다**
      · `index_generation` 기본값이 **은퇴한 세대**를 가리켰다
      · 행수가 정리·적재·은퇴 전 값이었다

★`pg` 마커다. DB 가 없으면 건너뛴다 — 그때는 **문서만 보고 통과시키지 않는다.**
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.pg

_DOC = pathlib.Path(__file__).resolve().parents[1] / "docs" / "submission" / "05A_DB_스키마.md"


def _conn_or_skip():
    from db.postgres.pgvector_index import get_conn

    try:
        return get_conn()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG 없음 — 건너뜀: {str(exc)[:80]}")


def test_문서에_적힌_외래키가_실제로_있다():
    """★문서가 DDL 로 적은 제약은 **실제로 서 있어야** 한다."""
    doc = _DOC.read_text(encoding="utf-8")
    named = set(re.findall(r"ADD CONSTRAINT (policy_clause_\w+_fk)", doc))
    assert named, "문서에 외래키 DDL 이 없다 — 실제로는 서 있는데 안 적은 것 아닌가"

    conn = _conn_or_skip()
    try:
        with conn.cursor() as cur:
            #: ★★스키마를 **설정에서 읽는다** (2026-08-26, 코덱스 M4 감사가 잡음).
            #:   앞서는 `connamespace='public'` 으로 굳어 있었다. 그래서
            #:   `PGVECTOR_SCHEMA=vec` 로 전환하면 **실제로 서 있는 외래키를 못 보고**
            #:   「문서에만 있다」고 실패했다 — 시험이 이관을 막는 셈이었다.
            #:   ★이 시험의 목적은 「문서와 실제가 맞나」이지 「public 에 있나」가 아니다.
            from app.core.config import get_settings

            schema = (get_settings().PGVECTOR_SCHEMA or "public").strip()
            cur.execute("SELECT conname, confdeltype FROM pg_constraint "
                        "WHERE contype='f' AND connamespace = %s::regnamespace", (schema,))
            actual = dict(cur.fetchall())
    finally:
        conn.close()

    assert named <= set(actual), f"문서에만 있는 외래키: {named - set(actual)}"
    assert set(actual) <= named, (
        f"실제로는 있는데 문서에 없는 외래키: {set(actual) - named} — ERD 를 고쳐야 한다"
    )
    for name in named:
        #: ★`CASCADE` 면 본문 한 줄을 지울 때 벡터가 조용히 함께 사라진다.
        assert actual[name] == "r", f"{name} 의 ON DELETE 가 RESTRICT 가 아니다"


def test_은퇴한_세대가_기본값으로_남아_있으면_문서가_경고한다():
    """★`DEFAULT 's5-mixed'` 가 0행 세대를 가리키면 직접 INSERT 한 행이 조용히 사라진다.

    기본값을 아직 안 고쳤다면 **문서가 그 사실을 말하고 있어야** 한다.
    """
    conn = _conn_or_skip()
    try:
        with conn.cursor() as cur:
            from app.core.config import get_settings

            schema = (get_settings().PGVECTOR_SCHEMA or "public").strip()
            cur.execute("SELECT column_default FROM information_schema.columns "
                        "WHERE table_schema=%s AND table_name='policy_clause_occurrence' "
                        "  AND column_name='index_generation'", (schema,))
            row = cur.fetchone()
            default = (row[0] or "") if row else ""
            m = re.search(r"'([^']+)'", default)
            gen = m.group(1) if m else ""
            cur.execute(f"SELECT count(*) FROM {schema}.policy_clause_occurrence "
                        "WHERE index_generation = %s", (gen,))
            rows_in_default_gen = cur.fetchone()[0]
    finally:
        conn.close()

    if gen and rows_in_default_gen == 0:
        doc = _DOC.read_text(encoding="utf-8")
        assert "기본값이 **죽은 값을 가리킨다**" in doc or "기본값 주의" in doc, (
            f"index_generation 기본값 '{gen}' 이 0행 세대인데 문서가 경고하지 않는다"
        )
