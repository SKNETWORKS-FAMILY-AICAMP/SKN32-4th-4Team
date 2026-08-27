"""확정 약관 범위를 벗어나지 않는 근거 묶음 계약."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.errors import ValidationErr
from app.core.ports.precheck import ClauseRow
from app.core.usecases.retrieval import retrieve


SHA = "a" * 64
OTHER = "b" * 64


def _row(
    no: str, text: str, *, sha: str = SHA, h: str | None = None, **kw
) -> ClauseRow:
    base = dict(
        sha256=sha,
        qualified_no=f"보통약관/{no}",
        clause_no=no,
        section="보통약관",
        title="",
        text=text,
        page_from=1,
        page_to=1,
        content_hash=h or (no.encode("utf-8").hex() + "0" * 64)[:64],
        usable=True,
        citation_eligible=True,
        chunk_type="clause",
        parse_status="ok",
    )
    base.update(kw)
    return ClauseRow(**base)


class _Store:
    def __init__(self, rows, *, status="ok", lexical=None):
        self.rows = list(rows)
        self.status = status
        self.lexical = list(lexical or [])

    def stats(self, _sha):
        return {"parse_status": self.status}

    def load_clauses(self, _sha, *, usable_only=True):
        return list(self.rows)

    def search(self, _sha, _query, *, limit=8):
        return self.lexical[:limit]


def test_문서_경계를_신뢰할_수_없으면_즉시_멈춘다():
    with pytest.raises(ValidationErr, match="parse_status"):
        retrieve(
            policy_version_sha=SHA,
            kcd_codes=["F32"],
            clauses=_Store([], status="suspect"),
        )


def test_다른_약관과_인용불가_조항을_근거에서_제외한다():
    good = _row("제1조", "정신 및 행동장애(F04~F99)는 보상하지 않습니다")
    rows = [
        good,
        _row("제2조", "F32", sha=OTHER),
        _row("제3조", "F32", citation_eligible=False),
        _row("제4조", "F32", chunk_type="page_fallback"),
    ]
    bundle = retrieve(policy_version_sha=SHA, kcd_codes=["F32"], clauses=_Store(rows))
    assert bundle.clauses == [good]
    assert all(row.sha256 == SHA for row in bundle.clauses)
    assert bundle.truncated == ["안전 관문을 통과하지 못한 조항 3개 제외"]


def test_코드가_실제로_나온_조항과_규칙만_담는다():
    hit = _row("제1조", "회사는 정신 및 행동장애(F04~F99)를 보상하지 않습니다")
    miss = _row("제2조", "상해 입원 의료비를 보상합니다")
    bundle = retrieve(
        policy_version_sha=SHA, kcd_codes=["F32"], clauses=_Store([hit, miss])
    )
    assert bundle.clauses == [hit]
    assert [mention.kind for mention in bundle.code_rules] == ["exclude"]


def test_검색_0건이면_비슷한_다른_조항을_끌어오지_않는다():
    row = _row("제1조", "상해 입원 의료비")
    bundle = retrieve(
        policy_version_sha=SHA,
        kcd_codes=["F32"],
        question="치과 보철",
        clauses=_Store([row], lexical=[]),
    )
    assert bundle.clauses == []


def test_의미검색_hit를_저장소_원문으로_다시_해소한다():
    lexical = _row("제1조", "치과 치료")
    semantic = _row("제2조", "보철 치료")
    forged_other = replace(semantic, sha256=OTHER)

    class _Result:
        hits = [forged_other, semantic]

    bundle = retrieve(
        policy_version_sha=SHA,
        kcd_codes=[],
        question="치과 보철",
        top_k=2,
        clauses=_Store([lexical, semantic], lexical=[lexical]),
        semantic_search=lambda **_kw: _Result(),
    )
    assert bundle.clauses == [lexical, semantic]
    assert bundle.clauses[1] is semantic


def test_유일한_준용조항은_3단계_안에서_따라간다():
    one = _row("제1조", "제2조에서 정한 사유가 발생한 경우")
    two = _row("제2조", "제3조의 절차에 따릅니다")
    three = _row("제3조", "보험금 지급 절차")
    bundle = retrieve(
        policy_version_sha=SHA,
        kcd_codes=[],
        question="정한 사유",
        clauses=_Store([one, two, three], lexical=[one]),
    )
    assert [row.qualified_no for row in bundle.clauses] == [
        "보통약관/제1조",
        "보통약관/제2조",
        "보통약관/제3조",
    ]
    assert all(path.resolved for path in bundle.reference_paths)


def test_같은_번호가_둘이면_하나를_추측하지_않는다():
    source = _row("제1조", "제9조에 따릅니다")
    a = _row("제9조", "첫 번째", h="1" * 64)
    b = replace(
        a, qualified_no="특별약관/제9조", section="특별약관", content_hash="2" * 64
    )
    bundle = retrieve(
        policy_version_sha=SHA,
        kcd_codes=[],
        question="따릅니다",
        clauses=_Store([source, a, b], lexical=[source]),
    )
    assert [row for row in bundle.clauses if "제9조" in row.qualified_no] == []
    assert bundle.reference_paths[0].resolved is False
    assert "후보 2개" in bundle.unresolved_references[0]


@pytest.mark.parametrize("code", ["", "F3", "가나다"])
def test_잘못된_KCD는_빈_결과가_아니라_입력오류다(code):
    with pytest.raises(ValidationErr, match="KCD"):
        retrieve(policy_version_sha=SHA, kcd_codes=[code], clauses=_Store([]))
