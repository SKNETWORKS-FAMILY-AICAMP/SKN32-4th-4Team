import pytest

from app.core.domain.benefit_facts import DisabilityRateFact
from app.core.errors import ValidationErr
from app.core.usecases.disability_rate import lookup


SHA = "a" * 64


def _fact(**changes):
    values = dict(
        policy_version_sha=SHA,
        candidate_id="candidate-1",
        classification="한 눈이 멀었을 때",
        payment_rate_percent=50,
        ordinal=2,
        page=80,
        content_hash="b" * 64,
        approval="human_pattern_approved",
        serving_eligible=True,
        citation_eligible=True,
    )
    values.update(changes)
    return DisabilityRateFact(**values)


class _Source:
    def __init__(self, facts):
        self.facts = facts

    def load_for_policy(self, sha):
        return [fact for fact in self.facts if fact.policy_version_sha == sha]


def test_사람승인과_정확문구가_모두_맞아야_지급률을_반환한다():
    result = lookup(
        policy_version_sha=SHA,
        classification="한 눈이 멀었을 때",
        source=_Source([_fact()]),
    )
    assert result.payment_rate_percent == 50
    assert result.ambiguous is False


def test_후보상태는_같은문구라도_서비스에_내보내지_않는다():
    result = lookup(
        policy_version_sha=SHA,
        classification="한 눈이 멀었을 때",
        source=_Source([_fact(
            approval="candidate",
            serving_eligible=False,
            citation_eligible=False,
        )]),
    )
    assert result.payment_rate_percent is None
    assert result.blocked_candidates == 1
    assert "승인 전" in result.reason


def test_같은분류의_서로다른_승인지급률은_추측하지_않는다():
    result = lookup(
        policy_version_sha=SHA,
        classification="한 눈이 멀었을 때",
        source=_Source([_fact(), _fact(candidate_id="candidate-2", payment_rate_percent=40)]),
    )
    assert result.payment_rate_percent is None
    assert result.ambiguous is True


def test_부분일치나_잘못된_SHA는_허용하지_않는다():
    result = lookup(
        policy_version_sha=SHA,
        classification="한 눈",
        source=_Source([_fact()]),
    )
    assert result.payment_rate_percent is None
    with pytest.raises(ValidationErr, match="64자리"):
        lookup(policy_version_sha="short", classification="한 눈", source=_Source([]))
