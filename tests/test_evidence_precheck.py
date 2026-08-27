from app.core.domain.insurance import Verdict
from app.core.domain.precheck_result import PrecheckInput
from app.core.ports.precheck import ClauseRow
from app.core.usecases.assess import ExplanationDraftV1
from app.core.usecases.evidence_precheck import run


SHA = "a" * 64


def _row(no: str, text: str) -> ClauseRow:
    return ClauseRow(
        sha256=SHA,
        qualified_no=f"보통약관/{no}",
        clause_no=no,
        section="보통약관",
        title="",
        text=text,
        page_from=1,
        page_to=1,
        content_hash=(no.encode().hex() + "0" * 64)[:64],
        citation_eligible=True,
        chunk_type="clause",
        parse_status="ok",
    )


class _Store:
    def __init__(self, rows, search=()):
        self.rows = list(rows)
        self.search_rows = list(search)

    def stats(self, _sha):
        return {"parse_status": "ok"}

    def load_clauses(self, _sha, *, usable_only=True):
        return self.rows

    def search(self, _sha, _query, *, limit=8):
        return self.search_rows[:limit]


def _request(code="F32"):
    return PrecheckInput("보험사", "20200101", (code,), "실손")


def test_검색부터_설명검증까지_한_약관에서_이어진다():
    row = _row("제1조", "F04~F99를 보상하지 않습니다.")
    result = run(policy_version_sha=SHA, request=_request(), clauses=_Store([row]))
    assert result.assessment.verdict is Verdict.UNLIKELY
    assert result.explanation.verdict is Verdict.UNLIKELY
    assert result.explanation.cited_clauses == ("E001",)


def test_근거가_없으면_검색_폴백없이_기권한다():
    result = run(policy_version_sha=SHA, request=_request(), clauses=_Store([]))
    assert result.evidence.clauses == []
    assert result.assessment.verdict is Verdict.NEEDS_EXPERT
    assert result.explanation.abstained is True


def test_질문검색_조항도_같은_안전관문을_거친다():
    row = _row("제1조", "치과 치료는 별도 서류가 필요합니다.")
    result = run(
        policy_version_sha=SHA,
        request=_request("Z99"),
        question="치과 치료",
        clauses=_Store([row], search=[row]),
    )
    assert result.evidence.clauses == [row]
    assert result.assessment.verdict is Verdict.NEEDS_EXPERT


def test_주입한_LLM_설명생성기도_검증뒤에만_반환한다():
    row = _row("제1조", "F04~F99를 보상하지 않습니다.")

    class _Generator:
        def generate(self, assessment, bundle):
            return ExplanationDraftV1(
                verdict=assessment.verdict,
                cited_clauses=("E001",),
                quotes={"E001": "F04~F99를 보상하지 않습니다."},
                reason="근거 E001: “F04~F99를 보상하지 않습니다.”",
            )

    result = run(
        policy_version_sha=SHA,
        request=_request(),
        clauses=_Store([row]),
        explanation_generator=_Generator(),
    )
    assert result.explanation.verdict is Verdict.UNLIKELY
    assert result.explanation.abstained is False
