from pathlib import Path

import pytest

from app.adapters.file_benefit_facts import FileSelfPayFactSource
from app.core.domain.benefit_facts import SelfPayFact, calculate_self_pay, parse_won
from app.core.errors import ValidationErr
from app.core.usecases.self_pay import calculate, lookup


SHA = "a" * 64


def _fact(**kw) -> SelfPayFact:
    values = dict(
        policy_version_sha=SHA,
        candidate_id="sha256:" + "1" * 64,
        plan="표준형",
        services=("외래",),
        institution="상급종합병원",
        coverage=("급여",),
        formula="2만원과 보상대상의료비의 20% 중 큰 금액",
        amount_tokens=("2만원",),
        rate_tokens=("20%",),
        page=10,
        content_hash="2" * 64,
        approval="human_pattern_approved",
    )
    values.update(kw)
    return SelfPayFact(**values)


class _Source:
    def __init__(self, facts):
        self.facts = facts

    def load_for_policy(self, sha):
        return [fact for fact in self.facts if fact.policy_version_sha == sha]


@pytest.mark.parametrize(("text", "won"), [("8천원", 8000), ("1만5천원", 15000), ("2만원", 20000)])
def test_한글_금액표기를_정수로_읽는다(text, won):
    assert parse_won(text) == won


def test_큰_금액_공식을_계산하고_의료비를_넘지_않는다():
    assert calculate_self_pay(_fact(), eligible_expense_won=200_000).deductible_won == 40_000
    assert calculate_self_pay(_fact(), eligible_expense_won=10_000).deductible_won == 10_000


def test_축으로_하나를_확정한_뒤에만_계산한다():
    source = _Source([_fact()])
    result = lookup(
        policy_version_sha=SHA,
        plan="표준형",
        service="외래",
        institution="상급종합",
        coverage="급여",
        source=source,
    )
    assert result.ambiguous is False
    assert calculate(result, eligible_expense_won=100_000).deductible_won == 20_000


def test_공식이_여럿이면_추측하지_않는다():
    source = _Source([_fact(), _fact(institution="의원", formula="1만원")])
    result = lookup(policy_version_sha=SHA, plan="표준형", service="외래", source=source)
    assert result.ambiguous is True
    with pytest.raises(ValidationErr, match="정확히 하나"):
        calculate(result, eligible_expense_won=100_000)


def test_실제_승인파일은_해시와_850건_경계를_통과한다():
    root = Path(__file__).resolve().parents[1]
    if not (root / "data/work/s7_1_approved_facts/manifest.json").is_file():
        pytest.skip("비공개 자기부담금 승인 원장은 공개 코드 저장소에 포함하지 않습니다.")
    source = FileSelfPayFactSource(root)
    # 공개 메서드로 실제 파일을 읽기 위해 승인 사실에 존재하는 전체 SHA를 하나 구한다.
    loaded = source._load()
    assert sum(len(rows) for rows in loaded.values()) == 850
    assert all(len(sha) == 64 for sha in loaded)
