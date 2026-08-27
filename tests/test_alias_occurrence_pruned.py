"""별칭(중복본) 문서의 발생행이 색인에 **남아 있으면 안 된다.**

★2026-08-26 실측으로 잡힌 것

    `config/document_content_aliases.jsonl` 이 「이 문서는 저 문서와 같은 원문」이라고
    선언하면, 적재기(`_collect`)는 그 문서를 **통째로 건너뛴다.**
    그래서 그 문서의 발생행은 **누가 지워 주지 않으면 영원히 남는다** —
    가리킬 청크가 없으니 곧 고아다.

    지우는 것은 `prune_occurrences` 인데, 그건 `_load` 안에서만 불린다.
    이관처럼 `_collect` 만 쓰는 경로로 색인을 만들면 **그 정리가 통째로 빠진다.**
    실제로 DB손해보험 중복본 1건의 발생 **196행**이 남아 있었고,
    그중 부록 6행이 「마지막 고아」였다(45,816 → 6 → 0).

★이 시험은 **결과**를 잰다 — 「정리 함수를 불렀나」가 아니라
  「별칭 문서의 발생행이 남아 있나」다. 어느 경로로 적재했든 결과는 같아야 한다.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.pg


#: ★★**옛 DB 는 재지 않는다** (2026-08-26).
#:
#:   `mall_vec` 은 이관 전 스냅샷이자 **되돌아갈 곳**이다. 거기엔 정리 전 상태가
#:   그대로 있어야 한다(고아 6 · 별칭 발생 196). 그걸 「깨졌다」고 잡으면
#:   **되돌리기용 원본을 손대라는 압력**이 된다 — 그러면 되돌릴 수 없어진다.
#:
#:   이 시험이 재는 것은 **현행 색인**이다. 옛 DB 를 가리키고 있으면 건너뛴다.
_LEGACY_DB = "mall_vec"


def _conn_or_skip():
    from app.core.config import get_settings
    from db.postgres.pgvector_index import get_conn

    dsn = get_settings().PGVECTOR_DSN or ""
    if f"dbname={_LEGACY_DB}" in dsn:
        pytest.skip(
            f"옛 DB({_LEGACY_DB})를 가리키고 있다 — 여기는 이관 전 스냅샷이자 "
            "되돌아갈 곳이라 정리 전 상태가 그대로 있는 것이 맞다. "
            "현행 색인을 재려면 PGVECTOR_DSN/PGVECTOR_SCHEMA 를 새 것으로 둔다."
        )
    try:
        return get_conn()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG 없음 — 건너뜀: {str(exc)[:80]}")


def test_별칭_문서의_발생행이_남아_있지_않다():
    from app.adapters.document_content_aliases import load as load_aliases

    aliases = load_aliases()
    if not aliases:
        pytest.skip("별칭 원장이 비어 있다 — 잴 것이 없다")

    conn = _conn_or_skip()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sha256, count(*) FROM policy_clause_occurrence "
                " WHERE sha256 = ANY(%s) GROUP BY 1 ORDER BY 2 DESC",
                (sorted(aliases),),
            )
            left = cur.fetchall()
    finally:
        conn.close()

    assert not left, (
        "별칭(중복본) 문서의 발생행이 색인에 남아 있다: "
        + ", ".join(f"{s[:12]}={n}행" for s, n in left)
        + " — 적재기는 이 문서를 건너뛰므로 이 행들은 가리킬 청크가 없다(고아)."
    )


def test_고아_발생이_없다():
    """★★가장 중요한 불변식 — 청크 없는 발생행은 **0** 이어야 한다.

    2026-08-26 이전에는 45,816행이었다. 정리·적재·은퇴·별칭 정리를 거쳐 0이 됐다.
    다시 늘면 적재 경로 어딘가가 「발생만 쓰고 청크는 안 쓰는」 상태로 돌아간 것이다.
    """
    conn = _conn_or_skip()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM policy_clause_occurrence o WHERE NOT EXISTS ("
                "  SELECT 1 FROM policy_clause_chunk c WHERE c.content_hash = o.content_hash)"
            )
            orphans = cur.fetchone()[0]
    finally:
        conn.close()

    assert orphans == 0, (
        f"청크 없는 발생행이 {orphans:,}행 있다. 적재 경로가 「발생만 쓰고 청크는 "
        "안 쓰는」 상태로 돌아갔는지 본다 — "
        "docs/reports/debugs/2026-08-25_2300_고아발생_원인_재규명과_단위정정.md"
    )
