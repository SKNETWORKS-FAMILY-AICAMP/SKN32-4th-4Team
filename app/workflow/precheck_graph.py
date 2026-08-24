"""보장 사전판정 흐름 — LangGraph.

계약: `docs/handoff/06_계약_Agent.md` §1

★계약서는 이 파일을 `app/insurance/graph.py` 로 적어 두었지만 **거기 두지 않는다.**
  `ARCH-003` 이 `app/core/{domain,ports,usecases}` 밖의 도메인 패키지를 막는다.
  앞서 `app/insurance/` 가 core 를 복제하다가 정리된 자리다.
  그래프는 도메인이 아니라 **오케스트레이션**이므로 바깥 계층에 둔다 —
  같은 폴더에 `ticket_graph.py` 선례가 있다.

```
정규화 → resolve_policy → gate_document → retrieve → assess → explain
                                                       ↓
                                            verify_citations ★재시도 지점
                                              ├ 통과            → 완료
                                              ├ 조항번호 오류   → 설명문만 1회 수정
                                              ├ 근거 부족       → 표적 검색 1회
                                              └ 재시도 초과     → 기권(CITATION_UNVERIFIED)
```

★**이 그래프는 도메인 판단을 하지 않는다.**

    `verdict` 를 여기서 바꾸지 않는다. 규칙엔진이 정한 것을 **그대로 나른다.**
    노드를 잇고, 분기하고, 재시도를 통제하는 것이 전부다.
    여기서 판단을 시작하면 규칙엔진·인용검증을 우회한 답이 나간다.

★**자율 ReAct 루프를 만들지 않는다.**

    언제 끝날지 모르고 감사가 안 된다. 노드와 분기를 **미리 다 적어 둔다.**
    재시도는 **2회를 넘지 않는다** — 그때까지 안 되면 근거가 없는 것이다.

★**상태에 원문 개인정보를 담지 않는다.**

    질병기호는 민감정보다. 상태에는 **해시**로 넣고, 원문은 입력 객체 안에만 둔다.
    상태는 로그·트레이스로 새어 나갈 수 있다.

★**LangGraph 가 없어도 돈다.**

    저장소에 `langgraph` 가 고정돼 있지만, 없는 환경에서 판정이 통째로 죽으면 안 된다.
    같은 노드·같은 분기를 그대로 실행하는 순차 실행기를 함께 둔다 —
    **폴백이 아니다.** 흐름이 동일함을 `tests/test_graph.py` 가 강제한다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.domain.precheck_result import PrecheckInput, PrecheckOutcome, ReasonCode, Verdict

#: ★재시도 상한. 계약 §1 — 2회를 넘지 않는다.
MAX_RETRY = 2

#: 노드 이름. 트레이스·테스트가 이 문자열을 본다.
NODES = (
    "normalize",
    "resolve_policy",
    "gate_document",
    "retrieve",
    "assess",
    "explain",
    "verify_citations",
)


def _hash_code(code: str) -> str:
    """질병기호를 상태에 담기 전에 해시한다.

    ★원문을 그대로 두면 트레이스·로그에 민감정보가 남는다.
      짧게 자르는 것은 사람이 눈으로 대조할 수 있게 하기 위함이고,
      되돌릴 수 없다는 성질은 그대로다.
    """
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()[:16]


@dataclass
class GraphState:
    """노드 사이를 오가는 상태. **원문 개인정보를 담지 않는다.**"""

    #: 질병기호 **해시**. 원문은 여기 없다.
    kcd_hashes: tuple[str, ...] = ()
    insurer: str = ""
    enrolled_on: str = ""

    policy: Any = None
    clauses: tuple = ()
    outcome: PrecheckOutcome | None = None

    #: 지나온 노드. 감사와 테스트가 본다.
    trail: list[str] = field(default_factory=list)
    #: `verify_citations` 에서 되돌아온 횟수.
    retries: int = 0
    #: 무엇 때문에 되돌아왔나. 같은 이유로 두 번 돌지 않는다.
    retry_reasons: list[str] = field(default_factory=list)
    #: 그래프가 끝났나.
    done: bool = False

    def visit(self, node: str) -> None:
        self.trail.append(node)


class PrecheckGraph:
    """판정 흐름. 노드 구현은 **주입받는다.**

    ★그래프가 어댑터를 직접 import 하지 않는다.
      그러면 흐름과 저장소가 얽혀 어느 쪽도 따로 시험할 수 없다.
    """

    def __init__(
        self,
        *,
        run_precheck: Callable[[PrecheckInput], PrecheckOutcome],
        verify: Callable[[PrecheckOutcome], tuple[bool, ReasonCode | None, str]],
        retarget: Callable[[PrecheckInput, PrecheckOutcome], PrecheckOutcome] | None = None,
    ):
        #: 규칙엔진 전체(resolve→gate→retrieve→assess)를 한 덩어리로 받는다.
        #: ★쪼개서 다시 조립하면 유스케이스와 그래프에 **같은 판단이 두 벌** 생긴다.
        self._run = run_precheck
        self._verify = verify
        self._retarget = retarget

    # ── 노드 ────────────────────────────────────────────────────────

    def normalize(self, st: GraphState, body: PrecheckInput) -> GraphState:
        st.visit("normalize")
        st.kcd_hashes = tuple(_hash_code(c) for c in body.kcd_codes)
        st.insurer = body.insurer
        st.enrolled_on = body.enrolled_on
        return st

    def run_rules(self, st: GraphState, body: PrecheckInput) -> GraphState:
        """resolve_policy → gate_document → retrieve → assess → explain.

        ★이 넷은 유스케이스가 이미 한 덩어리로 한다. 그래프가 쪼개서 다시 하면
          **같은 판단이 두 곳**에 생기고 반드시 어긋난다.
          그래서 여기서는 **부르고, 결과가 어느 단계에서 멈췄는지만 기록**한다.
        """
        for n in ("resolve_policy", "gate_document", "retrieve", "assess", "explain"):
            st.visit(n)
        st.outcome = self._run(body)
        st.policy = st.outcome.applied_policy
        st.clauses = tuple(st.outcome.citations)
        return st

    def verify_citations(self, st: GraphState, body: PrecheckInput) -> GraphState:
        """★재시도 지점. 여기서만 되돌아온다."""
        st.visit("verify_citations")
        assert st.outcome is not None

        #: ★인용 0건은 fail-closed 다.
        #:
        #:   전에는 `if not citations: return True` 였다 — **근거 없는 양성 판정이
        #:   그대로 통과했다.** 판정을 했다면 근거를 대야 하고, 못 대면 기권이다.
        #:   다만 **이미 기권한 결과를 덮지 않는다** — 규칙엔진이 정한 사유
        #:   (`no_evidence`·`ambiguous_product` 등)를 `citation_unverified` 로
        #:   바꿔치면 왜 기권했는지가 사라진다.
        if not st.outcome.citations:
            if not st.outcome.abstained:
                st.outcome = self._abstain(st.outcome, "판정에 근거 조항이 붙지 않았습니다")
                st.clauses = ()
            st.done = True
            return st

        ok, code, msg = self._verify(st.outcome)
        if ok:
            st.done = True
            return st

        reason = code.value if code else "unknown"

        #: ★같은 이유로 두 번 돌지 않는다. 돌아도 같은 결과가 나온다.
        if reason in st.retry_reasons or st.retries >= MAX_RETRY:
            st.outcome = self._abstain(st.outcome, msg)
            #: ★상태도 비운다. 응답에서만 지우면 감사·체크포인트에 검증 실패한
            #:   인용이 남아 응답과 내부 상태가 갈라진다.
            st.clauses = ()
            st.done = True
            return st

        st.retries += 1
        st.retry_reasons.append(reason)

        if self._retarget is not None:
            #: 표적 검색 1회. 여기서도 **verdict 를 바꾸지 않는다** —
            #: 근거를 더 모아 규칙엔진을 다시 부를 뿐이다.
            st.outcome = self._retarget(body, st.outcome)
            st.clauses = tuple(st.outcome.citations)
            return st

        #: 되돌아갈 수단이 없으면 그 사실을 숨기지 않고 기권한다.
        st.outcome = self._abstain(st.outcome, msg)
        st.clauses = ()
        st.done = True
        return st

    @staticmethod
    def _abstain(outcome: PrecheckOutcome, msg: str) -> PrecheckOutcome:
        """초안을 폐기하고 기권한다.

        ★설명문과 인용을 **비운다.** 검증에 실패한 근거를 남겨 두면
          클라이언트가 그걸 읽고 판단한다.
        """
        from dataclasses import replace

        return replace(
            outcome,
            verdict=Verdict.NEEDS_EXPERT,
            abstained=True,
            reason_code=ReasonCode.CITATION_UNVERIFIED,
            message=(
                "근거 인용을 검증하지 못해 판정하지 않았습니다. "
                f"전문가 확인이 필요합니다. ({msg})"
            ),
            citations=(),
            per_code=(),
        )

    # ── 실행 ────────────────────────────────────────────────────────

    def invoke(self, body: PrecheckInput) -> tuple[PrecheckOutcome, GraphState]:
        """순차 실행기. **LangGraph 가 없어도 같은 흐름을 돈다.**

        ★분기와 재시도가 아래 `while` 하나에 다 보인다.
          자율 루프가 아니라 **상한이 코드에 박힌 반복**이다.

        ★★**흐름 전체에서 승인 릴리스를 한 벌로 고정한다.**
          판정 본체만 고정했더니 **뒤따르는 인용 검증이 밖에서 새 릴리스를 읽었다**
          (코덱스 라운드3 지적). 판정과 검증이 다른 판을 보면
          "근거가 실재하는가"를 **다른 약관에 대고 묻는 셈**이다.
        """
        from app.core import release

        with release.pinned():
            return self._invoke(body)

    def _invoke(self, body: PrecheckInput) -> tuple[PrecheckOutcome, GraphState]:
        st = GraphState()
        self.normalize(st, body)
        self.run_rules(st, body)

        guard = 0
        while not st.done:
            self.verify_citations(st, body)
            guard += 1
            #: ★MAX_RETRY 로 이미 막히지만, 이중으로 잠근다.
            #:   흐름 버그로 무한 반복이 되면 서비스가 멈춘다.
            if guard > MAX_RETRY + 1:
                st.outcome = self._abstain(st.outcome, "재시도 흐름이 끝나지 않았습니다")
                st.done = True

        assert st.outcome is not None
        return st.outcome, st

    def build_langgraph(self, body: PrecheckInput):
        """같은 노드·분기를 LangGraph `StateGraph` 로 세운다.

        ★`invoke()` 와 **흐름이 같아야 한다.** 테스트가 그것을 강제한다.
          두 실행기가 갈라지면 어느 쪽이 진짜인지 알 수 없게 된다.

        ★`body` 를 **상태에 넣지 않고 클로저로 잡는다.**

            전에는 `{"st": st, "body": body}` 를 상태로 넘겼다. LangGraph 상태는
            체크포인터·트레이싱으로 **그대로 직렬화돼 남는다** — 그러면 질병기호
            원문이 기록된다. `GraphState` 만 해시로 담아도 소용이 없다.
            그래서 그래프를 요청마다 세우고 원문은 파이썬 클로저에만 둔다.
        """
        from langgraph.graph import END, StateGraph

        g = StateGraph(dict)

        def _norm(s: dict) -> dict:
            self.normalize(s["st"], body)
            return s

        def _rules(s: dict) -> dict:
            self.run_rules(s["st"], body)
            return s

        def _verify(s: dict) -> dict:
            self.verify_citations(s["st"], body)
            return s

        g.add_node("normalize", _norm)
        g.add_node("rules", _rules)
        g.add_node("verify_citations", _verify)
        g.set_entry_point("normalize")
        g.add_edge("normalize", "rules")
        g.add_edge("rules", "verify_citations")
        #: ★되돌아오는 지점. 끝났으면 END, 아니면 자기 자신으로.
        g.add_conditional_edges(
            "verify_citations",
            lambda s: END if s["st"].done else "verify_citations",
            {END: END, "verify_citations": "verify_citations"},
        )
        return g.compile()


def verify_against_store(outcome: PrecheckOutcome, clauses) -> tuple:
    """결과의 인용을 **저장소 원문과 대조**한다.

    ★전에는 자기 자신을 검증했다.

        `outcome.citations` 로 `evidence` 를 만들어 그 `evidence` 로 같은
        `citations` 를 검사했다. 조작된 인용도 **스스로를 통과시킨다.**
        검증은 반드시 **독립된 출처**와 대조해야 한다 — 그래서 여기서
        조항 저장소를 다시 읽는다.

    ★`qualified_no` 만 비교하면 부족하다.
        문서 내 중복 31,085건(실측). 그래서 `clause_id`(내용 해시 포함)로
        찾고, 쪽 범위와 **인용문이 원문에 실제로 있는지**까지 본다.

    ★지금은 LLM 설명문이 없어 `answer_text` 는 빈 문자열이다.
        `outcome.message` 를 넘기면 규칙엔진 메시지를 설명문인 척하게 된다.
        없는 것을 있는 척하지 않는다.
    """
    if not outcome.citations:
        #: 0건은 그래프가 fail-closed 로 이미 처리한다. 여기 오지 않는다.
        return True, None, ""

    pol = outcome.applied_policy
    sha = pol.sha256 if pol else ""
    if not sha:
        return (
            False,
            ReasonCode.CITATION_UNVERIFIED,
            "적용 약관을 알 수 없어 인용을 대조하지 못했습니다",
        )

    #: ★독립 출처. 판정 결과가 아니라 저장소에서 읽는다.
    try:
        #: ★★`usable_only=False` 로 **전부** 읽되, 아래에서 공통 게이트로 다시 거른다.
        #:   전에는 전부 읽고 `row.usable` 을 **확인하지 않았다**(코덱스 지적) —
        #:   인용 부적격 조항이 "저장소에 있으니 통과"가 됐다.
        stored = clauses.load_clauses(sha, usable_only=False)
    except Exception as e:  # 저장소 장애는 통과가 아니라 실패다
        return False, ReasonCode.CITATION_UNVERIFIED, f"조항 저장소를 읽지 못했습니다: {e}"

    #: ★★**정확히 한 행일 때만** 통과시킨다.
    #:
    #:   `clause_id` 는 `sha12 + qualified_no + hash8` 이라 잘린 해시를 쓴다.
    #:   dict 로 만들면 충돌한 행이 **조용히 덮인다**(코덱스 지적) —
    #:   덮인 쪽을 인용했는데 남은 쪽과 대조해 통과할 수 있다.
    #:
    #:   그래서 같은 키가 둘 이상이면 **그 키는 아예 못 쓴다**로 표시한다.
    by_id: dict[str, object] = {}
    ambiguous: set[str] = set()
    for row in stored:
        k = row.clause_id
        if k in by_id:
            ambiguous.add(k)
        by_id[k] = row
    #: 조회용 보조 색인 — occurrence 식별자가 있으면 그게 더 정확하다.
    by_occ = {}
    for row in stored:
        oid = getattr(row, "occurrence_id", "")
        if oid:
            by_occ.setdefault(oid, []).append(row)

    def _norm(s: str) -> str:
        return re.sub(r"\s+", "", s or "")

    for c in outcome.citations:
        if c.clause_id in ambiguous:
            return (
                False,
                ReasonCode.AMBIGUOUS_CITATION,
                f"같은 식별자의 조항이 둘 이상입니다: {c.clause_id}",
            )
        row = by_id.get(c.clause_id)
        if row is not None:
            #: ★★인용이 **수록 식별자를 들고 왔으면** 그것으로 대조한다.
            #:   안 들고 왔으면 저장소 행의 것을 쓰되, 그것도 없으면 기권한다 —
            #:   "정확히 한 행" 을 확인할 방법이 없는데 통과시킬 수는 없다.
            oid = getattr(c, "occurrence_id", "") or getattr(row, "occurrence_id", "")
            if not oid:
                return (
                    False,
                    ReasonCode.CITATION_UNVERIFIED,
                    f"수록 식별자가 없어 인용을 특정할 수 없습니다: {c.clause_id}",
                )
            #: ★★**두 식별자가 같은 행인지 확인한다.**
            #:   전에는 `count == 1` 만 보고 `clause_id=A + occurrence_id=B` 조합을
            #:   통과시켰다(코덱스 지적) — 서로 다른 조항을 가리키는데도.
            hit = by_occ.get(oid, [])
            if len(hit) == 1 and hit[0] is not row:
                return (
                    False,
                    ReasonCode.CITATION_UNVERIFIED,
                    f"조항 식별자와 수록 식별자가 다른 조항을 가리킵니다: "
                    f"{c.clause_id} vs {oid}",
                )
            if len(hit) != 1:
                return (
                    False,
                    ReasonCode.AMBIGUOUS_CITATION,
                    f"수록 식별자가 한 행을 가리키지 않습니다: {oid}",
                )
            #: ★★**공통 게이트를 여기서 다시 부른다.**
            #:   `row.usable` 만 믿으면 안 된다 — `ClauseRow.usable` 의 **기본값이 True** 라
            #:   필드를 안 채운 저장소의 행이 그대로 통과한다(코덱스 지적).
            #:   판정 근거는 두 번 검사해도 손해가 없다.
            from app.core.domain import eligibility as _elig

            _v = _elig.check_row(row)
            if not _v.usable:
                return (
                    False,
                    ReasonCode.CITATION_UNVERIFIED,
                    f"근거로 쓸 수 없는 조항을 인용했습니다: {c.clause_id} ({_v.reason})",
                )
            if not getattr(row, "usable", False):
                return (
                    False,
                    ReasonCode.CITATION_UNVERIFIED,
                    f"근거로 쓸 수 없는 조항을 인용했습니다: {c.clause_id}"
                    f" ({getattr(row, 'unusable_reason', '사유 미상')})",
                )
        if row is None:
            return (
                False,
                ReasonCode.CITATION_UNVERIFIED,
                f"저장소에 없는 조항을 인용했습니다: {c.clause_id}",
            )
        if c.qualified_no and c.qualified_no != row.qualified_no:
            return (
                False,
                ReasonCode.CITATION_UNVERIFIED,
                f"조항 번호가 원문과 다릅니다: {c.qualified_no} ≠ {row.qualified_no}",
            )
        if c.page_from and (c.page_from < row.page_from or c.page_to > row.page_to):
            return (
                False,
                ReasonCode.CITATION_UNVERIFIED,
                f"쪽 범위가 원문 밖입니다: {c.page_from}-{c.page_to} "
                f"(원문 {row.page_from}-{row.page_to})",
            )
        if c.quote and _norm(c.quote) not in _norm(row.text):
            return (
                False,
                ReasonCode.CITATION_UNVERIFIED,
                f"인용문이 원문에 없습니다: {c.qualified_no}",
            )

    #: 원문 대조를 통과한 뒤에만 조항번호 검증기에 넘긴다.
    #: ★evidence 는 **저장소에서 읽은 행**이다. 인용에서 만들지 않는다.
    from app.core.usecases import precheck as uc

    return uc.verify_explanation(
        cited_clauses=[c.qualified_no for c in outcome.citations],
        evidence=[by_id[c.clause_id] for c in outcome.citations],
        answer_text="",
    )


def build() -> PrecheckGraph:
    """조립. **어댑터를 고르는 것은 조립 지점의 일이다.**"""
    from app.composition import build_precheck
    from app.core.usecases import precheck as uc

    deps = build_precheck()

    #: ★★기동 시 한 번 읽어 **가두지 않는다.**
    #:
    #:   전에는 여기서 `load_versions()` 를 불러 클로저에 담았다. 그래서
    #:   판정 모드를 바꿔도, 확정 원장에 문서를 추가해도 **재시작 전까지
    #:   반영되지 않았다**(2026-08-04 실측). 캐시는 어댑터가 파일 지문으로
    #:   관리한다 — 안 바뀌면 재사용하므로 매 요청 1,367줄을 다시 읽지 않는다.
    def _versions():
        loader = getattr(deps["policies"], "load_versions_cached", None)
        return loader() if loader else deps["policies"].load_versions()

    def _run(body: PrecheckInput) -> PrecheckOutcome:
        return uc.run(body, policies=deps["policies"], clauses=deps["clauses"],
                      versions=_versions())

    def _verify(outcome: PrecheckOutcome):
        return verify_against_store(outcome, deps["clauses"])

    return PrecheckGraph(run_precheck=_run, verify=_verify)


__all__ = [
    "GraphState",
    "PrecheckGraph",
    "MAX_RETRY",
    "NODES",
    "build",
    "verify_against_store",
]
