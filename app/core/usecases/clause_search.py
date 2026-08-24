"""조항 의미검색 — **근거 후보를 찾을 뿐, 판정하지 않는다.**

★이 유스케이스가 생긴 이유 (2026-08-04)
  조항 벡터 색인이 122,772조각 적재돼 있는데 **서비스 어디서도 조회하지 않았다.**
  리랭커도 커머스 RAG 경로에만 붙어 있어, 보험 쪽에는 재정렬할 대상 자체가 없었다.
  그래서 검색 호출부를 먼저 만든다 — 리랭커 배선은 그다음이다.

★**판정이 아니다.** 여기 결과에 「보장된다/안 된다」를 뜻하는 필드를 두지 않는다.
  검색은 근거 후보를 고르는 일이고, 보장 여부는 `precheck` 가 약관 조항으로 정한다.

★**범위를 명시하게 한다.** `scope_sha256s=None` 은 전역 검색이고,
  서로 다른 상품·세대의 조항이 섞이면 그럴듯하지만 틀린 결과가 나온다.
  그래서 전역은 `allow_global=True` 를 **따로** 받아야 열린다(코덱스 지적).

★**무폴백.** 리랭커가 실패하면 벡터 순서로 조용히 되돌리지 않는다.
  되돌리면 "재정렬했다"고 믿으면서 실제로는 안 한 상태가 된다(CLAUDE.md §0).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import ValidationErr


class RerankUnavailable(RuntimeError):
    """리랭킹을 요청했지만 할 수 없었다. **조용히 넘기지 않는다.**"""


@dataclass(frozen=True)
class ClauseSearchResult:
    hits: list
    reranked: bool
    #: 어느 색인·프로필로 찾았는지. 없으면 결과를 재현할 수 없다.
    provenance: dict = field(default_factory=dict)
    #: 본문이 없어 제외한 조각 수. 0 이 아니면 적재가 반쪽이라는 신호다.
    dropped_incomplete: int = 0
    #: 채점할 본문(`score_body`)이 비어 재정렬에서 뺀 수.
    #: ★다른 본문으로 대신 채점하지 않는다 — 그러면 질의마다 기준이 달라진다.
    dropped_unscorable: int = 0


def search(
    *,
    index,
    rerank_fn,
    conn,
    embedder,
    query: str,
    scope_sha256s: list[str] | None,
    allow_global: bool = False,
    final_k: int = 8,
    candidate_k: int | None = None,
    reranker=None,
    max_candidates: int = 40,
    score_body: str = "chunk",
    score_chars: int = 1200,
) -> ClauseSearchResult:
    """조항을 찾아 (선택적으로) 다시 줄 세운다.

    `candidate_k` 는 리랭커에 넣을 후보 수다. `final_k` 만큼만 뽑아 재정렬하면
    벡터가 놓친 것을 리랭커가 되살릴 수 없다 — **재현율이 거기서 끝난다**(코덱스 지적).

    ★`index`·`rerank_fn` 을 **받아서** 쓴다. 유스케이스가 어댑터를 직접 부르면
      의존 방향이 뒤집힌다(ARCH-002·003). 처음엔 안에서 import 했다가
      아키텍처 테스트에 걸렸다 — 규칙이 맞았다. 조립은 `app/composition.py` 가 한다.
    """
    ix, rerank_hits = index, rerank_fn

    if not (query or "").strip():
        raise ValidationErr("질의가 비었습니다")
    if final_k < 1:
        raise ValidationErr("final_k 는 1 이상이어야 합니다")
    if scope_sha256s is None and not allow_global:
        #: ★기본값으로 전역을 열지 않는다. 「관리자니까 전역」은 안전성 근거가 아니다.
        raise ValidationErr(
            "약관 범위(scope_sha256s)를 넘기거나 allow_global=True 를 명시해야 합니다. "
            "범위를 안 정하면 다른 상품·세대의 조항이 섞입니다."
        )

    want = candidate_k if candidate_k is not None else max(final_k * 4, 20)
    want = max(final_k, min(want, max_candidates))

    hits = ix.search(conn, embedder.encode(query), sha256s=scope_sha256s, limit=want)

    #: ★조 전체(`full_text`)가 없는 조각은 재정렬에 넣지 않는다.
    #:   법률문은 예외가 뒤에 오므로 조각만 채점하면 뜻이 반대로 읽힌다.
    #:   빠뜨린 수를 세어 함께 돌려준다 — 조용한 스킵을 만들지 않는다(CLAUDE.md §3).
    usable = [h for h in hits if (h.full_text or "").strip()]
    dropped = len(hits) - len(usable)

    provenance = {
        "index_generation": ix.current_generation(),
        "index_embed_model": ix.current_embed_model(),
        "query_embed_profile": embedder.profile_key,
        "candidates_requested": want,
        "candidates_found": len(hits),
        "scope": "global" if scope_sha256s is None else f"{len(scope_sha256s)}개 약관",
        #: ★어느 본문으로 순위를 매겼는지 남긴다. 이 값이 결과를 5%p 가른다.
        "rerank_score_body": score_body if reranker is not None else None,
    }

    if reranker is None:
        return ClauseSearchResult(usable[:final_k], False, provenance, dropped)

    #: ★채점할 본문이 빈 후보를 **미리 걸러 센다.** 어댑터에서 다른 본문으로
    #:   대신 채점하면 그 질의만 다른 기준으로 줄 세워지고, 응답은 그 사실을 말하지 못한다.
    #:   한 건 때문에 요청 전체를 503 으로 떨어뜨리지도 않는다 — 빼고, 센다.
    def _body_of(h) -> str:
        return ((h.text if score_body == "chunk" else h.citable_text) or "").strip()

    scorable = [h for h in usable if _body_of(h)]
    unscorable = len(usable) - len(scorable)
    provenance["candidates_scorable"] = len(scorable)

    try:
        ordered = rerank_hits(reranker, query, scorable, top_n=final_k,
                              score_body=score_body, score_chars=score_chars)
    except Exception as exc:  # noqa: BLE001 — 원인을 감추지 않고 그대로 올린다
        raise RerankUnavailable(f"{type(exc).__name__}: {exc}") from exc
    return ClauseSearchResult(ordered, True, provenance, dropped, unscorable)
