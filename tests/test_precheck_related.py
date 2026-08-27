"""참고 조항 검색 — **판정은 절대 안 바뀐다.**

★이 시험이 있는 이유

    `/v1/prechecks` 가 벡터 색인을 처음으로 보게 됐다(2026-08-25). 편한 만큼 위험하다 —
    의미검색 결과가 **판정에 새어 들어가면** 유사도가 근거 행세를 하게 된다.
    CLAUDE.md §0 이 금지하는 바로 그것이다: 「근거 조항을 못 대면 판정하지 않는다」.

    그래서 계약을 시험으로 못 박는다 —

      · 참고 조항 검색을 **켜든 끄든 판정은 동일하다.**
      · 검색이 **헛것을 물어 와도** 판정은 동일하다.
      · 검색이 **터져도** 판정은 동일하고, 그 사실이 응답에 남는다.
      · 참고 조항은 `citations` 에 **안 섞인다.** 급도 다르다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.domain.precheck_result import (
    EvidenceTier,
    PrecheckInput,
    ReasonCode,
)
from app.core.ports.precheck import ClauseRow, PolicyVersionRow
from app.core.usecases import precheck as uc

_SHA = "a" * 64


def _version() -> PolicyVersionRow:
    return PolicyVersionRow(
        insurer="테스트화재", product_name="테스트실손의료비보장보험",
        sale_start="20200101", sale_end="20201231", generation=4,
        generation_label="4세대", product_line="실손", sha256=_SHA,
        date_confidence="exact", generation_confidence="", identification="confirmed",
    )


def _row(no: str, text: str, title: str = "") -> ClauseRow:
    return ClauseRow(
        sha256=_SHA, qualified_no=no, clause_no=no, section="제3관",
        title=title or f"제{no}조", text=text, page_from=10, page_to=10,
        content_hash=f"{no}" * 8, parse_status="ok", citation_eligible=True,
    )


class _Policies:
    def load_versions(self):
        return [_version()]

    def resolve(self, *, insurer, enrolled_on, product_name=None, versions=None):
        return _version()


class _Clauses:
    """면책 조항이 하나 있고, 질의한 코드는 거기 **없다** → `no_evidence` 로 끝난다."""

    def load_clauses(self, sha256, *, usable_only=True):
        return [
            _row("15", "회사는 다음의 경우에는 보상하지 않습니다. "
                       "한국표준질병사인분류상의 F00~F99 에 해당하는 질병",
                 "보상하지 않는 사항"),
        ]

    def stats(self, sha256):
        return {"parse_status": "ok", "extractor": "s6_pymupdf-1.28.0"}


class _Related:
    """부른 질의를 기록해 두는 가짜 의미검색."""

    def __init__(self, rows=None, boom: Exception | None = None):
        self.rows = rows if rows is not None else [
            _row("3", "회사는 피보험자가 상해로 병원에 입원하여 치료를 받은 때에는 "
                      "입원의료비를 보상하여 드립니다.", "보상하는 사항"),
        ]
        self.boom = boom
        self.calls: list[tuple[str, str, int]] = []

    def find(self, sha256, query, *, limit=5):
        self.calls.append((sha256, query, limit))
        if self.boom:
            raise self.boom
        return self.rows


def _req(**kw) -> PrecheckInput:
    base = PrecheckInput(insurer="테스트화재", enrolled_on="20200601",
                         kcd_codes=("S72.0",))
    return replace(base, **kw) if kw else base


def _verdict_shape(o):
    """**판정 부분만** 뽑는다. 이 값이 바뀌면 참고 조항이 판정에 샌 것이다."""
    return (
        o.verdict, o.abstained, o.reason_code, o.message,
        tuple((a.code, a.verdict, a.reason_code, a.citations, a.note) for a in o.per_code),
        tuple(o.citations),
    )


def _run(related=None):
    return uc.run(_req(), policies=_Policies(), clauses=_Clauses(), related=related)


# ── 계약 ────────────────────────────────────────────────────────────

def test_참고조항을_붙여도_판정이_같다():
    """★★이 시험이 이 기능의 존재 이유이자 한계다."""
    base = _run(None)
    with_related = _run(_Related())

    assert base.reason_code == ReasonCode.NO_EVIDENCE, "전제: 근거 없음으로 끝나는 경우"
    assert _verdict_shape(base) == _verdict_shape(with_related), (
        "참고 조항이 판정을 바꿨다 — 유사도가 근거 행세를 하고 있다"
    )
    assert with_related.related_clauses, "참고 조항은 붙어야 한다"
    assert not base.related_clauses and base.related_search == ""


def test_헛것을_물어와도_판정이_같다():
    """검색이 **아무 상관 없는 조항**을 줘도 판정은 그대로다."""
    junk = [_row("99", "이 보험의 계약자는 보험료를 납입하여야 합니다.", "보험료 납입")]
    assert _verdict_shape(_run(None)) == _verdict_shape(_run(_Related(rows=junk)))


def test_검색이_터져도_판정이_살고_그_사실이_남는다():
    """★실패를 빈 목록으로 숨기지 않는다 — 「관련 조항 없음」과 다른 말이다."""
    o = _run(_Related(boom=RuntimeError("pgvector 연결 실패")))

    assert _verdict_shape(o) == _verdict_shape(_run(None)), "검색 실패가 판정을 흔들면 안 된다"
    assert o.related_clauses == [], "실패했으면 참고 조항은 없다"
    assert o.related_search.startswith("failed: RuntimeError"), o.related_search
    assert any("참고 조항 검색에 실패" in w for w in o.warnings), (
        "실패가 조용히 빈 목록이 되면 화면은 '관련 조항 없음'이라 읽는다"
    )


def test_참고조항은_근거와_섞이지_않는다():
    o = _run(_Related())
    assert all(c.tier is EvidenceTier.RETRIEVED_CLAUSE for c in o.related_clauses)
    ids = {c.clause_id for c in o.citations}
    assert not (ids & {c.clause_id for c in o.related_clauses}), "두 목록이 겹쳤다"
    #: 코드별 판정 안에도 새면 안 된다.
    for a in o.per_code:
        assert all(c.tier is not EvidenceTier.RETRIEVED_CLAUSE for c in a.citations)


def test_자유서술이_있으면_그걸로_묻고_없으면_보상하는사항을_묻는다():
    r = _Related()
    uc.run(_req(condition_text="무릎 반월판 파열로 관절경 수술"),
           policies=_Policies(), clauses=_Clauses(), related=r)
    assert r.calls[0][1] == "무릎 반월판 파열로 관절경 수술"

    r2 = _Related()
    _run(r2)
    assert "보상하는 사항" in r2.calls[0][1], r2.calls[0]


def test_범위를_약관_한벌로_가둔다():
    """전역 검색을 열지 않는다 — 다른 세대 조항이 참고로 붙으면 틀린 참고다."""
    r = _Related()
    _run(r)
    assert r.calls and r.calls[0][0] == _SHA


def test_근거가_이미_있으면_참고조항을_안_붙인다():
    """면책 조항을 실제로 짚은 판정에 벡터 결과를 얹지 않는다."""
    r = _Related()
    o = uc.run(_req(kcd_codes=("F20.0",)),
               policies=_Policies(), clauses=_Clauses(), related=r)
    assert o.reason_code == ReasonCode.EXCLUDED_BY_CLAUSE, "전제: 면책으로 걸리는 코드"
    assert r.calls == [], "근거 있는 답에 근거 아닌 것을 섞지 않는다"
    assert o.related_clauses == []


def test_기본은_꺼져있다():
    from app.core.config import Settings

    assert Settings().PRECHECK_RELATED_SEARCH_ENABLED is False, (
        "판정 경로에 새 조회를 기본으로 켜지 않는다 — 도움이 되는지 아직 실측 전이다"
    )


def test_어댑터는_범위없는_호출을_전역으로_바꾸지_않는다():
    from app.adapters.related_clause_search import PgVectorRelatedClauses

    a = PgVectorRelatedClauses(index=object(), embedder_factory=lambda: object(),
                               conn_factory=lambda: None)
    with pytest.raises(ValueError, match="sha256"):
        a.find("", "질의")
    with pytest.raises(ValueError, match="질의"):
        a.find(_SHA, "   ")
