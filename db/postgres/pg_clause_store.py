"""★★**세대·모델 격리가 여기에도 걸려 있어야 한다.**

    벡터 검색(`pgvector_clause_index.search`)만 막고 이쪽을 안 막았더니
    같은 테이블의 **옛 세대 행이 이 경로로는 그대로 나왔다**(코덱스 지적 2026-08-03).
    실측 당시 `policy_clause_occurrence` 에 `s5-mixed` 158,186행과
    `s6` 195,617행이 함께 있었다.

    기본 저장소가 `file` 이라 당장 새지는 않았지만 **환경변수 하나 차이**다.
    막는 곳이 하나 빠지면 막은 게 아니다.

조항 조회 — **통합 저장소(PG)** 를 보는 어댑터. `ClauseSourcePort` 구현.

★통합 저장소를 **새로 만들지 않는다.** 인덱스 A 가 그것이다.

    조항 본문이 문서마다 중복 저장돼 있다(등장 211,131 / 고유 73,031 = 중복 65.4%).
    이걸 풀려고 파일 기반 통합 저장소를 하나 더 만들려 했는데, 그러면
    **같은 본문이 세 곳**(추출 산출물 · 파일 통합본 · PG)에 생긴다.
    어긋났을 때 무엇이 맞는지 판단할 근거가 없어진다
    (약관 보관형식 결정에서 표 CSV 를 폐기한 것과 같은 논리).

    그래서 층을 이렇게 나눈다.

        data/structured/…   **산출물**. 추출기가 만든 그대로. 불변. 재생성의 근거
        PG (인덱스 A)        **질의 계층**. 내용 한 벌 + 발생 여러 벌

    `file_clause_store` 는 산출물을 직접 읽고, 이 어댑터는 PG 를 읽는다.
    **둘은 같은 포트를 만족하므로 조립 지점에서 바꿔 끼운다.**

★검색은 **trigram**이다. 임베딩이 아니다.

    `ClauseSourcePort.search(sha256, query)` 는 낱말 질의를 받는다.
    여기서 임베딩을 계산하려면 요청마다 모델을 물어야 한다(최초 로드 수십 초).
    의미 검색이 필요하면 `pgvector_clause_index.search()` 를 벡터로 직접 부른다.
    **어느 쪽을 쓰는지 호출자가 알고 고르게 한다** — 조용히 바꿔치지 않는다.

★약관 버전으로 **가둔다.**

    `sha256` 을 반드시 받는다. 전역 검색을 하면 2019년 가입자에게
    2024년 조항이 근거로 붙는다.
"""

from __future__ import annotations

from typing import Sequence

from db.postgres import pgvector_clause_index as ix
from app.core.errors import InfraError
from app.core.ports.precheck import ClauseRow

#: trigram 유사도 하한. 낮추면 무관한 조항이 근거로 붙는다.
#:
#: ★`similarity()` 를 쓰면 안 된다 — 문자열 **전체**끼리 비교하므로
#:   10자 질의와 800자 조각은 아무리 잘 맞아도 값이 0.02 수준이다.
#:   실제로 "보상하지 않는 손해" 로 찾았더니 **0건**이 나왔다.
#:   `word_similarity(질의, 본문)` 은 질의가 본문의 **어느 부분과** 맞는지를 잰다.
_MIN_SIMILARITY = 0.45


def _conn():
    from db.postgres.pgvector_index import get_conn

    return get_conn()


def ensure_search_index(conn) -> None:
    """조항 본문 trigram 인덱스(멱등)."""
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS policy_clause_chunk_text_trgm "
            "ON policy_clause_chunk USING gin (text gin_trgm_ops)"
        )
    conn.commit()


def _rows_to_clauses(rows) -> list[ClauseRow]:
    """★★**`usable=True` 를 무조건 붙이지 않는다.**

    전에는 PG 에서 읽은 행을 전부 `usable=True` 로 만들었다(코덱스 지적).
    적재 때 걸렀으니 괜찮다고 본 것인데, 적재 게이트 자체가
    `citation_eligible is False` 만 제외해 **필드 부재를 통과**시키고 있었다.
    두 곳이 다 새면 아무 데서도 안 막힌다.

    ★PG 는 아직 `citation_eligible`·`chunk_type`·`is_statute` 를 저장하지 않는다.
      그러면 공통 게이트가 "모른다"로 판정해 **못 쓴다**고 답한다 — 그게 맞다.
      저장하도록 스키마를 늘리기 전까지 PG 경로는 인용 근거로 쓸 수 없다.
    """
    from app.core import release
    from app.core.domain import eligibility

    try:
        rid = release.current().release_id
    except Exception:  # noqa: BLE001
        rid = ""
    out: list[ClauseRow] = []
    for row in rows:
        (content_hash, text, sha, qno, section, title, pfrom, pto) = row[:8]
        #: ★게이트 값이 없으면 `None` — 공통 게이트가 "모른다"로 판정한다.
        gate = dict(zip(("citation_eligible", "chunk_type", "is_statute", "parse_status"),
                        row[8:12])) if len(row) >= 12 else {}
        out.append(
            ClauseRow(
                sha256=sha,
                qualified_no=qno,
                #: `clause_no` 는 `qualified_no` 의 꼬리다. 산출물이 따로 주지 않으므로 파생한다.
                clause_no=qno.split("/")[-1] if qno else "",
                section=section,
                title=title,
                text=text,
                page_from=pfrom,
                page_to=pto,
                content_hash=content_hash,
                #: ★★"적재 때 걸렀으니 괜찮다"고 **가정하지 않는다.**
                #:   적재 게이트가 새고 있었고(필드 부재 통과), 두 곳이 다 새면
                #:   아무 데서도 안 막힌다. 공통 게이트로 다시 판정한다.
                #:   PG 가 eligibility 필드를 아직 저장하지 않으므로 "모른다"가 되고,
                #:   그러면 **못 쓴다**로 나온다 — 그게 정직한 상태다.
                release_id=rid,
                **_gate(qno, text, gate),
            )
        )
    return out


def _gate(qualified_no: str, text: str, stored: dict | None = None) -> dict:
    """공통 게이트로 판정한 결과를 `ClauseRow` 필드로 만든다.

    ★`stored` 는 **DB 에서 읽은** 게이트 값이다. 없으면 `None`(모른다)이고,
      그러면 게이트가 "못 쓴다"로 답한다 — 채워 넣지 않는다(§0).

    ★★전에는 이 함수가 값을 **무조건 `None`** 으로 만들었다.
      그때는 스키마에 그 컬럼이 없었으니 맞는 동작이었다. 컬럼을 붙인 뒤에도
      그대로 두면 **읽어 놓고 안 쓰는** 상태가 된다 — 실측 2026-08-04 에
      `load_clauses()` 가 데이터를 다 갖춰 두고 0건을 돌려줬다.
    """
    from app.core.domain import eligibility

    st = stored or {}
    c = {"citation_eligible": st.get("citation_eligible"),
         "chunk_type": st.get("chunk_type"),
         "is_statute": st.get("is_statute"),
         "qualified_no": qualified_no, "text": text}
    v = eligibility.check(c, parse_status=st.get("parse_status"))
    return {"usable": v.usable, "unusable_reason": v.reason,
            "citation_eligible": st.get("citation_eligible"),
            "chunk_type": st.get("chunk_type"),
            "is_statute": st.get("is_statute"),
            "parse_status": st.get("parse_status")}


def load_clauses(sha256: str, *, usable_only: bool = True) -> list[ClauseRow]:
    """한 약관 문서의 조항 전부.

    ★조각을 **이어 붙이지 않는다.** 본문 한 벌을 그대로 읽는다.

        처음엔 조각을 이어 붙여 복원하려 했는데, 겹침이 **토큰 기준**이라
        글자 수로 잘라 낼 수 없다. 복원을 추측으로 하면 근거 인용문이
        미세하게 틀리고, 그건 인용 검증이 잡아내지 못한다.
        `policy_clause_content` 에 원본을 한 벌 둔다(중복 없음).
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.content_hash, ct.text,
                       o.sha256, o.qualified_no, o.section, o.title, o.page_from, o.page_to,
                       o.citation_eligible, o.chunk_type, o.is_statute, o.parse_status
                FROM policy_clause_occurrence o
                JOIN policy_clause_content ct ON ct.content_hash = o.content_hash
                WHERE o.sha256 = %s
                  AND o.index_generation = %s
                ORDER BY o.page_from, o.qualified_no
                """,
                (sha256, ix.current_generation()),
            )
            fetched = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        raise InfraError(f"조항을 읽지 못했습니다(sha={sha256[:12]}): {exc}") from exc

    if not fetched:
        #: ★"적재 안 됨"과 "인용 불가라 일부러 뺐음"은 **다른 사실**이다.
        #:   섞어 말하면 팀이 적재를 다시 돌리는 헛수고를 한다.
        raise InfraError(
            f"이 약관의 조항 본문이 색인에 없습니다: {sha256[:12]}. "
            "인용 불가(citation_eligible=false)로 제외됐거나 아직 적재 전입니다. "
            "GET /v1/support-manifest 로 대상 여부를 확인하세요."
        )
    rows = _rows_to_clauses(fetched)
    #: ★★`usable_only` 를 **무시하고 있었다**(코덱스 지적). 인자를 받아 놓고
    #:   전 행을 돌려주면 호출부가 "걸러 달라"고 한 뜻이 사라진다.
    #:   ★PG 는 아직 eligibility 필드를 저장하지 않아 전부 "모른다"→못 씀이다.
    #:     그래서 `usable_only=True` 면 **0건**이 나온다 — 그게 정직한 상태다.
    return [r for r in rows if r.usable] if usable_only else rows


def stats(sha256: str) -> dict:
    """이 문서가 인덱스에 얼마나 들어와 있나."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            #: ★**실제로 조회되는 것**을 센다.
            #:
            #:   처음엔 `policy_clause_occurrence` 만 셌더니 어떤 약관이
            #:   "조항 444개" 라고 나오는데 `load_clauses()` 는 0개를 돌려줬다.
            #:   발생 행은 전 문서에 대해 먼저 쌓았고, 본문 임베딩은 그 뒤에
            #:   `citation_eligible` 로 걸러 **161문서만** 넣었기 때문이다.
            #:   실측(2026-08-02): 발생 156,946 중 **91.4%가 본문 없음**.
            #:
            #:   현황이 조회 결과와 어긋나면 그게 더 나쁘다 —
            #:   "있다"고 세어 놓고 못 꺼내면 판정이 근거 없이 기권한다.
            cur.execute(
                """
                SELECT count(*) FILTER (WHERE c.h IS NOT NULL),
                       count(DISTINCT c.h),
                       count(*)
                FROM policy_clause_occurrence o
                LEFT JOIN LATERAL (
                    SELECT k.content_hash AS h FROM policy_clause_chunk k
                    WHERE k.content_hash = o.content_hash
                      AND k.embed_model = %s
                    LIMIT 1
                ) c ON true
                WHERE o.sha256 = %s AND o.index_generation = %s
                """,
                (ix.current_embed_model(), sha256, ix.current_generation()),
            )
            usable, uniq, recorded = cur.fetchone()
    except Exception as exc:  # noqa: BLE001
        raise InfraError(f"조항 현황을 읽지 못했습니다: {exc}") from exc
    if not recorded:
        raise InfraError(f"이 약관의 조항 기록이 없습니다: {sha256[:12]}")
    return {
        #: 조회 가능한 조항 수. `load_clauses()` 와 **같은 기준**이다.
        "clauses": usable,
        "distinct_contents": uniq,
        #: ★기록은 있으나 본문이 없는 것. 숨기지 않는다.
        "recorded_occurrences": recorded,
        "missing_bodies": recorded - usable,
        #: ★★`parse_status: "ok"` 를 **지어내고 있었다**(코덱스 지적).
        #:   PG 에는 그 값이 없다. 없는 것을 "ok" 로 채우면
        #:   호출부가 "이 문서는 파싱됐다"고 믿는다 — 확인한 적이 없는데.
        "parse_status": None,
        "parse_status_note": "PG 색인은 문서 파싱 상태를 저장하지 않는다(모름)",
        "eligibility_fields_stored": False,
        "source": "pg/index_a",
    }


def search(sha256: str, query: str, *, limit: int = 8) -> Sequence[ClauseRow]:
    """약관 **하나 안에서** 낱말로 찾는다(trigram).

    ★유사도 하한을 둔다. 없으면 아무거나 상위에 올라와 근거로 붙는다.
    """
    q = (query or "").strip()
    if not q:
        return []
    try:
        with _conn() as conn, conn.cursor() as cur:
            #: ★**빈 결과의 이유를 구분한다.** 승인 세대·모델이 색인에 0건이면
            #:   어떤 낱말을 넣어도 0건이다. 그걸 "못 찾았다"로 내보내면
            #:   판정이 근거 없이 기권하고 **원인은 감춰진다**(실측 2026-08-03).
            ix.ensure_index_matches_release(conn)
            cur.execute(
                """
                WITH ranked AS (
                    SELECT DISTINCT ON (o.content_hash, o.qualified_no, o.page_from)
                           o.content_hash, ct.text, o.sha256, o.qualified_no,
                           o.section, o.title, o.page_from, o.page_to,
                           word_similarity(%(q)s, c.text) AS sim
                    FROM policy_clause_chunk c
                    JOIN policy_clause_occurrence o ON o.content_hash = c.content_hash
                    JOIN policy_clause_content ct ON ct.content_hash = c.content_hash
                    WHERE o.sha256 = %(sha)s
                      AND o.index_generation = %(gen)s
                      AND c.embed_model = %(model)s
                      AND word_similarity(%(q)s, c.text) >= %(min)s
                    ORDER BY o.content_hash, o.qualified_no, o.page_from, sim DESC
                )
                --: ★정렬을 **바깥에서** 다시 한다.
                --:   `DISTINCT ON` 은 그룹 키 순서로 정렬해야 하므로,
                --:   그 상태로 LIMIT 을 걸면 유사도 상위가 아니라
                --:   **해시 순서 앞쪽**이 나온다. (코덱스 지적 2026-08-02)
                SELECT * FROM ranked ORDER BY sim DESC LIMIT %(k)s
                """,
                {"q": q, "sha": sha256, "min": _MIN_SIMILARITY, "k": limit,
                 "gen": ix.current_generation(), "model": ix.current_embed_model()},
            )
            fetched = cur.fetchall()
    except InfraError:
        #: ★이미 원인을 말하고 있는 오류를 **다시 감싸지 않는다.**
        #:   "조항 검색에 실패했습니다: 색인이 승인 릴리스와 맞지 않습니다…" 는
        #:   질의가 실패한 것처럼 읽힌다. 실제로는 설정과 색인이 어긋난 것이다.
        raise
    except Exception as exc:  # noqa: BLE001
        raise InfraError(f"조항 검색에 실패했습니다: {exc}") from exc
    return _rows_to_clauses([r[:8] for r in fetched])


__all__ = ["ensure_search_index", "load_clauses", "search", "stats"]
