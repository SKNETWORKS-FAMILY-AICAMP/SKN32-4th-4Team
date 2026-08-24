from __future__ import annotations

from pathlib import Path

from app.core.domain import kcd_ranges as kcd
from app.core.domain.precheck_result import CitationRef
from app.core.ports.precheck import ClauseRow
from app.core.usecases.precheck import _citations, _dedupe
from app.routers.precheck import _cite


def _row(text: str, *, page: int, content_hash: str) -> ClauseRow:
    return ClauseRow(
        sha256="a" * 64,
        qualified_no="별표2/제4조",
        clause_no="제4조",
        section="별표2",
        title="보상하지 않는 사항",
        text=text,
        page_from=page,
        page_to=page + 2,
        content_hash=content_hash,
    )


def _excluded_citation(row: ClauseRow):
    mention = next(m for m in kcd.scan_clause(row.text) if m.kind == "exclude")
    return _citations([(mention, row)], "excluded")[0]


def test_같은_제4조라도_실제_담보_범위를_표시한다():
    room = _row(
        "회사는 다음의 사유로 인하여 생긴 상급병실료차액보험금은 보상하지 않습니다. "
        "정신 및 행동장애(F04~F99)",
        page=62,
        content_hash="1" * 64,
    )
    nursing = _row(
        "회사는 다음의 사유로 인하여 생긴 요양병원 의료비는 보상하지 않습니다. "
        "정신 및 행동장애(F04~F99)",
        page=83,
        content_hash="2" * 64,
    )

    cites = [_excluded_citation(room), _excluded_citation(nursing)]
    assert [c.scope for c in cites] == ["상급병실료차액보험금", "요양병원 의료비"]
    assert len(_dedupe(cites)) == 2


def test_고객화면은_담보_범위를_제목에_붙인다():
    script_path = Path(__file__).resolve().parents[1] / "app" / "static" / "insurance.js"
    script = script_path.read_text(encoding="utf-8")
    assert "c.scope" in script


def test_scope와_occurrence_id가_api_응답까지_유지된다():
    ref = CitationRef(
        clause_id="abc/별표2/제4조#12345678",
        qualified_no="별표2/제4조",
        title="보상하지 않는 사항",
        scope="요양병원 의료비",
        occurrence_id="release:sha:clause:108",
    )
    body = _cite(ref).model_dump(mode="json")
    assert body["scope"] == "요양병원 의료비"
    assert body["occurrence_id"] == "release:sha:clause:108"


def test_한_조항의_면책과_예외가_같은_코드를_포함해도_인용은_하나다():
    row = _row(
        "회사는 다음의 요양병원 의료비는 보상하지 않습니다. "
        "정신 및 행동장애(F04~F99). 다만 F30~F39는 보상합니다.",
        page=83,
        content_hash="3" * 64,
    )
    code = kcd.CodeRef.parse("F32")
    pairs = [(m, row) for m in kcd.scan_clause(row.text) if m.range.contains(code)]
    cites = _dedupe(_citations(pairs, "exception"))
    assert len(cites) == 1
