"""발생행 **수록 순번** 계약.

결함: `docs/reports/debugs/2026-08-25_1400_pg조항색인에_수록순번이_없어_인용검증이_전건_실패한다.md`

★순번은 `occurrence_id`(= `릴리스:sha256:source_kind:순번`)의 재료다.
  **비면** 인용 검증이 "정확히 한 행"을 특정하지 못해 기권하고,
  **겹치면** 그 키를 「못 쓰는 것」으로 표시해 근거에서 버린다.
  둘 다 `CLAUSE_STORE=pg` 판정을 통째로 죽인다 — 실제로 그랬다.

여기서는 두 층을 나눠 본다.

    · 배선  — `upsert_occurrences` 가 넣은 뒤 **스스로 순번을 매기는가** (DB 불필요)
    · 불변식 — 실제 색인에서 순번이 **유일하고 빠짐없는가** (`pg` 마커)
"""

from __future__ import annotations

import pytest


# ────────────────────────────────────────────────── 배선 (DB 없이)


def test_upsert_는_넣은_뒤_순번을_매긴다(monkeypatch):
    """★호출자가 잊을 수 있는 일이 아니다. **표에 넣는 쪽이 책임진다.**

    전에는 아무도 순번을 안 매겨 `occurrence_id` 가 통째로 비어 있었다.
    """
    from db.postgres import pgvector_clause_index as ix

    called = {}

    def fake_assign(conn, *, generation, sha256s=None):
        called["generation"] = generation
        called["sha256s"] = sha256s
        return 0

    monkeypatch.setattr(ix, "assign_ordinals", fake_assign)

    class _Cur:
        rowcount = 1

        def execute(self, *a, **k):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            return None

    rows = [
        ("h1", "sha_A", "보험사", "보통약관/1.", "", "", 1, 1, "clause"),
        ("h2", "sha_B", "보험사", "보통약관/2.", "", "", 2, 2, "clause"),
        ("h3", "sha_A", "보험사", "[별표1]", "", "", 3, 3, "annex"),
    ]
    ix.upsert_occurrences(_Conn(), rows, generation="s6")

    assert called, "upsert 가 순번을 매기지 않았다 — occurrence_id 가 비게 된다"
    assert called["generation"] == "s6"
    #: ★**건드린 문서만** 다시 매긴다. 세대 전체를 훑으면 남의 문서까지 잠근다.
    assert called["sha256s"] == ["sha_A", "sha_B"]


def test_빈_입력이면_순번을_매기지_않는다(monkeypatch):
    """★넣은 것이 없는데 순번을 다시 매기면 **세대 전체를 훑는다** —
    공유 DB 에 불필요한 부하와 잠금을 준다."""
    from db.postgres import pgvector_clause_index as ix

    called = []
    monkeypatch.setattr(
        ix, "assign_ordinals",
        lambda conn, *, generation, sha256s=None: called.append(sha256s) or 0,
    )

    class _Cur:
        rowcount = 0

        def execute(self, *a, **k):
            raise AssertionError("빈 입력에 INSERT 를 실행할 이유가 없다")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            return None

    ix.upsert_occurrences(_Conn(), [], generation="s6")
    assert not called, "빈 입력인데 순번 재부여가 돌았다"


# ────────────────────────────────────────────────── 불변식 (실 색인)


@pytest.mark.pg
def test_인용_가능한_행은_모두_순번을_갖는다():
    """★게이트가 채워진 행 = 근거로 쓸 수 있는 행. 여기 순번이 없으면 못 쓴다."""
    from db.postgres import pgvector_clause_index as ix
    from db.postgres.pgvector_index import get_conn

    gen = ix.current_generation()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM policy_clause_occurrence "
                "WHERE index_generation = %s AND parse_status IS NOT NULL "
                "  AND ordinal IS NULL",
                (gen,),
            )
            missing = cur.fetchone()[0]
    finally:
        conn.close()
    assert missing == 0, f"게이트는 있는데 순번이 없는 행 {missing:,}건 — 인용 근거로 못 쓴다"


@pytest.mark.pg
def test_순번은_문서_종류별로_유일하다():
    """★겹치면 `occurrence_id` 가 둘을 가리켜 "정확히 한 행"이 성립하지 않는다."""
    from db.postgres import pgvector_clause_index as ix
    from db.postgres.pgvector_index import get_conn

    gen = ix.current_generation()
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) FROM (
                    SELECT sha256, source_kind, ordinal
                      FROM policy_clause_occurrence
                     WHERE index_generation = %s AND ordinal IS NOT NULL
                     GROUP BY 1,2,3 HAVING count(*) > 1) x
                """,
                (gen,),
            )
            dupes = cur.fetchone()[0]
    finally:
        conn.close()
    assert dupes == 0, f"순번이 겹치는 (문서, 종류, 번호) 조합 {dupes:,}건"


@pytest.mark.pg
def test_다시_매겨도_값이_그대로다():
    """★멱등하지 않으면 적재할 때마다 인용 식별자가 바뀐다 —
    어제 발급한 판정의 근거를 오늘 못 찾게 된다."""
    from db.postgres import pgvector_clause_index as ix
    from db.postgres.pgvector_index import get_conn

    #: ★★**세기만 한다**(`dry_run=True`, 2026-08-26).
    #:
    #:   앞서는 그냥 `assign_ordinals()` 를 불렀는데, 그 함수는 문서마다 커밋한다.
    #:   그래서 이 시험이 **운영 DB 의 순번 146,601행을 실제로 다시 매겼다.**
    #:   확인하려던 것을 확인하는 순간 바꿔 버린 것이다 —
    #:   순번이 바뀌면 `occurrence_id` 가 바뀌고, 그건 「어제 발급한 판정의 근거를
    #:   오늘 못 찾는다」는 뜻이다. 시험이 그걸 일으키면 안 된다.
    gen = ix.current_generation()
    conn = get_conn()
    try:
        changed = ix.assign_ordinals(conn, generation=gen, dry_run=True)
    finally:
        conn.close()
    #: ★★**근거가 바뀌었다** (2026-08-27, `occurrence_id` v2).
    #:
    #:   전에는 「순번이 밀리면 `occurrence_id` 가 바뀐다」가 이 시험의 이유였다.
    #:   이제 `occurrence_id` 는 `source_ordinal`(산출물이 매긴 자리)을 쓰므로
    #:   **이 순번이 밀려도 인용은 안 흔들린다.** 그게 v2 로 바꾼 이유다.
    #:
    #:   그래도 이 시험은 남긴다 — 이 값은 **검색 순서**를 정하고,
    #:   같은 입력에 다른 순서가 나오면 검색 결과가 실행마다 달라진다.
    #:   ★0 이 아니면 대개 **발생행을 지웠는데 다시 안 매긴 것**이다.
    #:     `assign_ordinals(scope="all_in_generation")` 로 맞춘다.
    assert changed == 0, (
        f"저장된 순번과 다시 매긴 값이 {changed:,}행 다르다 — 결정적이지 않다. "
        "★발생행을 지우거나 넣으면 남은 행의 검색 순번이 밀린다. "
        "인용(occurrence_id)은 이제 source_ordinal 을 쓰므로 안 흔들리지만, "
        "검색 순서가 실행마다 달라진다. assign_ordinals 로 다시 매겨야 한다."
    )


def test_청크_없는_해시에는_발생을_안_쓴다(capsys, monkeypatch):
    """★★고아를 «만들지 않는» 계약 (2026-08-26).

    앞서는 `_load` 가 발생을 **전량** 먼저 넣고 청크는 **내 샤드 몫만** 넣었다.
    그래서 샤드를 다 안 돌리거나 임베딩 도중에 죽으면 발생만 남았다 — 그게 고아다.
    전수 실측: 고아 45,816행 중 **32,065행(70.0%)** 이 이 원인.

    이제 `_write_occurrences` 가 **청크가 실제로 있는 해시에만** 쓴다.
    ★DB 없이 돈다 — `existing_hashes` 와 `upsert_occurrences` 만 바꿔 끼운다.
    """
    from scripts.index import build_clause_index as b
    from db.postgres import pgvector_clause_index as ix

    seen: dict = {}
    monkeypatch.setattr(ix, "existing_hashes", lambda conn: {"있다"})

    def _fake_upsert(conn, rows, *, generation):
        seen["rows"] = list(rows)
        return len(rows)

    monkeypatch.setattr(ix, "upsert_occurrences", _fake_upsert)

    b._write_occurrences(object(), [
        ("있다", "a" * 64, "보험사", "보통약관/1.", "보통약관", "제목", 1, 1, "clause", {}),
        ("없다", "a" * 64, "보험사", "보통약관/2.", "보통약관", "제목", 2, 2, "clause", {}),
    ], "s6")

    got = [r[0] for r in seen["rows"]]
    assert got == ["있다"], f"청크 없는 해시에 발생을 썼다 — 그 순간 고아가 된다: {got}"

    out = capsys.readouterr().out
    #: ★조용히 빼면 분모가 줄어 커버리지가 실제보다 좋아 보인다(CLAUDE.md §3).
    assert "안 쓴" in out and "1건" in out, "뺀 수를 안 찍었다: " + out


@pytest.mark.pg
def test_세대_전체_순번_다시매기기는_의도를_명시해야_한다():
    """★`sha256s=None` 은 「그 세대 전체」다 — 쓰기 기본값으로는 너무 넓다.

    순번이 바뀌면 `occurrence_id` 가 바뀌고, 그건 **어제 발급한 판정의 근거를
    오늘 못 찾는다**는 뜻이다. 실제로 시험 하나가 운영 순번 146,601행을 다시 매겼다.

    ★정상 적재는 안전하다 — `upsert_occurrences` 가 **건드린 문서 목록**을 넘긴다.
      위험은 소급 CLI 와 새 직접 호출에 있고, 그쪽만 막는다.
    ★`dry_run` 은 안 막는다. 세는 것은 아무것도 안 바꾼다.
    """
    from db.postgres import pgvector_clause_index as ix
    from db.postgres.pgvector_index import get_conn

    try:
        conn = get_conn()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG 없음: {str(exc)[:60]}")
    gen = ix.current_generation()
    try:
        with pytest.raises(ValueError, match="all_in_generation"):
            ix.assign_ordinals(conn, generation=gen)      # ← 범위도 scope 도 없이

        #: ★세기만 하는 것은 막지 않는다.
        n = ix.assign_ordinals(conn, generation=gen, dry_run=True)
        assert isinstance(n, int)

        #: ★문서를 지정하면 통과한다(정상 적재 경로).
        ix.assign_ordinals(conn, generation=gen, sha256s=[])
    finally:
        conn.close()
