"""통합 조항 저장소(PG) — 파일 어댑터와 **같은 포트**를 만족하는가.

★두 구현이 갈라지면 조립 지점에서 바꿔 끼울 수 없다.
  포트 준수는 PG 없이도 확인할 수 있으므로 여기서 먼저 막는다.
"""

from __future__ import annotations

import pytest

from app.adapters import pg_clause_store
from app.core.ports.precheck import ClauseSourcePort


def test_파일_어댑터와_같은_포트를_만족한다():
    from app.adapters import file_clause_store

    assert isinstance(pg_clause_store, ClauseSourcePort)
    assert isinstance(file_clause_store, ClauseSourcePort)


def test_빈_질의는_검색하지_않는다():
    #: ★conn 을 열기 전에 걸러야 한다. PG 가 없어도 터지지 않아야 한다.
    assert pg_clause_store.search("a" * 64, "   ") == []


def test_조립_지점이_기본으로_파일을_고른다(monkeypatch):
    """★기본이 PG 면, 적재 안 된 기계에서 판정이 통째로 죽는다.

    인덱스 A 적재는 CPU 로 4~5시간이라 기계마다 상태가 다르다.
    쓰려면 `CLAUSE_STORE=pg` 로 명시하게 한다.

    ★★계약이 하나 더 붙었다(2026-08-03) — **명시만으로는 부족하고
      승인된 임베딩 프로필이 있어야 한다.** 벡터가 없는데 PG 를 고르면
      "근거 없음"과 "적재 안 함"을 구분할 수 없다.
    """
    import app.composition as comp

    #: ★`importlib.reload` 를 쓰지 않는다. 조립 지점이 **부를 때** 환경을 읽는다 —
    #:   reload 로 우회하던 옛 방식은 모듈 상태를 남겨 뒤 테스트를 오염시켰다.
    monkeypatch.delenv("CLAUSE_STORE", raising=False)
    assert comp.build_precheck()["clauses"].__name__.endswith("file_clause_store")


def test_승인_프로필이_있으면_pg_를_고른다(monkeypatch):
    """명시 + 승인된 프로필이면 PG 를 고른다. 계약의 나머지 절반."""
    import app.composition as comp
    from app.core import release

    real = release.load()
    with_profile = release.AcceptedRelease(
        release_id=real.release_id, page_tag=real.page_tag,
        clause_tag=real.clause_tag, document_count=real.document_count,
        embed_profile=release.EmbedProfile(
            model="fake/model", dim=768, max_seq_length=512,
            chunk_budget=448, overlap=80),
    )
    monkeypatch.setattr(release, "load", lambda *a, **k: with_profile)
    monkeypatch.setenv("CLAUSE_STORE", "pg")
    assert comp.build_precheck()["clauses"].__name__.endswith("pg_clause_store")


# ---------------------------------------------------------------- PG 필요


def _conn_or_skip():
    from app.adapters.pgvector_index import get_conn

    try:
        return get_conn()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG 없음 — 건너뜀: {str(exc)[:80]}")



def _sha_with_current_index(conn):
    """**현재 세대 × 현재 임베딩 프로필**로 색인된 문서 하나를 고른다.

    ★셋업이 세대·모델을 안 보면 옛 프로필로 색인된 문서를 골라 놓고
      필터가 걸린 함수를 부른다 — 결과 0건이 나오고, 그걸 "검색이 고장났다"로
      오독하게 된다(실제로 그랬다: `@128` 벡터만 있는 상태에서 0건).

    ★없으면 **사유를 적어** 건너뛴다. 조용한 스킵을 만들지 않는다(CLAUDE.md §3).
    """
    from app.adapters import pgvector_clause_index as ix

    with conn.cursor() as cur:
        cur.execute(
            """SELECT o.sha256 FROM policy_clause_occurrence o
               JOIN policy_clause_chunk c ON c.content_hash = o.content_hash
               WHERE o.index_generation = %s AND c.embed_model = %s
               GROUP BY 1 ORDER BY count(*) DESC LIMIT 1""",
            (ix.current_generation(), ix.current_embed_model()),
        )
        row = cur.fetchone()
    return row[0] if row else None


@pytest.mark.pg
def test_없는_약관은_조용히_빈_결과를_주지_않는다():
    """★없는 것을 `[]` 로 돌려주면 "조항이 없는 약관"으로 읽힌다.

    실제로는 **적재가 안 된 것**이다. 둘을 섞으면 판정이 근거 없이 기권한다.
    """
    from app.core.errors import InfraError

    _conn_or_skip().close()
    with pytest.raises(InfraError):
        pg_clause_store.load_clauses("f" * 64)


@pytest.mark.pg
def test_적재된_약관을_읽고_찾는다():
    from app.adapters import pgvector_clause_index as ix

    conn = _conn_or_skip()
    ix.ensure_schema(conn)
    pg_clause_store.ensure_search_index(conn)

    sha = "e" * 64
    h = "통합저장소테스트해시"
    #: ★차원은 승인 프로필에서 온다(상수 금지 — test_clause_index.py 의 같은 자리 주석 참조).
    vec = [0.0] * ix.embed_dim()
    vec[3] = 1.0
    body = "회사는 피보험자가 상해로 인하여 의료기관에 입원하여 치료를 받은 경우 보상합니다."
    ix.upsert_content(conn, [(h, body, 1)])
    ix.upsert_chunks(conn, [(h, 0, 1, body, vec)])
    #: ★★게이트 값 **없이** 먼저 넣는다. 이 테스트가 지켜야 할 성질이 그것이다 —
    #:   `eligibility` 규칙은 "모르면 못 쓴다"(fail-closed)다. 전에는 8튜플만 넣고
    #:   바로 `len(rows) == 1` 을 기대했는데, 게이트가 생긴 뒤로는 그게 **틀린 기대**다.
    #:   0건이 나온 것은 결함이 아니라 **설계대로 막힌 것**이었다(2026-08-04).
    ix.upsert_occurrences(conn, [(h, sha, "테스트보험", "보통약관/3.", "보통약관", "보상내용", 7, 7)])
    conn.commit()

    assert pg_clause_store.load_clauses(sha) == [], (
        "인용 게이트 값이 없는데 조항이 근거로 나왔다 — fail-open 이다."
    )
    unfiltered = pg_clause_store.load_clauses(sha, usable_only=False)
    assert len(unfiltered) == 1 and unfiltered[0].usable is False
    assert "모른다" in unfiltered[0].unusable_reason

    #: ★게이트를 채우면(같은 키라 ON CONFLICT 로 갱신된다) 비로소 근거가 된다.
    ix.upsert_occurrences(
        conn,
        [(h, sha, "테스트보험", "보통약관/3.", "보통약관", "보상내용", 7, 7, "clause",
          {"citation_eligible": True, "chunk_type": None,
           "is_statute": False, "parse_status": "ok"})],
    )
    conn.commit()

    rows = pg_clause_store.load_clauses(sha)
    assert len(rows) == 1
    assert rows[0].text == body
    #: 인용 식별자 규칙이 파일 어댑터와 같아야 한다.
    assert rows[0].clause_id.startswith("eeeeeeeeeeee/보통약관/3.#")

    st = pg_clause_store.stats(sha)
    assert st["clauses"] == 1 and st["distinct_contents"] == 1

    hits = pg_clause_store.search(sha, "상해로 인하여 의료기관에 입원")
    assert hits and hits[0].content_hash == h
    #: ★다른 약관으로는 안 나온다.
    assert pg_clause_store.search("d" * 64, "상해로 인하여 의료기관에 입원") == []

    with conn.cursor() as cur:
        cur.execute("DELETE FROM policy_clause_chunk WHERE content_hash = %s", (h,))
        cur.execute("DELETE FROM policy_clause_content WHERE content_hash = %s", (h,))
        cur.execute("DELETE FROM policy_clause_occurrence WHERE content_hash = %s", (h,))
    conn.commit()
    conn.close()


@pytest.mark.pg
def test_짧은_질의가_긴_조각에서_찾아진다():
    """★`similarity()` 로는 **0건**이 나왔다.

    `similarity()` 는 문자열 **전체**끼리 비교하므로 10자 질의와 800자 조각은
    아무리 잘 맞아도 0.02 수준이다. 실측 —

        "보상하지 않는 손해"  similarity 0.066 · word_similarity 0.727
        "보험금의 지급사유"   similarity 0.106 · word_similarity 1.000

    `word_similarity(질의, 본문)` 은 질의가 본문의 **어느 부분과** 맞는지를 잰다.
    검색이 조용히 0건을 돌려주면 판정은 근거 없이 기권한다 — 고장이 안 보인다.
    """
    from app.adapters import pgvector_clause_index as ix

    conn = _conn_or_skip()
    sha = _sha_with_current_index(conn)
    conn.close()
    if not sha:
        pytest.skip(
            f"현재 프로필(세대 {ix.current_generation()} × 모델 "
            f"{ix.current_embed_model()})로 색인된 문서가 없습니다 — 적재 미실행"
        )
    hits = pg_clause_store.search(sha, "보험금의 지급사유", limit=5)
    assert hits, "짧은 질의가 0건입니다 — similarity/word_similarity 를 확인하세요"


@pytest.mark.pg
def test_현황이_조회_결과와_어긋나지_않는다():
    """★"조항 444개" 라고 세어 놓고 `load_clauses()` 가 0개를 돌려준 적이 있다.

    발생 행은 전 문서에 먼저 쌓고 본문은 `citation_eligible` 로 걸러 넣었기
    때문이다. 실측(2026-08-02): 발생 156,946 중 **91.4%가 본문 없음**.
    현황이 조회 결과와 어긋나면 판정이 근거 없이 기권한다.
    """
    from app.adapters import pgvector_clause_index as ix

    conn = _conn_or_skip()
    sha = _sha_with_current_index(conn)
    conn.close()
    if not sha:
        pytest.skip(
            f"현재 프로필(세대 {ix.current_generation()} × 모델 "
            f"{ix.current_embed_model()})로 색인된 문서가 없습니다 — 적재 미실행"
        )
    st = pg_clause_store.stats(sha)
    rows = pg_clause_store.load_clauses(sha)
    assert st["clauses"] == len(rows), (
        f"stats 는 {st['clauses']}개라는데 실제로는 {len(rows)}개가 나옵니다"
    )
    assert "missing_bodies" in st, "본문 없는 기록 수를 숨기면 안 됩니다"


def test_세대_필터가_모든_조회_경로에_걸려_있다():
    """★★**막는 곳이 하나 빠지면 막은 게 아니다.**

    벡터 검색(`pgvector_clause_index.search`)에만 세대 격리를 넣고
    이 저장소를 빠뜨렸었다. 같은 테이블의 옛 세대 행이 이쪽으로는
    **그대로 나왔다**(코덱스 지적 2026-08-03).

    실측 당시 `policy_clause_occurrence` 에 `s5-mixed` 158,186행과
    `s6` 195,617행이 함께 있었다. 세대가 섞이면 부록을 삼킨 옛 조항이
    판정 근거로 붙는다 — KCD 오인용이다.

    ★PG 없이 확인한다. SQL 문자열에 필터가 들어 있는지만 본다 —
      DB 를 띄워야만 도는 테스트는 CI 에서 조용히 건너뛰어진다.
    """
    import inspect
    import re

    src = inspect.getsource(pg_clause_store)
    #: `policy_clause_occurrence` 를 읽는 SQL 은 전부 세대를 봐야 한다.
    reads = len(re.findall(r"FROM policy_clause_occurrence", src))
    gens = len(re.findall(r"index_generation", src))
    assert reads > 0, "조회 경로를 못 찾았습니다 — 테스트가 낡았습니다"
    assert gens >= reads, (
        f"`policy_clause_occurrence` 조회 {reads}곳 중 "
        f"세대 필터는 {gens}곳입니다. 빠진 경로가 있습니다."
    )
    #: 조각을 읽는 곳은 **모델**도 봐야 한다. 벡터 공간이 다르면 거리가 뜻을 잃는다.
    chunk_reads = len(re.findall(r"policy_clause_chunk", src))
    models = len(re.findall(r"embed_model", src))
    assert models >= chunk_reads, (
        f"`policy_clause_chunk` 조회 {chunk_reads}곳 중 모델 필터는 {models}곳입니다."
    )


def test_현황이_현재_세대만_센다():
    """★`stats()` 가 전 세대를 합쳐 세면 **적재가 끝난 것처럼 보인다.**

    실측: 조각 46,385개가 전부 128토큰에서 잘린 모델의 것이었는데
    합계만 보면 "색인 있음"으로 읽힌다.
    """
    import inspect

    src = inspect.getsource(pg_clause_store.stats)
    assert "index_generation" in src
    assert "embed_model" in src
