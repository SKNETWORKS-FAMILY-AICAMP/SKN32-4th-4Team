"""합성/실제 트랙 분리와 검수 승격 — 계획서 §5-1·§3 을 테스트로 고정한다.

★이 파일이 지키는 명제 네 가지

    1. 제출만으로는 **집계가 움직이지 않는다**(`unverified` 는 세지 않는다).
    2. 승격해야 움직인다 — 그리고 **합성 트랙만** 움직인다.
    3. 합성 승격이 `verified_real` 을 **한 건도** 바꾸지 못한다(혼합 차단).
    4. 승격 방법(`admin_review`/`simulated`)이 이벤트에 남는다 —
       "이 숫자가 어떻게 생겼나"에 답할 수 있어야 한다.

★파일 경로를 monkeypatch 로 임시 폴더에 돌린다. 실제 `data/` 를 건드리면
  테스트가 개발자의 데모 데이터를 지운다.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.adapters import demo_submission_store as demo
from app.adapters import file_cohort_stats
from app.core.domain.insurance import DataSource, KcdCode
from app.core.errors import ValidationErr


@pytest.fixture
def tracks(tmp_path, monkeypatch):
    """합성/실제 두 트랙을 임시 폴더로 옮긴다."""
    submissions = tmp_path / "demo" / "submissions"
    synthetic = tmp_path / "cohort" / "synthetic"
    real = tmp_path / "cohort" / "verified_real"
    for d in (submissions, synthetic, real):
        d.mkdir(parents=True)
    (synthetic / "events.jsonl").write_text("", encoding="utf-8")
    (real / "events.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr(demo, "_SUBMISSIONS", submissions)
    monkeypatch.setattr(demo, "_COHORT_EVENTS", synthetic / "events.jsonl")
    monkeypatch.setattr(file_cohort_stats, "_BASE", tmp_path / "cohort")
    return {"submissions": submissions, "synthetic": synthetic, "real": real}


def _submit(client_ref="sim-1", code="S72.0", outcome="paid"):
    return demo.store({
        "client_ref": client_ref, "insurer": "테스트화재",
        "kcd_codes": [code], "outcome": outcome,
    })


def _n(source: DataSource, code="S72.0") -> int:
    return file_cohort_stats.fetch(
        kcd_code=KcdCode(version_label="", code=code, name_ko=""),
        product_id="", age_band=None, data_source=source,
    ).n


# ── 1. 제출만으로는 안 움직인다 ──────────────────────────────────────────
def test_제출만으로는_집계가_움직이지_않는다(tracks):
    """★수직 슬라이스의 앞쪽 절반. `unverified` 는 세지 않는다."""
    _submit()
    _submit(client_ref="sim-2")

    assert _n(DataSource.SYNTHETIC) == 0, "미검증 제출이 통계에 들어갔다 — fail-open 이다."
    assert demo.counts() == {"submitted": 2, "promoted": 0, "pending": 2}


# ── 2. 승격해야 움직인다 ────────────────────────────────────────────────
def test_승격하면_합성_표본이_하나_늘어난다(tracks):
    """★뒤쪽 절반. 이 순간이 제품의 증명이다."""
    res = _submit()
    before = _n(DataSource.SYNTHETIC)

    demo.promote(res.submission_id, method=demo.METHOD_ADMIN, actor="opsadmin")

    assert _n(DataSource.SYNTHETIC) == before + 1


def test_같은_제출을_두_번_승격할_수_없다(tracks):
    """중복 승격이 통과하면 표본이 부풀려진다."""
    res = _submit()
    demo.promote(res.submission_id, method=demo.METHOD_ADMIN, actor="opsadmin")

    with pytest.raises(ValidationErr, match="이미 승격"):
        demo.promote(res.submission_id, method=demo.METHOD_ADMIN, actor="opsadmin")


# ── 3. ★★혼합 차단 ─────────────────────────────────────────────────────
def test_합성_승격이_실제_트랙을_바꾸지_못한다(tracks):
    """★★계획서 §5-1 이 요구한 **보증** 층.

    합성 쪽에서 무엇을 하든 `verified_real` 은 0 이어야 한다.
    """
    assert _n(DataSource.VERIFIED_REAL) == 0

    for i in range(5):
        res = _submit(client_ref=f"sim-{i}")
        demo.promote(res.submission_id, method=demo.METHOD_SIMULATED, actor="simulator")

    assert _n(DataSource.SYNTHETIC) == 5
    assert _n(DataSource.VERIFIED_REAL) == 0, "합성 승격이 실제 통계로 샜다."

    #: ★파일 수준에서도 확인한다 — 준비된 빈 파일에 실제 이벤트가 생기지 않아야 한다.
    assert (tracks["real"] / "events.jsonl").read_text(encoding="utf-8") == ""


def test_합성_저장소는_실제_경로_문자열을_갖지_않는다():
    """★구조로 막는다 — 섞으려면 코드를 새로 써야 한다.

    `data_source` 컬럼 하나로 갈랐다면 `WHERE` 를 빠뜨리는 순간 섞인다.
    이 모듈이 실제 트랙 이름을 아예 모르는지 소스에서 확인한다.
    """
    import io
    import tokenize

    src = pathlib.Path(demo.__file__).read_text(encoding="utf-8")
    #: ★독스트링·주석에는 **설명을 위해** 등장한다(이 모듈은 분리를 문서화한다).
    #:   그래서 줄 앞글자로 거르면 안 되고, 토큰 단위로 문자열·주석을 걷어내야 한다.
    code_tokens = [
        t.string
        for t in tokenize.generate_tokens(io.StringIO(src).readline)
        if t.type not in (tokenize.COMMENT, tokenize.STRING)
    ]
    assert "verified_real" not in "".join(code_tokens), (
        "합성 저장소가 실행 코드에서 실제 트랙 경로를 안다 — 분리가 깨졌다."
    )


# ── 4. 승격 방법이 남는다 ───────────────────────────────────────────────
@pytest.mark.parametrize(
    "method,actor",
    [(demo.METHOD_ADMIN, "opsadmin"), (demo.METHOD_SIMULATED, "simulator")],
)
def test_승격_방법과_주체가_이벤트에_남는다(tracks, method, actor):
    """★"관리자가 검수함"과 "시뮬레이터가 만듦"이 구분돼야 한다.

    섞어서 기록하면 합성 표본이 사람이 검수한 것처럼 보인다.
    """
    res = _submit()
    event = demo.promote(res.submission_id, method=method, actor=actor)

    assert event["verification_method"] == method
    assert event["verified_by"] == actor
    assert event["data_source"] == "synthetic"

    line = json.loads((tracks["synthetic"] / "events.jsonl").read_text(encoding="utf-8").strip())
    assert line["verification_method"] == method


def test_승격_방법을_지어낼_수_없다(tracks):
    """`method` 화이트리스트 — 임의 문자열을 남기면 나중에 설명할 수 없다."""
    res = _submit()
    with pytest.raises(ValidationErr, match="승격 방법"):
        demo.promote(res.submission_id, method="verified_by_insurer", actor="x")


def test_제출은_클라이언트_주장을_무시하고_미검증으로_고정된다(tracks):
    """남이 자기 데이터를 스스로 '검증됨'이라 하면 그건 검증이 아니다."""
    res = demo.store({
        "client_ref": "sim-liar", "insurer": "테스트화재",
        "kcd_codes": ["S72.0"], "outcome": "paid",
        "verification": "confirmed",  # ← 주장
    })
    saved = json.loads(
        next(tracks["submissions"].rglob("*.json")).read_text(encoding="utf-8")
    )
    assert saved["verification"] == "unverified"
    assert res.stored is True


def test_알_수_없는_outcome_은_거부한다(tracks):
    with pytest.raises(ValidationErr, match="outcome"):
        demo.store({"client_ref": "sim-x", "outcome": "아마도지급"})
