"""판정 모드(문서 확정 게이트)와 실제 트랙 교차검증.

★이 파일이 지키는 명제

    1. 엄격 모드에서는 **사람 승인 전 문서를 판정에 쓰지 않는다**(fail-closed).
    2. 자동승인 모드는 쓰되, **응답이 그 사실을 말한다**(숨기지 않는다).
    3. 모드가 깨져 있으면 기본값으로 때우지 않고 **실패한다**.
    4. 실제 트랙 승격 등급은 `verified` 가 아니라 `admin_attested` 다.
    5. 승격에는 **근거(basis)** 가 필수다.
    6. 코호트 응답이 등급별 내역을 실어 보낸다 — n 만 보고 오해하지 않게.
"""

from __future__ import annotations

import json

import pytest

from app.core.domain import identification_mode as im
from app.core.errors import InfraError, ValidationErr


@pytest.fixture
def mode_file(tmp_path, monkeypatch):
    f = tmp_path / "precheck_mode.json"
    monkeypatch.setattr(im, "_MODE_FILE", f)
    return f


# ── 모드 자체 ────────────────────────────────────────────────────────────
def test_설정이_없으면_기본은_자동승인이고_경고를_단다(mode_file):
    st = im.current()
    assert st.mode == im.MACHINE_MATCH
    assert st.auto_approve is True
    #: ★켜져 있으면 **반드시** 경고 문구가 붙는다. 조용히 켜지는 일은 없다.
    assert st.as_dict()["warning"]


def test_엄격_모드로_바꾸면_경고가_사라진다(mode_file):
    st = im.set_mode(im.HUMAN_SIGNOFF, actor="opsadmin")
    assert st.auto_approve is False
    assert st.as_dict()["warning"] == ""
    assert im.current().changed_by == "opsadmin"


def test_모드는_파일에_남아_다른_프로세스도_같은_값을_본다(mode_file):
    im.set_mode(im.HUMAN_SIGNOFF, actor="opsadmin")
    saved = json.loads(mode_file.read_text(encoding="utf-8"))
    assert saved["mode"] == im.HUMAN_SIGNOFF
    assert saved["changed_by"] == "opsadmin"
    assert saved["changed_at"]


def test_바꾼_사람_없이는_바꿀_수_없다(mode_file):
    with pytest.raises(ValidationErr, match="바꾼 사람"):
        im.set_mode(im.HUMAN_SIGNOFF, actor="  ")


def test_알_수_없는_모드는_거절한다(mode_file):
    with pytest.raises(ValidationErr, match="판정 모드"):
        im.set_mode("trust_everything", actor="x")


def test_설정이_깨졌으면_기본값으로_때우지_않고_실패한다(mode_file):
    """★가장 중요한 것 — **어느 모드로 판정했는지 모르는 채 답하지 않는다.**"""
    mode_file.write_text("{ not json", encoding="utf-8")
    with pytest.raises(InfraError, match="판정 모드"):
        im.current()

    mode_file.write_text('{"mode": "무엇이든통과"}', encoding="utf-8")
    with pytest.raises(InfraError, match="알 수 없는 판정 모드"):
        im.current()


@pytest.mark.parametrize(
    "confirmed_by,pending",
    [
        ("claude-code 기계대조 · 사람 최종승인 대기", True),
        ("opsadmin 2026-08-04 승인", False),
        ("", False),
    ],
)
def test_사람승인_대기_판별(confirmed_by, pending):
    assert im.is_pending_signoff({"confirmed_by": confirmed_by}) is pending


# ── 게이트가 실제로 판정 목록을 바꾸는가 ──────────────────────────────────
def test_엄색_모드는_사람승인_대기분을_판정에서_뺀다(mode_file):
    """★실측(2026-08-04): 원장 10건이 전부 대기 상태인데 판정에 쓰이고 있었다."""
    from app.adapters import manifest_policy_resolver as mpr

    im.set_mode(im.MACHINE_MATCH, actor="t")
    auto = len(mpr.load_versions())

    im.set_mode(im.HUMAN_SIGNOFF, actor="t")
    strict = len(mpr.load_versions())

    assert strict <= auto, "엄격 모드가 자동승인보다 많이 쓰면 게이트가 거꾸로다."
    #: 원장이 전부 대기 상태인 동안에는 엄격 모드에서 0건이어야 한다.
    ledger = mpr.load_ledger()
    if ledger and all(im.is_pending_signoff(e) for e in ledger.values()):
        assert strict == 0


# ── 실제 트랙 교차검증 ───────────────────────────────────────────────────
@pytest.fixture
def real_track(tmp_path, monkeypatch):
    from app.adapters import external_submission_store as store
    from app.adapters import file_cohort_stats

    subs = tmp_path / "external" / "submissions"
    events = tmp_path / "external" / "events"
    real = tmp_path / "cohort" / "verified_real"
    for d in (subs, events, real):
        d.mkdir(parents=True)
    (real / "events.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr(store, "_SUBMISSIONS", subs)
    monkeypatch.setattr(store, "_EVENTS", events)
    monkeypatch.setattr(store, "_COHORT_EVENTS", real / "events.jsonl")
    monkeypatch.setattr(file_cohort_stats, "_BASE", tmp_path / "cohort")
    return {"store": store, "stats": file_cohort_stats, "real": real}


def _submit(store, ref="agent-1", code="S72.0", outcome="paid"):
    return store.store({"client_ref": ref, "insurer": "테스트화재",
                        "kcd_codes": [code], "outcome": outcome})


def test_승격_등급은_verified_가_아니라_admin_attested_다(real_track):
    store = real_track["store"]
    res = _submit(store)
    event = store.attest(res.idempotency_key, basis="지급통지서 사본 대조", actor="opsadmin")

    assert event["verification"] == "admin_attested"
    assert event["verification"] != "verified", (
        "관리자 교차검증을 verified 라 부르면 발행처 확인으로 읽힌다."
    )
    assert event["verification_method"] == "admin_review"
    assert event["verification_basis"] == "지급통지서 사본 대조"
    #: ★원본이 `payload` 아래 중첩돼 있어 한 겹 벗겨야 한다.
    assert event["insurer"] == "테스트화재"
    assert event["outcome"] == "paid"


def test_근거_없이는_승격할_수_없다(real_track):
    store = real_track["store"]
    res = _submit(store)
    for bad in ("", "   ", "ok"):
        with pytest.raises(ValidationErr):
            store.attest(res.idempotency_key, basis=bad, actor="opsadmin")


def test_같은_제보를_두_번_승격할_수_없다(real_track):
    store = real_track["store"]
    res = _submit(store)
    store.attest(res.idempotency_key, basis="지급통지서 대조", actor="opsadmin")
    with pytest.raises(ValidationErr, match="이미 승격"):
        store.attest(res.idempotency_key, basis="지급통지서 대조", actor="opsadmin")


def test_승격_전에는_실제_집계가_0이고_승격하면_1이_된다(real_track):
    from app.core.domain.insurance import DataSource, KcdCode

    store, stats = real_track["store"], real_track["stats"]

    def n():
        return stats.fetch(kcd_code=KcdCode(version_label="", code="S72.0", name_ko=""),
                           product_id="", age_band=None,
                           data_source=DataSource.VERIFIED_REAL).n

    res = _submit(store)
    assert n() == 0, "미검증 제보가 실제 통계에 들어갔다."
    store.attest(res.idempotency_key, basis="지급통지서 대조", actor="opsadmin")
    assert n() == 1


def test_집계가_등급별_내역을_함께_낸다(real_track):
    """★`n=1` 만 보면 발행처 확인으로 읽힌다. 등급을 숫자 옆에 붙여야 한다."""
    from app.core.domain.insurance import DataSource, KcdCode

    store, stats = real_track["store"], real_track["stats"]
    res = _submit(store)
    store.attest(res.idempotency_key, basis="지급통지서 대조", actor="opsadmin")

    got = stats.fetch(kcd_code=KcdCode(version_label="", code="S72.0", name_ko=""),
                      product_id="", age_band=None, data_source=DataSource.VERIFIED_REAL)
    assert dict(got.by_verification) == {"admin_attested": 1}


def test_실제_저장소는_합성_경로를_모른다():
    """대칭 격리 — 합성 저장소가 실제를 모르듯, 실제도 합성을 몰라야 한다."""
    import io
    import pathlib
    import tokenize

    from app.adapters import external_submission_store as store

    src = pathlib.Path(store.__file__).read_text(encoding="utf-8")
    code = "".join(
        t.string
        for t in tokenize.generate_tokens(io.StringIO(src).readline)
        if t.type not in (tokenize.COMMENT, tokenize.STRING)
    )
    assert "synthetic" not in code
