"""시뮬레이션 실행기 — 제어 계약을 고정한다.

★이 파일이 지키는 명제

    1. 이미 돌고 있으면 **거절한다**(조용히 덮어쓰면 두 벌이 같은 파일에 쓴다).
    2. 정지는 **협조적**이다 — 강제 종료하지 않고 루프가 스스로 멈춘다.
    3. 상한을 넘는 요청은 **거절한다**(화면에서 실수로 큰 수를 넣어 서버를 묶지 않게).
    4. 초기화는 **합성 트랙만** 지운다. 실행 중에는 거절한다.
    5. 실행기는 실제 트랙 경로를 **모른다**.

★네트워크를 타지 않는다 — `_post` 를 대역으로 바꿔 결정론적으로 돌린다.
  실제 HTTP 경로 자체는 `test_demo_track_isolation.py` 와 e2e 확인이 담당한다.
"""

from __future__ import annotations

import io
import pathlib
import time
import tokenize

import pytest

from app.adapters import demo_simulator as sim
from app.core.errors import ConflictErr, ValidationErr


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """상태와 파일 경로를 매 테스트마다 초기화한다."""
    from app.adapters import demo_submission_store as demo

    subs = tmp_path / "demo" / "submissions"
    syn = tmp_path / "cohort" / "synthetic"
    real = tmp_path / "cohort" / "verified_real"
    for d in (subs, syn, real):
        d.mkdir(parents=True)

    monkeypatch.setattr(demo, "_SUBMISSIONS", subs)
    monkeypatch.setattr(demo, "_COHORT_EVENTS", syn / "events.jsonl")
    monkeypatch.setattr(demo, "_VERIFICATION_EVENTS", tmp_path / "demo" / "verifications" / "events.jsonl")
    sim._state.__init__()  # type: ignore[misc]
    yield {"subs": subs, "syn": syn, "real": real}
    sim._state.stop_requested = True
    for _ in range(50):
        if not sim.status()["running"]:
            break
        time.sleep(0.05)
    sim._state.__init__()  # type: ignore[misc]


def _fake_post(counter):
    """`/v1/demo/observations` 를 흉내낸다 — 실제 저장소에 넣어 승격까지 이어지게."""
    from app.adapters import demo_submission_store as demo

    def post(base, path, body, timeout=20):
        counter.append(base + path)
        res = demo.store(body, auto_validate=bool(body.get("auto_validate")))
        return 202, {
            "submission_id": res.submission_id,
            "duplicate": res.duplicate,
            "promoted": res.promoted,
            "verification": res.verification,
            "reason_codes": list(res.reason_codes),
        }

    return post


def _wait_done(timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if not sim.status()["running"]:
            return True
        time.sleep(0.05)
    return False


def test_시작하면_합성_제출이_쌓이고_상태가_끝난다(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(sim, "_post", _fake_post(calls))

    sim.start(base="http://x", agents=3, cases=2, codes=["S72.0"],
              delay_ms=0, auto_verify=False, seed=1)
    assert _wait_done(), "시뮬레이션이 끝나지 않았습니다."

    st = sim.status()
    assert st["planned"] == 6
    assert st["submitted"] == 6
    assert st["promoted"] == 0, "auto_verify=False 인데 승격됐다."
    assert st["stopped_by"] == "completed"
    #: ★HTTP 경로로 갔는지 확인한다 — 저장소를 직접 부르면 시연이 성립하지 않는다.
    assert all(c.endswith("/v1/demo/observations") for c in calls)


def test_자동모드는_합성정합성_게이트_통과분만_승격한다(monkeypatch, _isolated):
    import json

    monkeypatch.setattr(sim, "_post", _fake_post([]))
    sim.start(base="http://x", agents=2, cases=2, codes=["S72.0"],
              delay_ms=0, auto_verify=True, seed=1)
    assert _wait_done()

    assert sim.status()["promoted"] == 4
    lines = (_isolated["syn"] / "events.jsonl").read_text(encoding="utf-8").splitlines()
    methods = {json.loads(x)["verification_method"] for x in lines if x.strip()}
    assert methods == {"simulated_consistency"}, (
        "합성 정합성 검사를 사람이 검수한 것처럼 기록하면 안 된다."
    )


def test_이미_실행_중이면_거절한다(monkeypatch):
    monkeypatch.setattr(sim, "_post", _fake_post([]))
    sim.start(base="http://x", agents=5, cases=5, codes=["S72.0"],
              delay_ms=200, auto_verify=False, seed=1)
    try:
        with pytest.raises(ConflictErr, match="이미"):
            sim.start(base="http://x", agents=1, cases=1, codes=["S72.0"],
                      delay_ms=0, auto_verify=False, seed=1)
    finally:
        sim.stop()
        _wait_done()


def test_정지하면_계획보다_적게_만들고_사용자정지로_남는다(monkeypatch):
    monkeypatch.setattr(sim, "_post", _fake_post([]))
    sim.start(base="http://x", agents=20, cases=5, codes=["S72.0"],
              delay_ms=60, auto_verify=False, seed=1)
    time.sleep(0.35)
    sim.stop()
    assert _wait_done()

    st = sim.status()
    assert st["stopped_by"] == "user"
    assert st["submitted"] < st["planned"], "정지했는데 계획대로 다 만들었다."


def test_실행_중이_아니면_정지도_거절한다():
    with pytest.raises(ConflictErr, match="실행 중인"):
        sim.stop()


@pytest.mark.parametrize(
    "kwargs,msg",
    [
        (dict(agents=0, cases=1), "에이전트 수"),
        (dict(agents=sim.MAX_AGENTS + 1, cases=1), "에이전트 수"),
        (dict(agents=1, cases=0), "건수"),
        (dict(agents=sim.MAX_AGENTS, cases=sim.MAX_CASES), "총 생성량"),
    ],
)
def test_상한을_넘는_요청은_거절한다(kwargs, msg):
    with pytest.raises(ValidationErr, match=msg):
        sim.start(base="http://x", codes=["S72.0"], delay_ms=0,
                  auto_verify=False, seed=1, **kwargs)


def test_초기화는_합성만_지우고_실행_중에는_거절한다(monkeypatch, _isolated):
    monkeypatch.setattr(sim, "_post", _fake_post([]))
    sim.start(base="http://x", agents=2, cases=2, codes=["S72.0"],
              delay_ms=0, auto_verify=True, seed=1)
    assert _wait_done()
    assert list(_isolated["subs"].rglob("*.json")), "제출 파일이 없다."

    #: 실제 트랙에 표시 파일을 두고, 초기화가 건드리지 않는지 본다.
    keep = _isolated["real"] / "events.jsonl"
    keep.write_text("", encoding="utf-8")

    result = sim.reset()
    assert result["data_source"] == "synthetic"
    assert not list(_isolated["subs"].rglob("*.json"))
    assert keep.exists(), "★초기화가 실제 트랙을 지웠다."


def test_실행_중_초기화는_거절한다(monkeypatch):
    monkeypatch.setattr(sim, "_post", _fake_post([]))
    sim.start(base="http://x", agents=20, cases=5, codes=["S72.0"],
              delay_ms=60, auto_verify=False, seed=1)
    try:
        with pytest.raises(ConflictErr, match="실행 중"):
            sim.reset()
    finally:
        sim.stop()
        _wait_done()


def test_실행기는_실제_트랙_경로를_모른다():
    """★구조로 막는다 — 실행 코드에 `verified_real` 이 없어야 한다."""
    src = pathlib.Path(sim.__file__).read_text(encoding="utf-8")
    code = "".join(
        t.string
        for t in tokenize.generate_tokens(io.StringIO(src).readline)
        if t.type not in (tokenize.COMMENT, tokenize.STRING)
    )
    assert "verified_real" not in code


def test_같은_시드는_같은_결과를_만든다(monkeypatch, _isolated):
    """발표 때마다 숫자가 달라지면 '그때 그 화면'을 다시 만들 수 없다."""
    import json

    def run_once():
        monkeypatch.setattr(sim, "_post", _fake_post([]))
        sim.start(base="http://x", agents=3, cases=3, codes=list(sim.CODES),
                  delay_ms=0, auto_verify=False, seed=777)
        assert _wait_done()
        files = _isolated["subs"].rglob("*.json")
        #: ★★**파일 순서로 비교하지 않는다.**
        #:
        #:   처음엔 `sorted(rglob(...))` 순서를 그대로 비교했다가 무작위로 실패했다.
        #:   파일명이 `{초 단위 타임스탬프}_{해시}` 라서 두 실행이 **초 경계를 넘으면**
        #:   같은 폴더 안 정렬 순서가 달라진다. 시드가 보장하는 것은 **생성 내용**이지
        #:   파일 나열 순서가 아니다 — 계약이 아닌 것을 단언하면 테스트가 거짓말을 한다.
        codes: dict[str, int] = {}
        for f in files:
            for c in json.loads(f.read_text(encoding="utf-8"))["kcd_codes"]:
                codes[c] = codes.get(c, 0) + 1
        return codes

    first = run_once()
    for f in list(_isolated["subs"].rglob("*.json")):
        f.unlink()
    sim._state.__init__()  # type: ignore[misc]
    second = run_once()

    assert first == second and first, "같은 시드인데 생성 내용이 달라졌다."


def test_같은_시드로_두번째_실행해도_전부_중복이_되지_않는다(monkeypatch, _isolated):
    """seed는 생성 내용 재현용이지 실행 멱등성 키가 아니다.

    이전에는 client_ref와 payload만 해시해 같은 seed의 두 번째 실행 36건이
    전부 첫 실행의 재전송으로 처리됐다. 실행은 새로 받되, 실행 안의 같은 사례를
    재전송할 때만 같은 idempotency_key를 사용해야 한다.
    """
    monkeypatch.setattr(sim, "_post", _fake_post([]))

    sim.start(base="http://x", agents=3, cases=2, codes=["S72.0"],
              delay_ms=0, auto_verify=False, seed=20260804)
    assert _wait_done()
    first_run_id = sim.status()["run_id"]
    assert sim.status()["submitted"] == 6
    assert sim.status()["duplicated"] == 0

    sim.start(base="http://x", agents=3, cases=2, codes=["S72.0"],
              delay_ms=0, auto_verify=False, seed=20260804)
    assert _wait_done()
    second = sim.status()

    assert second["run_id"] != first_run_id
    assert second["submitted"] == 6
    assert second["duplicated"] == 0
    assert len(list(_isolated["subs"].rglob("*.json"))) == 12
