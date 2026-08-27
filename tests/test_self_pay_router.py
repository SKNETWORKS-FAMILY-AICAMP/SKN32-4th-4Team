"""POST /v1/self-pay — 승인 사실만으로 자기부담금을 계산하는지 확인한다."""

from fastapi.testclient import TestClient

from app.core.domain.benefit_facts import SelfPayFact
from app.main import create_app
from app.routers import precheck as public_router

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


class _FakeSource:
    def __init__(self, facts):
        self.facts = facts

    def load_for_policy(self, sha):
        return [f for f in self.facts if f.policy_version_sha == sha]


def _client(facts, monkeypatch) -> TestClient:
    monkeypatch.setattr(public_router, "_SELF_PAY_SOURCE", _FakeSource(facts))
    return TestClient(create_app("customer"))


def test_승인사실이_하나면_계산한다(monkeypatch):
    client = _client([_fact()], monkeypatch)
    r = client.post("/v1/self-pay", json={
        "policy_version_sha": SHA,
        "plan": "표준형",
        "service": "외래",
        "eligible_expense_won": 200_000,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is True
    assert body["ambiguous"] is False
    assert body["deductible_won"] == 40_000


def test_후보가_여러개면_금액을_추측하지_않는다(monkeypatch):
    client = _client(
        [_fact(institution="상급종합병원", page=10),
         _fact(candidate_id="sha256:" + "3" * 64, institution="한방병원", page=20)],
        monkeypatch,
    )
    r = client.post("/v1/self-pay", json={
        "policy_version_sha": SHA,
        "plan": "표준형",
        "service": "외래",
        "eligible_expense_won": 200_000,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is True
    assert body["ambiguous"] is True
    assert body["deductible_won"] is None
    assert len(body["candidates"]) == 2


def test_기관을_지정하면_좁혀서_확정한다(monkeypatch):
    client = _client(
        [_fact(institution="상급종합병원", page=10),
         _fact(candidate_id="sha256:" + "3" * 64, institution="한방병원", page=20)],
        monkeypatch,
    )
    r = client.post("/v1/self-pay", json={
        "policy_version_sha": SHA,
        "plan": "표준형",
        "service": "외래",
        "institution": "한방",
        "eligible_expense_won": 200_000,
    })
    body = r.json()
    assert body["ambiguous"] is False
    assert body["deductible_won"] == 40_000


def test_승인사실이_없으면_금액대신_사유만_준다(monkeypatch):
    client = _client([], monkeypatch)
    r = client.post("/v1/self-pay", json={
        "policy_version_sha": SHA,
        "plan": "표준형",
        "service": "외래",
        "eligible_expense_won": 200_000,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is False
    assert body["deductible_won"] is None
    assert body["reason"]


def test_짧은_sha는_422(monkeypatch):
    client = _client([_fact()], monkeypatch)
    r = client.post("/v1/self-pay", json={
        "policy_version_sha": "short",
        "plan": "표준형",
        "service": "외래",
        "eligible_expense_won": 200_000,
    })
    assert r.status_code == 422
