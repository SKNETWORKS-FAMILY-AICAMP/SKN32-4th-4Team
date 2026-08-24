"""합성 자동 모드는 진위판정이 아니라 결정론적 정합성 검사다."""

from types import SimpleNamespace

from app.core.domain.synthetic_validation import RULE_VERSION, evaluate


def _payload():
    return {
        "client_ref": "sim-agent-001",
        "insurer": "삼성화재",
        "enrolled_on": "20260804",
        "kcd_codes": ["S72.0"],
        "age_band": "30대",
        "outcome": "paid",
        "idempotency_key": "sim-a1b2c3d4e5f6-001-001",
        "simulation_run_id": "a1b2c3d4e5f6",
        "simulation_case_no": 1,
    }


def test_정상_합성제출은_모든_정합성_검사를_통과한다():
    got = evaluate(_payload())
    assert got.accepted is True
    assert got.rule_version == RULE_VERSION
    assert got.reason_codes == ("all_checks_passed",)
    assert all(value is True for value in got.evidence.values())


def test_약관범위와_잘못된_멱등키는_승격하지_않고_사유를_남긴다():
    got = evaluate({
        **_payload(),
        "kcd_codes": ["C30~C39"],
        "idempotency_key": "재사용키",
    })
    assert got.accepted is False
    assert "single_kcd_code_valid" in got.reason_codes
    assert "idempotency_matches_run_case" in got.reason_codes


def test_검사증거가_진위나_보험금승인을_주장하지_않는다():
    evidence = evaluate(_payload()).evidence
    forbidden = {"truth_verified", "claim_approved", "coverage_confirmed"}
    assert forbidden.isdisjoint(evidence)


def test_형식만_그럴듯한_유령_kcd는_승격하지_않는다():
    got = evaluate({**_payload(), "kcd_codes": ["Z99.9"]})
    assert got.accepted is False
    assert "kcd_code_in_simulator_catalog" in got.reason_codes
    assert got.evidence["single_kcd_code_valid"] is True


def test_api는_접수와_코호트_승격을_구분한다(monkeypatch):
    from app.adapters import demo_submission_store
    from app.obs import agent_stream
    from app.routers.demo import DemoObservation, submit_demo_observation

    monkeypatch.setattr(agent_stream, "publish", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        demo_submission_store,
        "store",
        lambda *args, **kwargs: SimpleNamespace(
            stored=True,
            duplicate=False,
            submission_id="sub-rejected",
            promoted=False,
            verification="rejected",
            reason_codes=("kcd_code_in_simulator_catalog",),
            rule_version=RULE_VERSION,
        ),
    )
    body = DemoObservation(**{**_payload(), "kcd_codes": ["Z99.9"], "auto_validate": True})
    got = submit_demo_observation(body)

    assert got["received"] is True
    assert got["accepted"] is False
    assert got["accepted_for_cohort"] is False
