"""인덱스 A — 약관 조항 벡터 색인.

★PG 없이 도는 것만 여기 둔다. 실제 적재·검색은 PG 가 떠 있을 때만 돈다.
  PG 를 요구하는 테스트를 무조건 통과시키지 않고 **건너뛴다고 말한다.**
"""

from __future__ import annotations

import contextlib

import pytest

from db.postgres import pgvector_clause_index as ix

#: 토큰 대신 **어절 수**로 세는 가짜 토크나이저. 모델 없이 규칙만 검사한다.
def _count(text: str) -> int:
    return len(text.split())


def test_짧은_조항은_쪼개지_않는다():
    body = "가 나 다 라"
    assert ix.chunk_clause(body, _count) == [body]


def test_어떤_조각도_토큰_예산을_넘지_않는다():
    """★이게 핵심이다. 넘으면 모델이 **조용히 잘라** 뒤를 임베딩하지 않는다.

    실측(2026-08-02): 800자 고정으로 자를 때 조각의 1.4%가 512토큰을 넘었다.
    """
    body = ". ".join("낱말 " * 60 for _ in range(20))
    parts = ix.chunk_clause(body, _count)
    assert len(parts) > 1
    assert all(_count(p) <= ix.MAX_TOKENS for p in parts)


def test_문장_경계로_끊는다():
    """★법률문은 예외가 문장 끝에 온다.

    "…보상합니다. 다만 … 보상하지 않습니다" 를 한가운데서 자르면
    뜻이 **반대로** 남는다.
    """
    head = "회사는 " + "이러이러한 경우에 " * 300 + "보상합니다."
    tail = "다만 고의로 자신을 해친 경우에는 보상하지 않습니다."
    parts = ix.chunk_clause(head + " " + tail, _count)
    assert len(parts) > 1
    #: 마지막 조각이 단서 문장을 통째로 담는다.
    assert tail in parts[-1]


def test_한_문장이_예산을_넘으면_그_문장만_자른다():
    huge = "낱말 " * 1000
    parts = ix.chunk_clause(huge, _count)
    assert all(_count(p) <= ix.MAX_TOKENS for p in parts)
    assert len(parts) >= 3


def test_조각들이_겹친다():
    body = ". ".join("낱말 " * 60 for _ in range(10))
    parts = ix.chunk_clause(body, _count)
    #: 겹침이 있으면 조각 토큰 합이 원문보다 크다.
    assert sum(_count(p) for p in parts) > _count(body)


def test_빈_약관목록은_전역검색으로_바뀌지_않는다():
    #: ★"쓸 수 있는 약관이 없다"를 "전부에서 찾자"로 바꾸면
    #:   2019년 가입자에게 2024년 조항이 근거로 붙는다.
    #:   conn 을 건드리기 전에 걸러야 하므로 None 을 넘겨도 터지지 않아야 한다.
    assert ix.search(None, [0.0] * 8, sha256s=[], limit=5) == []


def test_필터를_기본값으로_두지_않는다():
    #: ★`sha256s` 는 키워드 필수다. 안 넘기면 호출이 실패해야 한다 —
    #:   기본값이 있으면 판정 경로가 조용히 전역 검색을 한다.
    with pytest.raises(TypeError):
        ix.search(None, [0.0] * 8)  # type: ignore[call-arg]


def test_검색결과는_어느_문서_어디인지를_들고_다닌다():
    hit = ix.ClauseHit(
        content_hash="deadbeefcafebabe",
        chunk_ix=0,
        text="…",
        distance=0.1,
        sha256="a" * 64,
        insurer="가보험",
        qualified_no="보통약관/9.",
        section="보통약관",
        title="보상하지 않는 사항",
        page_from=12,
        page_to=13,
    )
    #: 인용 식별자는 판정 경로(`ClauseRow.clause_id`)와 같은 규칙이다.
    assert hit.clause_id == "aaaaaaaaaaaa/보통약관/9.#deadbeef"


# ---------------------------------------------------------------- PG 필요


def _conn_or_skip():
    from db.postgres.pgvector_index import get_conn

    try:
        return get_conn()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG 없음 — 건너뜀: {str(exc)[:80]}")


@contextlib.contextmanager
def _isolated_conn(name: str):
    """**임시 스키마에 격리된** 연결. `ensure_schema` 를 부르는 시험은 여기를 쓴다.

    ★★왜 (2026-08-26 · 코덱스 감사 A1)

        `ensure_schema()` 는 `conn` 하나만 받고 **스키마 전체에 DDL 을 건다** —
        열 추가, 인덱스 생성, 그리고 **기본키 DROP/ADD**. 지금 운영 테이블은
        19만행대다. 그 잠금을 시험이 일으켜서는 안 된다.

        「DDL 이 멱등이니 괜찮다」는 **격리를 대신하지 않는다.** 옛 PK/FK/열 상태에서는
        실제로 DROP/ADD 가 돌고, 그 사이 다른 세션이 밀린다
        (실측 2026-08-03: 조회 하나가 3시간 락을 쥐어 12개 세션이 밀렸다).

    ★세 요건을 **여기서 한 번에** 만족시킨다 —
      `CREATE SCHEMA` · `SET search_path` · **빈 테이블 확인**.
      시험마다 손으로 쓰면 하나씩 빠진다 — 실제로 8곳/10곳이 빠져 있었다.

    ★`public` 을 뒤에 둔다. `vector` 확장 타입이 거기 있다.
    """
    conn = _conn_or_skip()
    schema = f"t_{name}"
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute(f'CREATE SCHEMA "{schema}"')
            cur.execute(f'SET search_path TO "{schema}", public')
        conn.commit()

        ix.ensure_schema(conn)
        conn.commit()

        #: ★★**정말 빈 테이블을 보고 있는가.** 아니면 여기서 멈춘다 —
        #:   운영 테이블에 붙은 채 진행하면 그 시험이 다음 사고다.
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM policy_clause_occurrence")
            n = cur.fetchone()[0]
        assert n == 0, (
            f"임시 스키마가 아니라 운영 테이블을 보고 있다(발생 {n:,}행) — 중단. "
            f"search_path 가 '{schema}' 로 안 옮겨졌다."
        )
        yield conn
    finally:
        #: ★먼저 롤백한다. 앞에서 터졌으면 트랜잭션이 망가진 채라 정리 SQL 도 죽고
        #:   **임시 스키마가 남는다.**
        try:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("SET search_path TO public")
                cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.commit()
        finally:
            conn.close()


@pytest.mark.pg
def test_스키마와_적재와_검색이_이어진다():
    #: ★임시 스키마에 격리한다 — `ensure_schema` 는 스키마 전체에 DDL 을 건다
    #:   (기본키 DROP/ADD 포함). 운영 19만행에 그 잠금을 걸면 안 된다.
    with _isolated_conn("schema_e2e") as conn:

        h = "테스트해시_" + "0" * 10
        #: ★차원을 상수로 박지 않는다 — `ix.embed_dim()` 과 같은 이유다(adapter §_EMBED_DIM_FALLBACK).
        #:   768 을 박아 두었더니 승인 릴리스가 arctic-ko(1024d) 로 올라간 순간
        #:   테이블은 `vector(1024)` 인데 테스트만 768 을 넣어 **DataException** 이 났다(2026-08-04).
        #:   차원은 **승인 프로필이 정한다.** 테스트도 같은 출처를 봐야 한다.
        vec = [0.0] * ix.embed_dim()
        vec[0] = 1.0
        body = "상해라 함은 급격하고 우연한 외래의 사고를 말합니다."
        ix.upsert_content(conn, [(h, body, 1)])
        ix.upsert_chunks(conn, [(h, 0, 1, body, vec)])
        ix.upsert_occurrences(
            conn, [(h, "t" * 64, "테스트보험", "보통약관/2.", "보통약관", "용어의 정의", 3, 3)]
        )

        #: ★같은 것을 두 번 넣어도 늘지 않는다(재개 가능해야 하므로).
        again = ix.upsert_chunks(conn, [(h, 0, 1, "무시됨", vec)])
        assert again == 0

        #: ★온전히 들어간 것만 "완료"다.
        assert h in ix.existing_hashes(conn)

        hits = ix.search(conn, vec, sha256s=["t" * 64], limit=3)
        assert hits and hits[0].content_hash == h
        assert hits[0].insurer == "테스트보험"

        #: ★다른 약관으로 가두면 안 나온다.
        assert ix.search(conn, vec, sha256s=["z" * 64], limit=3) == []

        #: ★★**지우는 순서가 있다** (2026-08-26, `occurrence/chunk → content` 외래키 신설 이후).
        #:   본문을 먼저 지우면 그것을 가리키는 조각·발생 때문에 `RESTRICT` 에 막힌다.
        #:   **자식 먼저, 부모 나중** — 조각·발생 → 본문.
        #:   ★`CASCADE` 로 두지 않은 이유가 이것이다 — 본문 한 줄을 지울 때 벡터가
        #:     조용히 함께 사라지면 검색 결과가 줄어드는데 아무도 모른다.
        with conn.cursor() as cur:
            cur.execute("DELETE FROM policy_clause_chunk WHERE content_hash = %s", (h,))
            cur.execute("DELETE FROM policy_clause_occurrence WHERE content_hash = %s", (h,))
            cur.execute("DELETE FROM policy_clause_content WHERE content_hash = %s", (h,))
        conn.commit()
        conn.close()


@pytest.mark.pg
def test_반쪽으로_남은_조항은_완료로_치지_않는다():
    """★조각 하나만 들어가도 "완료"로 보던 버그.

    배치 중간에 죽으면 나머지 조각이 영구 누락되고 다음 실행이 건너뛴다.
    실측(2026-08-02 중단 지점): 내용 12,507개 중 2개가 그렇게 잘려 있었다.
    """
    #: ★임시 스키마에 격리한다 — `ensure_schema` 는 스키마 전체에 DDL 을 건다
    #:   (기본키 DROP/ADD 포함). 운영 19만행에 그 잠금을 걸면 안 된다.
    with _isolated_conn("halfdone") as conn:
        h = "반쪽테스트해시"
        vec = [0.0] * ix.embed_dim()  # 상수 금지 — 위 테스트의 주석 참조
        #: 3조각이라고 선언하고 1조각만 넣는다 = 중간에 죽은 상태
        ix.upsert_content(conn, [(h, "본문", 3)])
        ix.upsert_chunks(conn, [(h, 0, 3, "조각0", vec)])

        assert h not in ix.existing_hashes(conn)

        #: ★`drop_incomplete()` 를 여기서 부르지 않는다.
        #:
        #:   처음엔 불렀다가 **공유 DB의 조각 43,064개를 통째로 지웠다**(2026-08-02).
        #:   그 함수는 저장소 전체를 훑는 정리 작업이라, 테스트가 쓰는 순간
        #:   남의 적재 결과까지 날린다. 테스트는 **자기가 넣은 것만** 치운다.
        #:   정리 함수의 동작은 아래에서 자기 해시로만 확인한다.
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM policy_clause_chunk WHERE content_hash = %s "
                "AND content_hash IN (SELECT content_hash FROM policy_clause_content "
                "                     WHERE n_chunks <> ("
                "  SELECT count(*) FROM policy_clause_chunk k WHERE k.content_hash = %s))",
                (h, h),
            )
            assert cur.rowcount == 1, "미완성 조각이 정리 대상으로 잡혀야 한다"
            cur.execute("DELETE FROM policy_clause_content WHERE content_hash = %s", (h,))
        conn.commit()
        conn.close()


# ── drop_incomplete 이 고아를 만들지 않는다 (2026-08-25) ──────────────────

@pytest.mark.pg
def test_정리가_발생이_가리키는_본문을_지우지_않는다():
    """★실측으로 규명된 결함의 회귀 시험(고아 발생 38,326행).

    `drop_incomplete()` 가 「벡터 없는 본문」을 지우면서 `occurrence` 는 안 지웠다.
    재임베딩으로 모델 이름이 바뀌면 옛 해시의 조각이 «현재 모델»에 없어
    본문이 반쪽으로 보이는데, 그 본문을 가리키던 옛 세대 발생은 그대로 남는다.

    ★**임시 스키마에 격리해서 돌린다.** 이 함수는 저장소 전체를 훑으므로
      그냥 부르면 운영 데이터를 지운다 — 실제로 조각 43,064개를 지운 적이 있다.
      `search_path` 를 옮긴 뒤 **정말 옮겨졌는지 먼저 확인**하고 나서만 진행한다.
    """
    conn = _conn_or_skip()
    schema = "t_drop_incomplete_guard"
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{schema}"')
    conn.commit()
    try:
        #: ★`public` 을 뒤에 둬야 한다 — `vector` 확장 타입이 거기 있다.
        #:   임시 스키마만 두면 `type "vector" does not exist` 로 죽는다.
        #:   **테이블은 앞선 임시 스키마에 만들어지고**, 타입만 public 에서 찾는다.
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')
        ix.ensure_schema(conn)

        #: ★★안전 확인 — 임시 스키마의 빈 테이블을 보고 있는가.
        #:   운영 테이블(발생 30만행대)에 붙어 있으면 **여기서 멈춘다.**
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM policy_clause_occurrence")
            n = cur.fetchone()[0]
        assert n == 0, f"임시 스키마가 아니라 운영 테이블을 보고 있다(발생 {n:,}행) — 중단"

        vec = [0.0] * ix.embed_dim()
        vec[0] = 1.0
        model = ix.current_embed_model()

        keep = "해시_발생이_가리킨다"   # 벡터 없음 + 발생 있음 → 남아야 한다
        gone = "해시_아무도_안_쓴다"     # 벡터 없음 + 발생 없음 → 지워도 된다
        ix.upsert_content(conn, [(keep, "본문A", 1), (gone, "본문B", 1)])
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO policy_clause_occurrence"
                " (content_hash, sha256, insurer, qualified_no, section, title,"
                "  page_from, page_to, index_generation, source_kind)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (keep, "a" * 64, "삼성화재", "제1조", "보통약관", "t", 1, 2, "s-old", "clause"),
            )
        conn.commit()

        #: ★격리된 임시 스키마다(위에서 빈 테이블 확인 완료). scope 를 명시한다.
        result = ix.drop_incomplete(conn, scope="all")

        #: 반환이 dict 이고 무슨 일이 있었는지 말한다(앞서는 int 하나였다).
        #: ★키를 **정확히** 대조한다. 하나 늘면 여기서 걸리고, 그때 「무엇이
        #:   늘었는지」를 보게 된다 — 조용히 추가되면 아무도 안 읽는 값이 된다.
        assert set(result) == {"chunks_deleted", "content_deleted",
                               "content_kept", "orphans_before",
                               #: 2026-08-26 · 조각 삭제가 **새로 만든** 고아 수(DB10)
                               "orphaned_by_drop"}
        assert result["content_deleted"] == 1, "아무도 안 쓰는 본문은 지운다"
        assert result["content_kept"] == 1, "발생이 가리키는 본문은 남기고 센다"

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM policy_clause_content WHERE content_hash=%s", (keep,))
            assert cur.fetchone()[0] == 1, "★지우면 그 발생이 고아가 된다"
            cur.execute("SELECT count(*) FROM policy_clause_content WHERE content_hash=%s", (gone,))
            assert cur.fetchone()[0] == 0
            #: 이 정리가 고아를 **하나도** 만들지 않았는지 직접 센다.
            cur.execute(
                "SELECT count(*) FROM policy_clause_occurrence o WHERE NOT EXISTS ("
                "  SELECT 1 FROM policy_clause_content t WHERE t.content_hash=o.content_hash)")
            assert cur.fetchone()[0] == 0, "정리가 고아를 만들었다"
        assert model  # 승인 프로필이 비면 위 단언들의 뜻이 달라진다
    finally:
        with conn.cursor() as cur:
            cur.execute("SET search_path TO public")
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()


@pytest.mark.pg
def test_게이트를_안_주는_호출이_아는_값을_NULL로_덮지_않는다():
    """★★**아는 값을 모르는 값으로 덮지 않는다.**

    `upsert_occurrences` 는 게이트를 안 받으면 `{}` 로 두고 네 필드를 NULL 로 쓴다.
    앞서는 `ON CONFLICT DO UPDATE SET x = EXCLUDED.x` 라, 게이트를 주는 호출이
    채워 둔 값을 **게이트를 안 주는 호출이 지워 버렸다.**

    NULL 은 「모른다」다. 그 조항은 조용히 **인용 불가**로 떨어진다 —
    검색에서 사라지는데 아무도 그 사실을 모른다.

    실물 증거(2026-08-25): `s6` 210,733행 중 청크가 있으면서 게이트 4필드가
    전부 NULL 인 발생이 **정확히 한 행** 있었다. 그때는 1건이었지만 막혀 있지 않았다.
    """
    #: ★임시 스키마에 격리한다 — `ensure_schema` 는 스키마 전체에 DDL 을 건다
    #:   (기본키 DROP/ADD 포함). 운영 19만행에 그 잠금을 걸면 안 된다.
    with _isolated_conn("gate_overwrite") as conn:

        h = "게이트덮기_" + "0" * 10
        sha = "g" * 64
        base = (h, sha, "테스트보험", "보통약관/9.", "보통약관", "보상하지 않는 사항", 11, 11)
        gate = {"citation_eligible": True, "chunk_type": "clause",
                "is_statute": False, "parse_status": "ok"}

        def _gate_now():
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT citation_eligible, chunk_type, is_statute, parse_status "
                    "FROM policy_clause_occurrence WHERE content_hash=%s AND sha256=%s",
                    (h, sha))
                return cur.fetchone()

        try:
            #: ★★**본문을 먼저 넣는다** — 2026-08-26 에 `occurrence → content` 외래키가
            #:   생겼다. 본문 없는 발생행은 이제 DB 가 막는다(그게 이 FK 의 목적이다).
            #:   앞서는 발생만 넣어도 통과했는데, 그때는 **막는 것이 없었다.**
            ix.upsert_content(conn, [(h, "본문", 1)])

            #: ① 게이트를 주는 호출 — 채워진다.
            ix.upsert_occurrences(conn, [(*base, "clause", gate)], generation="s6")
            assert _gate_now() == (True, "clause", False, "ok")

            #: ② 게이트를 **안 주는** 호출이 같은 자리를 다시 쓴다.
            #:    ★여기서 지워지면 안 된다. 앞서는 지워졌다.
            ix.upsert_occurrences(conn, [base], generation="s6")
            assert _gate_now() == (True, "clause", False, "ok"), (
                "게이트를 안 주는 호출이 아는 값을 NULL 로 덮었다 — "
                "그 조항은 조용히 인용 불가가 된다"
            )

            #: ③ 그렇다고 **얼어붙지도 않는다.** 새로 아는 값이 오면 반영한다.
            ix.upsert_occurrences(
                conn, [(*base, "clause", {**gate, "citation_eligible": False,
                                          "parse_status": "suspect"})], generation="s6")
            assert _gate_now() == (False, "clause", False, "suspect"), (
                "새 값이 왔는데 옛 값을 붙들고 있다"
            )
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM policy_clause_occurrence WHERE content_hash=%s", (h,))
            conn.commit()
            conn.close()


@pytest.mark.pg
def test_정합도구가_지워도_되는_것만_지운다():
    """`reconcile_occurrences` — **증거 없이, 쓰이는 것을, 남의 출처를** 지우지 않는다.

    ★2026-08-25 실측에서 지우기 **직전에** 잡은 결함이 배경이다.
      첫 판은 `source_kind` 를 안 봐서, 삭제 후보 14,378행 중 **850행이
      `citation_eligible=True` 이고 청크도 있었다** — S7.1 승인 OCR fact 였다.
      「구조화 산출물에 없으니 낡았다」로 읽었는데, 애초에 **다른 출처**였다.
    """
    #: ★임시 스키마에 격리한다 — `ensure_schema` 는 스키마 전체에 DDL 을 건다
    #:   (기본키 DROP/ADD 포함). 운영 19만행에 그 잠금을 걸면 안 된다.
    with _isolated_conn("reconcile") as conn:

        sha_live = "r" * 64        # 산출물이 있는 문서
        sha_dark = "s" * 64        # 산출물을 못 읽은 문서 — 건드리면 안 된다
        vec = [0.0] * ix.embed_dim(); vec[0] = 1.0
        h_keep, h_stale, h_used, h_fact = ("k" * 40, "t" * 40, "u" * 40, "f" * 40)
        G = "s6"

        def _n(sha, h):
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM policy_clause_occurrence "
                            "WHERE sha256=%s AND content_hash=%s", (sha, h))
                return cur.fetchone()[0]

        try:
            gate = {"citation_eligible": True, "chunk_type": "clause",
                    "is_statute": False, "parse_status": "ok"}
            #: ★★**본문을 먼저 넣는다** — 2026-08-26 에 `occurrence → content` 외래키가
            #:   생겼다. 본문 없는 발생행은 이제 DB 가 막는다(그게 이 FK 의 목적이다).
            #:   앞서는 발생만 넣어도 통과했는데, 그때는 **막는 것이 없었다.**
            ix.upsert_content(conn, [(x, "본문", 1) for x in (h_keep, h_stale, h_used, h_fact)])
            rows = [
                (h_keep,  sha_live, "보험사", "보통약관/1.", "보통약관", "유지", 1, 1, "clause", {}),
                (h_stale, sha_live, "보험사", "보통약관/2.", "보통약관", "낡음", 2, 2, "clause", {}),
                #: 산출물엔 없지만 **쓰이는** 행 — 안전장치가 지켜야 한다
                (h_used,  sha_live, "보험사", "보통약관/3.", "보통약관", "쓰임", 3, 3, "clause", gate),
                #: **다른 출처** — 심판 대상이 아니다
                (h_fact,  sha_live, "보험사", "OCR표/x", "자기부담금 표", "표", 4, 4,
                 "approved_ocr_table_fact", gate),
                #: 산출물을 못 읽은 문서의 행 — 건너뛰어야 한다
                (h_stale, sha_dark, "보험사", "보통약관/9.", "보통약관", "미지", 9, 9, "clause", {}),
            ]
            ix.upsert_occurrences(conn, rows, generation=G)

            art = {sha_live: {h_keep}}          # ★sha_dark 는 **일부러 안 넣는다**

            #: ① 조회만 — 아무것도 안 지운다
            r = ix.reconcile_occurrences(conn, generation=G, artifact_hashes=art, apply=False)
            assert r["documents_skipped"] >= 1, "산출물 없는 문서를 건너뛰었다고 말해야 한다"
            assert r["deleted"] == 0
            assert all(_n(s, h) == 1 for s, h in
                       ((sha_live, h_keep), (sha_live, h_stale), (sha_live, h_used),
                        (sha_live, h_fact), (sha_dark, h_stale)))

            #: ② 실제로 지운다 — **낡고 안 쓰이는 것만**
            #:    ★백업 테이블 없이는 지울 수 없다(`test_지운_행을_한_줄로_되돌릴_수_있다`).
            r = ix.reconcile_occurrences(conn, generation=G, artifact_hashes=art, apply=True,
                                         backup_table="pco_reconcile_probe")
            assert _n(sha_live, h_stale) == 0, "낡은 행은 지워야 한다"
            assert _n(sha_live, h_keep) == 1, "산출물에 있는 행을 지웠다"
            assert _n(sha_live, h_used) == 1, "인용 가능한 행을 지웠다 — 안전장치가 안 먹었다"
            assert _n(sha_live, h_fact) == 1, "다른 출처를 심판했다 — S7.1 승인 fact 가 이 경로로 사라진다"
            assert _n(sha_dark, h_stale) == 1, "산출물을 못 읽은 문서의 행을 지웠다"
            assert r["protected"] >= 1, "지키느라 뺀 행 수를 말해야 한다"
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM policy_clause_occurrence WHERE sha256 = ANY(%s)",
                            ([sha_live, sha_dark],))
                cur.execute("DROP TABLE IF EXISTS pco_reconcile_probe")
            conn.commit()
            conn.close()


@pytest.mark.pg
def test_지운_행을_한_줄로_되돌릴_수_있다():
    """★★**되돌릴 수 없는 삭제는 하지 않는다.**

    13,528행을 지우는 작업이다. 「백업이 있다」가 아니라 **「되돌려 봤다」** 여야 한다.
    백업이 나중에 안 돌아오는 것은 백업이 아니다.

    ★백업 없이 `apply=True` 를 부르면 **거절**한다. 깜빡해서 지울 수 없게.
    ★백업 행수 ≠ 삭제 행수면 트랜잭션을 되돌린다.
    """
    #: ★임시 스키마에 격리한다 — `ensure_schema` 는 스키마 전체에 DDL 을 건다
    #:   (기본키 DROP/ADD 포함). 운영 19만행에 그 잠금을 걸면 안 된다.
    with _isolated_conn("rollback") as conn:

        sha = "b" * 64
        h_keep, h_stale = "K" * 40, "T" * 40
        G, BK = "s6", "pco_rollback_probe"

        def _rows():
            with conn.cursor() as cur:
                cur.execute("SELECT content_hash, page_from, title FROM policy_clause_occurrence "
                            "WHERE sha256=%s ORDER BY content_hash", (sha,))
                return cur.fetchall()

        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP TABLE IF EXISTS {BK}")
            conn.commit()
            #: ★★**본문을 먼저 넣는다** — 2026-08-26 에 `occurrence → content` 외래키가
            #:   생겼다. 본문 없는 발생행은 이제 DB 가 막는다(그게 이 FK 의 목적이다).
            #:   앞서는 발생만 넣어도 통과했는데, 그때는 **막는 것이 없었다.**
            ix.upsert_content(conn, [(h_keep, "본문", 1), (h_stale, "본문", 1)])
            ix.upsert_occurrences(conn, [
                (h_keep,  sha, "보험사", "보통약관/1.", "보통약관", "유지", 1, 1, "clause", {}),
                (h_stale, sha, "보험사", "보통약관/2.", "보통약관", "낡음", 2, 2, "clause", {}),
            ], generation=G)
            before = _rows()
            assert len(before) == 2

            #: ① 백업 이름 없이 지우려 하면 **거절**한다.
            with pytest.raises(ValueError, match="되돌릴 수 없는"):
                ix.reconcile_occurrences(conn, generation=G, artifact_hashes={sha: {h_keep}},
                                         apply=True)
            assert _rows() == before, "거절했는데 뭔가 지워졌다"

            #: ② 지운다 — 백업에 그대로 남는다.
            r = ix.reconcile_occurrences(conn, generation=G, artifact_hashes={sha: {h_keep}},
                                         apply=True, backup_table=BK)
            assert r["deleted"] == 1 and r["backed_up"] == r["deleted"]
            assert len(_rows()) == 1

            #: ③ ★한 줄로 되돌린다 — **문서에 적은 그 문장 그대로**.
            with conn.cursor() as cur:
                cur.execute(f"INSERT INTO policy_clause_occurrence SELECT * FROM {BK}")
            conn.commit()
            assert _rows() == before, "되돌렸는데 원래대로가 아니다"
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM policy_clause_occurrence WHERE sha256=%s", (sha,))
                cur.execute(f"DROP TABLE IF EXISTS {BK}")
            conn.commit()
            conn.close()


@pytest.mark.pg
def test_인용가능이_불가로_바뀌면_DB가_그_사실을_듣는다():
    """★★코덱스 교차검증이 잡은 결함(2026-08-26).

    적재기가 게이트에 걸린 조항을 `continue` 로 건너뛰어 **발생을 아예 안 보냈다.**
    그래서 인용 가능(True)이던 조항이 불가로 바뀌어도 DB 는 옛 `True` 를 들고 있었고,
    판정 경로가 그 값을 **현재 값으로 읽어** 인용 불가가 된 조항을 근거로 쓸 수 있었다.

    ★`COALESCE` 수정만으로는 안 막힌다 — 그 수정은 「NULL 이 아는 값을 못 덮게」 한 것이고,
      여기 문제는 **아무 값도 안 보내는** 것이다. 둘은 다른 구멍이다.

    ★★그렇다고 **새로 넣어서는 안 된다.** 넣으면 청크 없는 발생이 늘어 그게 고아다.
      `demote_occurrences` 는 **이미 있는 행만** 갱신한다.
    """
    #: ★임시 스키마에 격리한다 — `ensure_schema` 는 스키마 전체에 DDL 을 건다
    #:   (기본키 DROP/ADD 포함). 운영 19만행에 그 잠금을 걸면 안 된다.
    with _isolated_conn("demote") as conn:

        sha = "d" * 64
        h_live, h_absent = "L" * 40, "A" * 40
        G = "s6"
        ok = {"citation_eligible": True, "chunk_type": "clause",
              "is_statute": False, "parse_status": "ok"}
        bad = {"citation_eligible": False, "chunk_type": "clause",
               "is_statute": False, "parse_status": "ok"}

        def _gate(h):
            with conn.cursor() as cur:
                cur.execute("SELECT citation_eligible FROM policy_clause_occurrence "
                            "WHERE content_hash=%s AND sha256=%s", (h, sha))
                r = cur.fetchone()
                return r[0] if r else "행없음"

        def _count():
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM policy_clause_occurrence WHERE sha256=%s", (sha,))
                return cur.fetchone()[0]

        try:
            #: ★★**본문을 먼저 넣는다** — 2026-08-26 에 `occurrence → content` 외래키가
            #:   생겼다. 본문 없는 발생행은 이제 DB 가 막는다(그게 이 FK 의 목적이다).
            #:   앞서는 발생만 넣어도 통과했는데, 그때는 **막는 것이 없었다.**
            ix.upsert_content(conn, [(h_live, "본문", 1)])
            ix.upsert_occurrences(conn, [
                (h_live, sha, "보험사", "보통약관/5.", "보통약관", "인용가능이던 조항",
                 5, 5, "clause", ok),
            ], generation=G)
            assert _gate(h_live) is True and _count() == 1

            #: 재추출에서 인용 불가가 됐다 — DB 에 있는 행은 **갱신**되고,
            #: DB 에 없던 조항은 **새로 안 들어간다.**
            r = ix.demote_occurrences(conn, [(h_live, sha, bad), (h_absent, sha, bad)],
                                      generation=G)
            assert _gate(h_live) is False, "인용 불가로 바뀐 사실이 DB 에 안 닿았다"
            assert _gate(h_absent) == "행없음", "없던 행을 새로 넣었다 — 그게 곧 고아다"
            assert _count() == 1
            assert r["matched"] == 1
            #: ★「그전까지 근거로 나갈 수 있었다」는 사실을 세어 말해야 한다.
            assert r["was_true"] == 1, "True 였다가 바뀐 수를 안 세면 심각도를 못 알린다"
        finally:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM policy_clause_occurrence WHERE sha256=%s", (sha,))
            conn.commit()
            conn.close()


@pytest.mark.pg
def test_본문_없는_조각은_DB가_막는다():
    """★이 저장소에는 외래키가 **0개**였다(2026-08-26 이전).

    그래서 조각·발생이 본문 없이 들어가도 아무도 안 막았고 고아가 45,816행 쌓였다.
    안전했던 이유는 DB 가 막아서가 아니라 **적재 코드가 우연히 순서를 지켜서**였다.

    ★`chunk → content` 는 위반 0이라 지금 세울 수 있다.
      `occurrence → content` 는 위반 28,227이라 아직 못 세운다 — 그건 적재 정합
      문제라 코드가 아니라 데이터를 먼저 고쳐야 한다.
    """
    #: ★임시 스키마에 격리한다 — `ensure_schema` 는 스키마 전체에 DDL 을 건다
    #:   (기본키 DROP/ADD 포함). 운영 19만행에 그 잠금을 걸면 안 된다.
    with _isolated_conn("fk_guard") as conn:
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT confdeltype FROM pg_constraint "
                        "WHERE conname = 'policy_clause_chunk_content_fk'")
            row = cur.fetchone()
        assert row, "chunk → content 외래키가 없다"
        #: ★`CASCADE` 면 본문 한 줄을 지울 때 벡터가 조용히 함께 사라진다 —
        #:   검색 결과가 줄어드는데 아무도 모른다. 막고 사람이 보게 해야 한다.
        assert row[0] == "r", f"ON DELETE 가 RESTRICT 가 아니다: {row[0]!r}"

        vec = "[" + ",".join(["0"] * ix.embed_dim()) + "]"
        try:
            with conn.cursor() as cur:
                with pytest.raises(Exception) as ei:
                    cur.execute(
                        "INSERT INTO policy_clause_chunk"
                        "(content_hash,chunk_ix,n_chunks,text,embedding,embed_model) "
                        "VALUES (%s,0,1,'x',%s,'m')", ("고아조각_" + "z" * 30, vec))
                assert "ForeignKey" in type(ei.value).__name__, type(ei.value).__name__
        finally:
            conn.rollback()
            conn.close()


@pytest.mark.pg
def test_조각_삭제가_고아를_만들면_그_수를_말한다():
    """★`drop_incomplete` 의 **조각 삭제**가 고아를 만든다 — 2026-08-26(DB10).

    본문 삭제 쪽은 2026-08-25 에 막았다(발생이 가리키면 남긴다).
    그런데 그 위의 **조각 삭제는 그대로**였다. 반쪽 조각을 지우는 것은 맞지만,
    그 해시를 발생행이 가리키고 있으면 지우는 순간 고아가 된다.

    ★외래키는 이걸 **안 막는다.** `chunk → content` 는 「본문 없는 조각을 못 넣게」
      하는 것이지 「조각을 지우지 못하게」 하는 것이 아니다.

    ★그렇다고 안 지울 수는 없다 — 반쪽을 남기면 그게 근거로 나간다.
      **지우되 몇 건이 고아가 되는지 세어 보고한다.** 조용히 만들지 않는다.

    ★★**임시 스키마에 격리해서 돌린다.**

        `drop_incomplete()` 는 **저장소 전체를 훑는다.** 그냥 부르면 운영 데이터를 지운다.
        같은 파일 위쪽 `test_정리가_발생이_가리키는_본문을_지우지_않는다` 가 그 이유로
        이미 격리해 두었고, 그 주석에 「실제로 조각 43,064개를 지운 적이 있다」고 적혀 있다.

        ★그런데 이 시험을 처음 쓸 때 **그 경고를 읽고도 격리를 안 했다**(2026-08-26).
          운영 `mall_vec` 에서 본문 **1,506행**이 지워졌다 — 조각도 발생도 가리키지 않던
          행이라 무결성은 안 깨졌지만, **되돌아갈 곳인 옛 DB 가 기준선과 달라졌다.**
          같은 실수를 두 번 하지 않으려고 여기 남긴다.
    """
    conn = _conn_or_skip()
    schema = "t_drop_orphan_count"
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{schema}"')
    conn.commit()

    h, sha = "반쪽조각_" + "9" * 12, "o" * 64
    try:
        #: ★`public` 을 뒤에 둬야 한다 — `vector` 확장 타입이 거기 있다.
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}", public')
        ix.ensure_schema(conn)
        conn.commit()

        #: ★★**안전 확인 — 임시 스키마의 빈 테이블을 보고 있는가.**
        #:   운영 테이블(발생 19만행대)에 붙어 있으면 여기서 멈춘다.
        #:   위쪽 `test_정리가_발생이_가리키는_본문을_지우지_않는다` 와 **같은 방식**이다.
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM policy_clause_occurrence")
            n = cur.fetchone()[0]
        assert n == 0, f"임시 스키마가 아니라 운영 테이블을 보고 있다(발생 {n:,}행) — 중단"

        vec = [0.0] * ix.embed_dim(); vec[0] = 1.0
        #: ★**반쪽**을 만든다 — 본문은 3조각이라는데 조각은 1개만 넣는다.
        ix.upsert_content(conn, [(h, "본문", 3)])
        ix.upsert_chunks(conn, [(h, 0, 3, "조각0", vec)])
        #: 그 해시를 **발생행이 가리킨다** — 지우면 고아가 된다.
        #:   ★`upsert_occurrences` 대신 직접 넣는다 — 임시 스키마에서는
        #:     `ON CONFLICT` 가 걸 제약 이름이 운영 것과 겹쳐 안 잡힌다(위 시험과 같은 이유).
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO policy_clause_occurrence"
                " (content_hash, sha256, insurer, qualified_no, section, title,"
                "  page_from, page_to, index_generation, source_kind)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (h, sha, "보험사", "보통약관/7.", "보통약관", "반쪽", 7, 7, "s6", "clause"),
            )
        conn.commit()

        r = ix.drop_incomplete(conn, scope="all")
        assert r["chunks_deleted"] >= 1, "반쪽 조각을 안 지웠다"
        assert r.get("orphaned_by_drop", 0) >= 1, (
            "조각을 지워 고아를 만들었는데 그 수를 말하지 않았다 — "
            "다음 사람이 「원래 있던 고아」와 구분하지 못한다"
        )
    finally:
        #: ★먼저 롤백한다. 앞에서 터졌으면 트랜잭션이 망가진 채라
        #:   정리 SQL 이 `InFailedSqlTransaction` 으로 또 죽는다 —
        #:   그러면 **임시 스키마가 남는다.**
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("SET search_path TO public")
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()
        conn.close()


@pytest.mark.pg
def test_전역_정리는_의도를_명시해야_돈다():
    """★★`drop_incomplete` 는 **저장소 전체를 훑어 지운다.**

    주석으로는 안 막혔다 — **두 번 터졌다**(조각 43,064개 · 본문 1,506행).
    두 번째는 같은 파일 20줄 위 경고를 읽고도 났다.

    전역 정리 자체는 필요하다(반쪽 벡터를 다시 넣으려면 지워야 한다).
    없애는 대신 **의도를 말하게** 한다 — 실수로는 못 부르고 일부러는 부를 수 있다.

    ★기본값을 「아무것도 안 함」으로 두지 않는다. 조용히 안 하면 호출자는
      정리가 된 줄 안다 — 그게 더 나쁘다. **거절**한다.
    """
    conn = _conn_or_skip()
    try:
        with pytest.raises(ValueError, match='scope="all"'):
            ix.drop_incomplete(conn)          # ← scope 없이
        msg = None
        try:
            ix.drop_incomplete(conn)
        except ValueError as exc:
            msg = str(exc)
        #: ★무엇을 해야 하는지 말해야 한다. 「거절합니다」만으로는 다음 사람이 헤맨다.
        assert "임시 스키마" in msg and "격리" in msg, msg
    finally:
        conn.close()
