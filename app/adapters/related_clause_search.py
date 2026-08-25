"""판정에 붙는 **참고 조항** 검색 — 벡터 색인을 판정 경로에 처음으로 잇는다.

★왜 이제야 붙나 (2026-08-25)

    조항 벡터 122,772조각이 적재돼 있는데 `/v1/prechecks` 는 **한 번도 안 봤다.**
    판정은 「질병기호가 적힌 조항을 찾아 면책 범위와 대조」로만 했고, 그 결과
    대부분의 답이 이렇게 끝났다 —

      「면책 조항에는 없습니다. 다만 보장 여부는 '보상하는 사항' 조항이 정하므로…」

    맞는 말인데, **그 조항을 보여 주지 않았다.** 사람은 어디를 읽어야 할지 모른 채
    "확인 불가"만 받는다. 이 어댑터가 그 자리를 메운다.

★★**판정을 바꾸지 않는다.** 유사도는 근거가 아니다.
    · 여기서 나온 조항은 `related_clauses` 로만 나가고 `citations` 에 안 섞인다.
    · 급도 다르다 — `EvidenceTier.RETRIEVED_CLAUSE`.
    · 그래서 이 어댑터가 무엇을 물어 오든(심지어 헛것을 물어 와도)
      보장 판정 자체는 동일하다. 시험이 그것을 잰다.

★범위를 **약관 한 벌로 가둔다.** 전역 검색을 열지 않는다 —
  2019년 가입자에게 2024년 조항이 참고로 붙으면 참고라도 틀린 참고다.

★무폴백. 색인이 없거나 임베더가 못 서면 **예외를 올린다.**
  빈 목록으로 돌려주면 호출부가 「관련 조항 없음」이라고 읽는다 — 다른 말이다.

★기본은 꺼져 있다(`PRECHECK_RELATED_SEARCH_ENABLED=false`).
  판정 한 건마다 임베딩 1회 + 벡터 조회 1회가 늘고, 이 값이 얼마나 도움이 되는지는
  아직 실측하지 않았다. 켜는 근거가 생기면 그때 켠다.
"""

from __future__ import annotations

from typing import Sequence

from app.core.ports.precheck import ClauseRow


def _to_row(h) -> ClauseRow:
    """`ClauseHit` → `ClauseRow`. **없는 값을 지어내지 않는다.**

    ★`text` 에는 `citable_text`(조 전체)를 넣는다. 조각만 실으면
      "…보상합니다" 까지만 남아 뜻이 반대로 읽힌다(법률문은 예외가 뒤에 온다).
    ★`usable` 을 `True` 로 박지 않는다 — 여기 오는 것은 판정 재료가 아니므로
      게이트 값을 아는 척할 이유가 없다. 모르는 것은 `None` 으로 둔다.
    """
    return ClauseRow(
        sha256=h.sha256,
        qualified_no=h.qualified_no,
        clause_no=h.qualified_no,
        section=h.section,
        title=h.title,
        text=h.citable_text,
        page_from=h.page_from,
        page_to=h.page_to,
        content_hash=h.content_hash,
        citation_eligible=None,
        parse_status=None,
    )


class PgVectorRelatedClauses:
    """pgvector 색인으로 참고 조항을 찾는다(`RelatedClausePort`)."""

    def __init__(self, index=None, embedder_factory=None, conn_factory=None) -> None:
        #: ★생성자에서 임베더를 만들지 않는다. 무게추를 여기서 올리면
        #:   `build_precheck()` 만 불러도 모델이 뜬다 — 조립은 싸야 한다.
        self._index = index
        self._embedder_factory = embedder_factory
        self._conn_factory = conn_factory
        self._embedder = None

    def _deps(self):
        if self._index is None:
            from db.postgres import pgvector_clause_index

            self._index = pgvector_clause_index
        if self._conn_factory is None:
            from db.postgres.pgvector_index import get_conn

            self._conn_factory = get_conn
        if self._embedder is None:
            if self._embedder_factory is None:
                from app.adapters import clause_query_embedder

                self._embedder_factory = clause_query_embedder.build
            #: 한 번 만들어 붙들고 있는다 — 판정마다 다시 만들면 그 비용이 요청에 든다.
            self._embedder = self._embedder_factory()
        return self._index, self._conn_factory, self._embedder

    def find(self, sha256: str, query: str, *, limit: int = 5) -> Sequence[ClauseRow]:
        if not (sha256 or "").strip():
            #: ★범위 없는 호출을 전역 검색으로 바꾸지 않는다. 그건 조용한 확대다.
            raise ValueError("참고 조항 검색에는 약관 sha256 이 있어야 합니다")
        if not (query or "").strip():
            raise ValueError("참고 조항 검색 질의가 비었습니다")

        index, get_conn, embedder = self._deps()
        vec = embedder.encode(query)
        with get_conn() as conn:
            hits = index.search(conn, vec, sha256s=[sha256], limit=max(1, limit))

        #: 조 전체가 없는 조각은 뺀다. 조각만으로는 뜻이 뒤집힌다.
        #:   ★조용히 빼지 않고 수를 남길 자리가 지금 포트에 없다 —
        #:     `related_search` 가 "ok" 로만 나가므로, 여기서 뺀 수는
        #:     서버 로그로 보낸다(판정에 영향이 없어 응답 계약을 늘리지 않았다).
        usable = [h for h in hits if (h.citable_text or "").strip()]
        if len(usable) != len(hits):
            import logging

            logging.getLogger(__name__).info(
                "참고 조항 %d건 중 %d건은 조 전체 본문이 없어 제외했습니다(sha=%s)",
                len(hits), len(hits) - len(usable), sha256[:12],
            )
        return [_to_row(h) for h in usable]


def build():
    """설정이 켜져 있으면 포트를 만들고, 아니면 `None`.

    ★`None` 은 「참고 조항을 안 붙인다」는 뜻이고, 판정은 그대로 난다.
      스위치를 끈 것과 검색이 실패한 것은 다른 상태다 — 섞지 않는다.
    """
    from app.core.config import get_settings

    if not get_settings().PRECHECK_RELATED_SEARCH_ENABLED:
        return None
    return PgVectorRelatedClauses()


__all__ = ["PgVectorRelatedClauses", "build"]
