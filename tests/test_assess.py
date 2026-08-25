from dataclasses import replace

from app.core.domain.insurance import Verdict
from app.core.domain.precheck_result import PrecheckInput, ReasonCode
from app.core.ports.precheck import ClauseRow
from app.core.usecases.assess import ExplanationDraftV1, assess, explain
from app.core.usecases.retrieval import EvidenceBundleV1


SHA = "a" * 64


def _row(no: str, text: str, **kw) -> ClauseRow:
    values = dict(
        sha256=SHA,
        qualified_no=f"보통약관/{no}",
        clause_no=no,
        section="보통약관",
        title="",
        text=text,
        page_from=1,
        page_to=1,
        content_hash=(no.encode().hex() + "0" * 64)[:64],
        usable=True,
        citation_eligible=True,
        chunk_type="clause",
        parse_status="ok",
    )
    values.update(kw)
    return ClauseRow(**values)


def _req(*codes: str) -> PrecheckInput:
    return PrecheckInput("보험사", "20200101", tuple(codes), "실손")


def _bundle(*rows: ClauseRow) -> EvidenceBundleV1:
    return EvidenceBundleV1(policy_version_sha=SHA, clauses=list(rows))


def test_면책과_예외를_규칙으로_판정한다():
    excluded = assess(_bundle(_row("제1조", "F04~F99를 보상하지 않습니다.")), _req("F32"))
    assert excluded.verdict is Verdict.UNLIKELY
    assert excluded.reason_code is ReasonCode.EXCLUDED_BY_CLAUSE
    assert excluded.cited_clause_ids

    exception = assess(
        _bundle(_row("제1조", "F04~F99를 보상하지 않습니다. 다만 F30~F39는 보상합니다.")),
        _req("F32"),
    )
    assert exception.verdict is Verdict.NEEDS_DOCUMENTS
    assert exception.reason_code is ReasonCode.EXCEPTION_APPLIES


def test_목록에_없다는_이유로_보장판정을_만들지_않는다():
    result = assess(_bundle(_row("제1조", "F04~F09를 보상하지 않습니다.")), _req("F32"))
    assert result.verdict is Verdict.NEEDS_EXPERT
    assert result.abstained is True
    assert all(row.verdict is not Verdict.LIKELY_COVERED for row in result.per_code)


def test_같은_문장의_코드와_명시적_지급선언이_있을_때만_긍정한다():
    result = assess(_bundle(_row("제1조", "F32 질병의 치료비를 보상합니다.")), _req("F32"))
    assert result.verdict is Verdict.LIKELY_COVERED
    assert result.reason_code is ReasonCode.COVERED_BY_CLAUSE
    assert result.cited_clause_ids


def test_일부_코드만_긍정이면_전체를_긍정하지_않는다():
    result = assess(
        _bundle(_row("제1조", "F32 질병의 치료비를 보상합니다.")),
        _req("F32", "Z99"),
    )
    assert result.verdict is Verdict.NEEDS_EXPERT


def test_잘못된_코드와_빈_코드는_기권한다():
    assert assess(_bundle(), _req("우울증")).reason_code is ReasonCode.INVALID_CODE
    assert assess(_bundle(), _req()).reason_code is ReasonCode.INVALID_CODE


def test_다른_문서나_인용불가_근거가_섞이면_전체를_기권한다():
    other = _row("제1조", "F04~F99를 보상하지 않습니다.", sha256="b" * 64)
    result = assess(_bundle(other), _req("F32"))
    assert result.verdict is Verdict.NEEDS_EXPERT
    assert result.reason_code is ReasonCode.DOCUMENT_NOT_RELIABLE


def test_검증된_설명은_핸들과_실제_인용문을_반환한다():
    bundle = _bundle(_row("제1조", "F04~F99를 보상하지 않습니다."))
    result = explain(assess(bundle, _req("F32")), bundle)
    assert result.verdict is Verdict.UNLIKELY
    assert result.abstained is False
    assert result.cited_clauses == ("E001",)
    assert "F04~F99" in result.reason


def test_설명초안은_규칙판정을_바꿀_수_없다():
    bundle = _bundle(_row("제1조", "F04~F99를 보상하지 않습니다."))
    assessment = assess(bundle, _req("F32"))
    draft = ExplanationDraftV1(Verdict.LIKELY_COVERED, ("E001",), {"E001": "F04~F99"}, "F04~F99")
    result = explain(assessment, bundle, draft=draft)
    assert result.verdict is Verdict.NEEDS_EXPERT
    assert result.reason_code is ReasonCode.CITATION_UNVERIFIED


def test_근거에_없는_인용을_쓴_설명초안은_폐기한다():
    bundle = _bundle(_row("제1조", "F04~F99를 보상하지 않습니다."))
    assessment = assess(bundle, _req("F32"))
    draft = ExplanationDraftV1(Verdict.UNLIKELY, ("E999",), {"E999": "F04~F99"}, "F04~F99")
    result = explain(assessment, bundle, draft=draft)
    assert result.abstained is True
    assert result.reason_code is ReasonCode.CITATION_UNVERIFIED


def test_판정후_근거행이_사라지면_설명을_만들지_않는다():
    row = _row("제1조", "F04~F99를 보상하지 않습니다.")
    assessment = assess(_bundle(row), _req("F32"))
    result = explain(assessment, _bundle(replace(row, content_hash="f" * 64)))
    assert result.abstained is True
    assert "후보 0개" in result.citation_reason
