"""조항 검색 결과를 **다시 줄 세운다.**

★왜 필요한가 — 벡터만으로는 도메인 안에서 엉뚱한 조항이 올라온다(실측 2026-08-03).

    질의 「치과치료 보철료는 보상하나요」 에 대해 최근접이
    「보상하지 않는 사항 … 고의로 피보험자를 해」 (거리 0.941) 였다.
    거리 하한(`MAX_DISTANCE=1.13`) 아래라 **하한으로는 못 거른다.**

    「보상하지 않는 사항」 같은 **큰 조항**은 여러 주제를 담고 있어
    아무 질의와도 어중간하게 가깝다. 순위를 다시 매겨야 한다.

★**있는 것을 쓴다.** `app/adapters/reranker.LlmReranker` 가 이미 있다.
  커머스 RAG 경로(`Evidence`)에 맞춰져 있을 뿐이라, 여기서 `ClauseHit` 를
  `Evidence` 로 옮겼다가 되돌린다. 채점 로직을 다시 만들지 않는다.

★★**무폴백** — LLM 이 점수를 못 주면 `LLMOutputError` 가 그대로 올라간다.

    조용히 원래 순서로 되돌리지 않는다. 그러면 "재정렬했다"고 믿으면서
    실제로는 안 한 상태가 되고, 그건 **감사 신호 없는 자동 복구**다
    (CLAUDE.md §0). 부르는 쪽이 잡아서 판정을 기권시키거나 재시도해야 한다.

★리랭커는 **근거를 만들지 않는다.** 순서만 바꾼다.
  후보에 없던 조항이 결과에 나타나면 그건 결함이다 — `rerank_hits` 가 검사한다.
"""

from __future__ import annotations

from db.postgres.pgvector_clause_index import ClauseHit


def rerank_hits(
    reranker,
    query: str,
    hits: list[ClauseHit],
    *,
    top_n: int | None = None,
    score_body: str = "chunk",
    score_chars: int = 1200,
) -> list[ClauseHit]:
    """`ClauseHit` 목록을 질의 관련도로 다시 줄 세운다.

    ★★**채점에 무엇을 넣는지가 성능을 가른다** — 실측으로 뒤집힌 자리다(2026-08-05).

        여기 「채점에는 조각이 아니라 조 전체를 넣는다」고 적혀 있었다.
        이유도 적혀 있었다 — 법률문은 예외가 뒤에 오므로 조각만 보면 뜻이 반대로 읽힌다고.
        **그 이유는 맞지만 이 자리에 적용할 이유가 아니었다.**

        417질의 · Qwen3-Reranker-4B 실측:
            조각        hit@1 0.6379
            조 전체     hit@1 0.5875   ← 5.04%p 낮다
        면책을 **다른 말로** 물으면 격차가 +19.81%p 까지 벌어진다(0.6792 ↔ 0.4811).
        조 전체에는 여러 주제가 함께 들어 있어 면책 신호가 묻히기 때문이다.
        `max_length` 를 1536 으로 올려도 차이는 그대로였다 — 절단 탓이 아니다.

        「예외가 뒤에 온다」는 **최종 답이 맞는가**에 관한 것이지
        **순위가 맞는가**에 관한 것이 아니었다. 두 관심사를 한 곳에서 처리하려다
        랭킹을 잃고 있었다.

    ★그래서 **순위는 조각으로, 인용·판정은 조 전체로** 나눈다.
      이 함수는 순서만 바꾸고, 돌려주는 것은 `ClauseHit` 그대로다 —
      부르는 쪽은 여전히 `citable_text` 전체를 근거로 쓴다(`citation_guard`).

    `score_body`
        `"chunk"`        조각(`ClauseHit.text`) — 실측 우세, 기본값
        `"full_clause"`  조 전체(`citable_text`) — 옛 동작. 비교·회귀용으로 남긴다
    """
    from app.application.ports import Evidence

    if not hits:
        return []

    #: `clause_id` 로 되돌린다. 본문으로 되찾으면 같은 내용이 여럿일 때 섞인다
    #: (중복률 66%라 실제로 섞인다 — CLAUDE.md §1).
    by_id = {h.clause_id: h for h in hits}
    if len(by_id) != len(hits):
        #: ★조용히 넘기지 않는다. 같은 `clause_id` 가 둘이면 검색이 이미 이상하다.
        raise ValueError(f"clause_id 가 겹칩니다: {len(hits)}건 중 고유 {len(by_id)}건")

    if score_body not in ("chunk", "full_clause"):
        #: 오타를 조용히 기본값으로 흘리지 않는다 — 어느 쪽으로 쟀는지 모르게 된다.
        raise ValueError(f"score_body 는 chunk|full_clause 여야 합니다: {score_body!r}")

    def _body(h: ClauseHit) -> str:
        """★**대체하지 않는다.** 고른 본문이 비면 멈춘다.

        처음엔 조각이 비면 조 전체로 떨어지게 해 뒀다. 그건 **조용한 폴백**이다
        (코덱스 지적 2026-08-05) — 응답은 `score_body=chunk` 라고 말하는데
        실제로는 조 전체로 채점한 상태가 되고, 그 질의만 다른 기준으로 줄 세워진다.
        어느 본문으로 쟀는지가 결과를 5%p 가르는 마당에 섞이면 안 된다.

        빈 본문은 부르는 쪽(`clause_search`)이 **미리 걸러 세어 보고**한다.
        여기까지 왔다는 것은 거르지 못했다는 뜻이므로 감추지 않고 올린다.
        """
        text = h.text if score_body == "chunk" else h.citable_text
        text = (text or "").strip()
        if not text:
            raise ValueError(
                f"채점할 본문이 비었습니다(score_body={score_body!r}): {h.clause_id}. "
                f"다른 본문으로 대신 채점하지 않습니다 — 질의마다 기준이 달라집니다."
            )
        return text[:score_chars]

    evidence = [
        Evidence(
            content=_body(h),
            source=h.sha256,
            locator=h.clause_id,
            score=0.0,
            backend="clause_index",
        )
        for h in hits
    ]
    ordered = reranker.rerank(query, evidence, top_n=top_n)

    out: list[ClauseHit] = []
    for e in ordered:
        h = by_id.get(e.locator)
        if h is None:
            #: ★리랭커가 **없던 것을 만들어 냈다.** 근거로 쓸 수 없다.
            raise ValueError(f"리랭커가 후보에 없던 조항을 돌려줬습니다: {e.locator!r}")
        out.append(h)
    return out


#: 채점 프롬프트에 넣는 본문 길이. ★조 전체가 3만 자까지 있어 그대로는 못 넣는다.
#:   앞부분만 넣으면 뒤쪽 단서를 못 보므로, **재정렬은 보조 수단**이다 —
#:   최종 인용·판정은 여전히 `citable_text` 전체를 본다.
_SCORE_CHARS = 1200
