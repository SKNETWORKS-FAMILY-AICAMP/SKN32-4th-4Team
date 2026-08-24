"""인덱스 A — 약관 조항 벡터 색인.

★PG 없이 도는 것만 여기 둔다. 실제 적재·검색은 PG 가 떠 있을 때만 돈다.
  PG 를 요구하는 테스트를 무조건 통과시키지 않고 **건너뛴다고 말한다.**
"""

from __future__ import annotations

import pytest

from app.adapters import pgvector_clause_index as ix

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
    from app.adapters.pgvector_index import get_conn

    try:
        return get_conn()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG 없음 — 건너뜀: {str(exc)[:80]}")


@pytest.mark.pg
def test_스키마와_적재와_검색이_이어진다():
    conn = _conn_or_skip()
    ix.ensure_schema(conn)

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

    with conn.cursor() as cur:
        cur.execute("DELETE FROM policy_clause_chunk WHERE content_hash = %s", (h,))
        cur.execute("DELETE FROM policy_clause_content WHERE content_hash = %s", (h,))
        cur.execute("DELETE FROM policy_clause_occurrence WHERE content_hash = %s", (h,))
    conn.commit()
    conn.close()


@pytest.mark.pg
def test_반쪽으로_남은_조항은_완료로_치지_않는다():
    """★조각 하나만 들어가도 "완료"로 보던 버그.

    배치 중간에 죽으면 나머지 조각이 영구 누락되고 다음 실행이 건너뛴다.
    실측(2026-08-02 중단 지점): 내용 12,507개 중 2개가 그렇게 잘려 있었다.
    """
    conn = _conn_or_skip()
    ix.ensure_schema(conn)
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
