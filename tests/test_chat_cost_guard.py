"""공개 챗봇 과금 보호 — 야간 작업자 2(`tasks/02_공개챗봇_과금보호.md`).

★이 파일은 **PostgreSQL 이 없어도 돈다.** 전역 `tests/conftest.py` 는 import 시점에
  127.0.0.1:5433 에 붙는다. 그게 없으면 이렇게 돌린다.

    LLM_CHAT_ENABLED=false LLM_PROVIDER=local SECRET_KEY=x \\
      python -m pytest --noconftest -p no:cacheprovider tests/test_chat_cost_guard.py -q

  (PowerShell 은 `$env:LLM_CHAT_ENABLED="false"` 식으로 먼저 넣는다.)
  conftest 를 타는 정상 실행에서도 그대로 돈다 — 아래 `setdefault` 는 그때 무시된다.

★외부 생성 API 호출 0. 모든 LLM 은 `_FakeModel` 이다.

지키는 것(작업지시서의 검사 항목 그대로):
  1. 동일 `통원 뜻` 동시 20건 → fake provider 호출 1회
  2. insurer / model / 릴리스가 다르면 합쳐지지 않는다
  3. leader 실패 시 대기 요청이 멈추지 않고 **같은 오류**를 받는다
  4. rate limit 429 + Retry-After
  5. S7 릴리스 변경 뒤 옛 응답이 나오지 않는다
  6. `llm.used` 메타가 실제 호출과 일치한다(`llm.source="call"` 수 == provider 호출 수)
  + 보장 질문·못 찾은 용어는 LLM 을 부르지 않는다 / 전역 LLM 예산 초과는 429
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

#: ★app import 전에 넣는다. 전역 conftest 가 이미 넣었으면 그 값이 이긴다.
os.environ.setdefault("LLM_CHAT_ENABLED", "false")
os.environ.setdefault("LLM_PROVIDER", "local")
os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-prod")
os.environ.setdefault("DEMO_STORE_BACKEND", "file")

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.application import chat_call_guard as g  # noqa: E402
from app.application.grounded_term_answer import explain_term  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.errors import (  # noqa: E402
    InfraError,
    LLMOutputError,
    RateLimitErr,
    register_exception_handlers,
)
from app.core.ports.glossary import TermPassage  # noqa: E402

get_settings.cache_clear()


# ================================================================ 순수 단위(스레드 결정론)


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_limiter_는_상한까지_허용하고_초과는_retry_after_를_준다():
    clock = _Clock()
    lim = g.SlidingWindowLimiter(limit=3, window_seconds=60, clock=clock)
    assert [lim.consume("ip").allowed for _ in range(3)] == [True, True, True]
    d = lim.consume("ip")
    assert d.allowed is False and d.retry_after_seconds == 60
    #: 다른 키는 별개다.
    assert lim.consume("other").allowed is True
    #: 슬라이딩 — 첫 요청이 창 밖으로 나가면 하나 다시 열린다.
    clock.t += 61
    assert lim.consume("ip").allowed is True


def test_limiter_0_은_꺼진_것이다():
    lim = g.SlidingWindowLimiter(limit=0)
    assert all(lim.consume("ip").allowed for _ in range(100))


def test_single_flight_는_동시_20건을_1회_호출로_합친다():
    """★결정론: 20 스레드가 전부 진입한 뒤에야 leader 가 끝난다."""
    guard = g.ChatLlmGuard(client_limit_per_minute=0, llm_calls_per_minute=0, cache_ttl_seconds=0)
    n = 20
    calls = []
    release = threading.Event()

    def produce() -> str:
        calls.append(1)
        release.wait(5)
        return "설명"

    key = g.build_key(["통원", "", "openai", "m", g.PROMPT_VERSION, "fp", "q"])
    with ThreadPoolExecutor(max_workers=n) as pool:
        futs = [pool.submit(guard.explain, key=key, produce=produce) for _ in range(n)]
        #: 대기자 19명이 다 줄을 설 때까지 기다린 뒤 leader 를 풀어 준다.
        deadline = time.monotonic() + 5
        while guard.single_flight.waiters(key) < n - 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert guard.single_flight.waiters(key) == n - 1
        release.set()
        outs = [f.result(timeout=5) for f in futs]

    assert len(calls) == 1
    assert guard.provider_calls == 1
    assert {o.value for o in outs} == {"설명"}
    assert sum(o.source == g.SOURCE_CALL for o in outs) == 1
    assert sum(o.source == g.SOURCE_SINGLE_FLIGHT for o in outs) == n - 1


def test_single_flight_는_다른_키를_합치지_않는다():
    guard = g.ChatLlmGuard(client_limit_per_minute=0, llm_calls_per_minute=0, cache_ttl_seconds=0)
    calls = []
    release = threading.Event()

    def produce() -> str:
        calls.append(1)
        release.wait(5)
        return "x"

    base = ["통원", "", "openai", "m", g.PROMPT_VERSION, "fp", "q"]
    variants = [
        base,
        base[:1] + ["가보험"] + base[2:],  # insurer
        base[:3] + ["other-model"] + base[4:],  # model
        base[:5] + ["fp2"] + base[6:],  # release
    ]
    keys = [g.build_key(v) for v in variants]
    assert len(set(keys)) == 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(guard.explain, key=k, produce=produce) for k in keys]
        deadline = time.monotonic() + 5
        while len(calls) < 4 and time.monotonic() < deadline:
            time.sleep(0.005)
        release.set()
        [f.result(timeout=5) for f in futs]
    assert len(calls) == 4


def test_leader_가_실패하면_대기자도_같은_오류를_받고_멈추지_않는다():
    guard = g.ChatLlmGuard(client_limit_per_minute=0, llm_calls_per_minute=0, cache_ttl_seconds=30)
    n = 8
    release = threading.Event()
    boom = InfraError("LLM 서버에 연결할 수 없습니다.")
    calls = []

    def produce() -> str:
        calls.append(1)
        release.wait(5)
        raise boom

    key = g.build_key(["k"])
    with ThreadPoolExecutor(max_workers=n) as pool:
        futs = [pool.submit(guard.explain, key=key, produce=produce) for _ in range(n)]
        deadline = time.monotonic() + 5
        while guard.single_flight.waiters(key) < n - 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        release.set()
        errors = []
        for f in futs:
            with pytest.raises(InfraError) as ei:
                f.result(timeout=5)  # ★timeout 이 「멈추지 않는다」의 증명이다
            errors.append(ei.value)
    assert all(e is boom for e in errors)
    #: 실패는 캐시되지 않는다. 다음 요청은 다시 부른다.
    assert len(guard.cache) == 0
    assert guard.single_flight.in_flight() == 0
    with pytest.raises(InfraError):
        guard.explain(key=key, produce=produce)
    assert len(calls) == 2


class _BoundaryFaultModel:
    """외부 호출 없이 provider 완료 경계를 재현한다."""

    def __init__(self, *, release: threading.Event, result: str = "", error=None):
        self.release = release
        self.result = result
        self.error = error
        self.calls = 0
        self._lock = threading.Lock()

    def complete(self, prompt, **kwargs):
        with self._lock:
            self.calls += 1
        assert self.release.wait(5), "fault provider was not released"
        if self.error is not None:
            raise self.error
        return self.result


def _run_20_way_boundary_fault(model: _BoundaryFaultModel):
    """20건을 한 flight에 세우고 모두의 종료/오류 동일성을 돌려준다."""
    guard = g.ChatLlmGuard(
        client_limit_per_minute=0,
        llm_calls_per_minute=0,
        cache_ttl_seconds=0,
        wait_timeout_seconds=5,
    )
    key = g.build_key(["통원", "", "fake", "fault-model", g.PROMPT_VERSION, "fp", "quotes"])

    def produce():
        return explain_term(
            term="통원",
            quotes=["통원은 의료기관에 입원하지 않고 방문하여 치료받는 것이다."],
            model=model,
        )

    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(guard.explain, key=key, produce=produce) for _ in range(20)]
        deadline = time.monotonic() + 5
        while guard.single_flight.waiters(key) < 19 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert guard.single_flight.waiters(key) == 19
        model.release.set()
        errors = []
        for future in futures:
            with pytest.raises(Exception) as caught:
                future.result(timeout=5)
            errors.append(caught.value)

    assert model.calls == guard.provider_calls == 1
    assert len(errors) == 20
    assert all(error is errors[0] for error in errors)
    assert len(guard.cache) == 0
    assert guard.single_flight.in_flight() == 0
    return errors[0]


def test_20way_provider_429_preserves_retry_after_and_same_error():
    provider_error = RateLimitErr(
        "LLM 공급자 요청 한도 초과.", retry_after_seconds="17"
    )
    error = _run_20_way_boundary_fault(
        _BoundaryFaultModel(release=threading.Event(), error=provider_error)
    )
    assert error is provider_error
    assert type(error) is RateLimitErr
    assert error.http_status == 429
    assert error.error_code == "rate_limit_exceeded"
    assert error.headers == {"Retry-After": "17"}


def test_20way_provider_timeout_has_same_explicit_infra_error():
    provider_error = InfraError("LLM 서버 응답 시간이 초과되었습니다.")
    error = _run_20_way_boundary_fault(
        _BoundaryFaultModel(release=threading.Event(), error=provider_error)
    )
    assert error is provider_error
    assert type(error) is InfraError
    assert error.http_status == 503
    assert error.error_code == "infra_error"


def test_20way_empty_provider_response_has_same_explicit_output_error():
    error = _run_20_way_boundary_fault(
        _BoundaryFaultModel(release=threading.Event(), result="  \n")
    )
    assert type(error) is LLMOutputError
    assert error.http_status == 502
    assert error.error_code == "llm_output_error"
    assert "빈 응답" in error.message


def test_20way_coverage_assertion_is_rejected_with_same_output_error():
    error = _run_20_way_boundary_fault(
        _BoundaryFaultModel(
            release=threading.Event(),
            result="이 경우 보험금이 지급됩니다.",
        )
    )
    assert type(error) is LLMOutputError
    assert error.http_status == 502
    assert error.error_code == "llm_output_error"
    assert "보장·지급 여부를 단정" in error.message


def test_대기자는_leader_타임아웃에_영원히_붙들리지_않는다():
    guard = g.ChatLlmGuard(
        client_limit_per_minute=0, llm_calls_per_minute=0, cache_ttl_seconds=0,
        wait_timeout_seconds=0.2,
    )
    release = threading.Event()
    key = g.build_key(["slow"])

    def produce() -> str:
        release.wait(5)
        return "late"

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader = pool.submit(guard.explain, key=key, produce=produce)
        deadline = time.monotonic() + 5
        while guard.single_flight.in_flight() < 1 and time.monotonic() < deadline:
            time.sleep(0.005)
        waiter = pool.submit(guard.explain, key=key, produce=produce)
        with pytest.raises(g.SingleFlightTimeout):
            waiter.result(timeout=5)
        release.set()
        assert leader.result(timeout=5).source == g.SOURCE_CALL


def test_캐시는_TTL_안에서만_재사용하고_키가_바뀌면_miss_다():
    clock = _Clock()
    guard = g.ChatLlmGuard(
        client_limit_per_minute=0, llm_calls_per_minute=0, cache_ttl_seconds=30, clock=clock
    )
    calls = []

    def produce() -> str:
        calls.append(1)
        return f"v{len(calls)}"

    k1 = g.build_key(["통원", "", "openai", "m", g.PROMPT_VERSION, "release-A", "q"])
    assert guard.explain(key=k1, produce=produce).source == g.SOURCE_CALL
    hit = guard.explain(key=k1, produce=produce)
    assert hit.source == g.SOURCE_CACHE and hit.value == "v1"
    #: ★릴리스 지문이 바뀌면 옛 답이 나오지 않는다.
    k2 = g.build_key(["통원", "", "openai", "m", g.PROMPT_VERSION, "release-B", "q"])
    assert guard.explain(key=k2, produce=produce).value == "v2"
    #: TTL 만료.
    clock.t += 31
    assert guard.explain(key=k1, produce=produce).value == "v3"
    assert guard.provider_calls == 3


def test_전역_LLM_예산은_실제_호출만_소비한다():
    clock = _Clock()
    guard = g.ChatLlmGuard(
        client_limit_per_minute=0, llm_calls_per_minute=2, cache_ttl_seconds=30, clock=clock
    )
    produce = lambda: "ok"  # noqa: E731
    k1, k2, k3 = (g.build_key([c]) for c in "abc")
    guard.explain(key=k1, produce=produce)
    guard.explain(key=k1, produce=produce)  # cache — 예산 안 씀
    guard.explain(key=k2, produce=produce)
    with pytest.raises(g.LlmBudgetExceeded) as ei:
        guard.explain(key=k3, produce=produce)
    assert ei.value.retry_after_seconds >= 1
    assert guard.provider_calls == 2
    clock.t += 61
    assert guard.explain(key=k3, produce=produce).source == g.SOURCE_CALL


def test_build_key_는_경계가_밀려도_같은_키를_내지_않는다():
    assert g.build_key(["ab", "c"]) != g.build_key(["a", "bc"])
    assert g.fingerprint_of({"b": 1, "a": 2}) == g.fingerprint_of({"a": 2, "b": 1})
    assert g.fingerprint_of(["x"]) != g.fingerprint_of(["y"])


# ================================================================ 라우터(TestClient, PG 무관)


def _p(text: str, *, insurer="가보험") -> TermPassage:
    return TermPassage(
        kind="clause", sha256="a" * 64, insurer=insurer, qualified_no="보통약관/2.",
        section="보통약관", title="용어의 정의", page_from=3, page_to=3,
        content_hash="deadbeefcafe", text=text,
    )


class _Glossary:
    def __init__(self, rows, meta):
        self.rows = rows
        self._meta = meta

    def find(self, term, *, insurer=None, limit=20):
        hit = [r for r in self.rows if term in r.text and (not insurer or r.insurer == insurer)]
        return hit[:limit] if limit else hit

    def meta(self):
        return dict(self._meta)


_ROWS = [
    _p("2. (용어의 정의)\n통원 의료기관에 입원하지 않고 방문하여 치료받는 것"),
    _p("2. (용어의 정의)\n통원 다른 보험사 정의 문장입니다", insurer="나보험"),
    _p("2. (용어의 정의)\n도수치료 치료자가 손을 이용해 실시하는 치료행위"),
]


class _FakeModel:
    """외부 호출 0. `block` 을 주면 그 이벤트가 풀릴 때까지 leader 를 붙든다."""

    def __init__(self, *, block: threading.Event | None = None, fail: Exception | None = None):
        self.calls = 0
        self.entered = threading.Event()
        self.block = block
        self.fail = fail
        self._lock = threading.Lock()

    def complete(self, prompt, **kwargs):
        with self._lock:
            self.calls += 1
            n = self.calls
        self.entered.set()
        if self.block is not None:
            self.block.wait(5)
        if self.fail is not None:
            raise self.fail
        return f"통원은 입원하지 않고 방문해 치료받는 것입니다. #{n}"


@pytest.fixture()
def chat_env(monkeypatch):
    """LLM 켠 라우터 + fake 모델 + 설정 주입. 매 테스트마다 가드를 새로 만든다."""
    from app import composition
    from app.routers import chat as chat_router

    chat_router._reset_guard_for_tests()
    state = SimpleNamespace(
        glossary=_Glossary(_ROWS, {"built_from": "s7", "s7_serving": True, "occ": 850}),
        model=_FakeModel(),
        model_name="fake-model",
    )

    def configure(*, rate_limit=100, llm_budget=0, cache_ttl=30.0, trust_xff=False):
        chat_router._reset_guard_for_tests()
        monkeypatch.setattr(
            chat_router,
            "_guard_settings",
            lambda: SimpleNamespace(
                CHAT_RATE_LIMIT_PER_MINUTE=rate_limit,
                CHAT_LLM_MAX_CALLS_PER_MINUTE=llm_budget,
                CHAT_LLM_CACHE_TTL_SECONDS=cache_ttl,
                CHAT_TRUST_FORWARDED_FOR=trust_xff,
                LLM_REQUEST_TIMEOUT_SECONDS=5.0,
            ),
        )

    configure()
    monkeypatch.setattr(composition, "build_glossary", lambda: state.glossary)
    monkeypatch.setattr(chat_router, "_source", lambda: state.glossary)
    monkeypatch.setattr(chat_router, "_model", lambda: state.model)
    monkeypatch.setattr(
        chat_router,
        "get_settings",
        lambda: SimpleNamespace(LLM_CHAT_ENABLED=True, LLM_PROVIDER="openai"),
    )
    monkeypatch.setattr(chat_router, "get_active_model", lambda: state.model_name)
    state.app = FastAPI()
    register_exception_handlers(state.app)
    state.app.include_router(chat_router.router)
    state.configure = configure
    state.router = chat_router
    yield state
    chat_router._reset_guard_for_tests()


def _post(app, payload, headers=None):
    #: 스레드마다 자기 클라이언트를 쓴다. 한 TestClient 를 여러 스레드가 나눠 쓰지 않는다.
    with TestClient(app) as c:
        return c.post("/v1/chat", json=payload, headers=headers or {})


def _concurrent(app, payload, n, *, headers=None):
    with ThreadPoolExecutor(max_workers=n) as pool:
        futs = [pool.submit(_post, app, payload, headers) for _ in range(n)]
        return [f.result(timeout=15) for f in futs]


def test_동일_통원뜻_동시_20건은_provider_를_1번만_부른다(chat_env):
    """★작업지시서 검사 1. 캐시를 끄고 single-flight 만으로 본다."""
    chat_env.configure(cache_ttl=0)
    release = threading.Event()
    chat_env.model = _FakeModel(block=release)
    n = 20

    with ThreadPoolExecutor(max_workers=n) as pool:
        futs = [pool.submit(_post, chat_env.app, {"message": "통원 뜻"}) for _ in range(n)]
        assert chat_env.model.entered.wait(5), "leader 가 provider 에 진입하지 않았다"
        #: 나머지 19건이 대기열에 서도록 잠깐 준다. 늦게 온 요청이 있어도 아래 assert 가 잡는다.
        deadline = time.monotonic() + 3
        guard = chat_env.router._guard()
        while guard.single_flight.in_flight() and sum(
            guard.single_flight.waiters(k) for k in list(guard.single_flight._flights)
        ) < n - 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        release.set()
        rs = [f.result(timeout=15) for f in futs]

    assert [r.status_code for r in rs] == [200] * n
    bodies = [r.json() for r in rs]
    assert chat_env.model.calls == 1
    assert len({b["message"] for b in bodies}) == 1  # 전원 같은 답
    assert all(b["llm"]["used"] is True for b in bodies)
    assert all(b["llm"] == {**b["llm"], "provider": "openai", "model": "fake-model"} for b in bodies)
    #: ★`used=True` 가 20이어도 실제 호출은 `source="call"` 수와 같다.
    sources = [b["llm"]["source"] for b in bodies]
    assert sources.count("call") == chat_env.model.calls == 1
    assert set(sources) <= {"call", "single_flight"}


def test_insurer_model_release_가_다르면_합치지_않는다(chat_env):
    chat_env.configure(cache_ttl=30)
    app = chat_env.app
    assert _post(app, {"message": "통원 뜻"}).status_code == 200
    assert chat_env.model.calls == 1
    #: 같은 조건 → 캐시. 호출 없음.
    r = _post(app, {"message": "통원 뜻"})
    assert r.json()["llm"]["source"] == "cache" and chat_env.model.calls == 1
    #: insurer 가 다르면 다른 인용문·다른 키.
    r = _post(app, {"message": "통원 뜻", "insurer": "나보험"})
    assert r.json()["llm"]["source"] == "call" and chat_env.model.calls == 2
    #: model 이 다르면 다른 키.
    chat_env.model_name = "other-model"
    r = _post(app, {"message": "통원 뜻"})
    assert r.json()["llm"]["source"] == "call" and r.json()["llm"]["model"] == "other-model"
    assert chat_env.model.calls == 3
    chat_env.model_name = "fake-model"
    #: 릴리스 메타가 다르면 다른 키 — 인용문이 같아도.
    chat_env.glossary = _Glossary(_ROWS, {"built_from": "s8", "s7_serving": True, "occ": 900})
    r = _post(app, {"message": "통원 뜻"})
    assert r.json()["llm"]["source"] == "call" and chat_env.model.calls == 4


def test_S7_릴리스가_바뀌면_옛_응답이_나오지_않는다(chat_env):
    """★작업지시서 검사 5. 메타가 그대로여도 본문이 바뀌면 인용문 해시가 막는다."""
    chat_env.configure(cache_ttl=300)
    app = chat_env.app
    first = _post(app, {"message": "통원 뜻"}).json()["message"]
    assert "#1" in first
    assert _post(app, {"message": "통원 뜻"}).json()["message"] == first  # 캐시
    #: 1) 릴리스 메타 변경
    chat_env.glossary = _Glossary(_ROWS, {"built_from": "s7", "s7_serving": True, "occ": 851})
    second = _post(app, {"message": "통원 뜻"}).json()["message"]
    assert second != first and "#2" in second
    #: 2) 메타는 같은데 본문만 바뀐 색인
    rows = [_p("2. (용어의 정의)\n통원 의료기관에 입원하지 않고 **새 문장** 방문하여 치료받는 것")]
    chat_env.glossary = _Glossary(rows, {"built_from": "s7", "s7_serving": True, "occ": 851})
    third = _post(app, {"message": "통원 뜻"}).json()["message"]
    assert third != second and "#3" in third


def test_leader_실패시_대기_요청은_같은_오류를_받고_고정문구로_폴백하지_않는다(chat_env):
    """★작업지시서 검사 3."""
    chat_env.configure(cache_ttl=30)
    release = threading.Event()
    chat_env.model = _FakeModel(block=release, fail=InfraError("LLM 서버에 연결할 수 없습니다."))
    n = 8
    with ThreadPoolExecutor(max_workers=n) as pool:
        futs = [pool.submit(_post, chat_env.app, {"message": "통원 뜻"}) for _ in range(n)]
        assert chat_env.model.entered.wait(5)
        time.sleep(0.3)
        release.set()
        rs = [f.result(timeout=15) for f in futs]  # ★timeout 이 「멈추지 않는다」의 증명
    assert [r.status_code for r in rs] == [503] * n
    for r in rs:
        body = r.json()
        #: 인용문·고정문구로 200 을 내지 않는다. 오류는 고정 공개 문구다.
        assert "quotes" not in body
        assert body.get("detail") == "서비스 의존 시스템을 사용할 수 없습니다." or (
            body.get("ok") is False and "secret" not in r.text
        )
    #: 실패는 캐시되지 않는다 — 다음 요청은 다시 부른다.
    chat_env.model = _FakeModel()
    r = _post(chat_env.app, {"message": "통원 뜻"})
    assert r.status_code == 200 and r.json()["llm"]["source"] == "call"


def test_rate_limit_은_429와_Retry_After_를_준다(chat_env):
    """★작업지시서 검사 4. 색인 조회·LLM 전에 거른다."""
    chat_env.configure(rate_limit=3, cache_ttl=0)
    app = chat_env.app
    codes = [_post(app, {"message": "도수치료 뜻"}).status_code for _ in range(3)]
    assert codes == [200, 200, 200]
    r = _post(app, {"message": "도수치료 뜻"})
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    assert "요청이 너무 잦습니다" in r.json()["detail"]
    #: 차단된 요청은 provider 를 부르지 않는다(캐시 꺼 뒀으니 3회가 정확한 수다).
    assert chat_env.model.calls == 3
    #: 도움말·보장 질문도 같은 창을 쓴다 — 익명 반복 요청 자체를 세는 것이다.
    assert _post(app, {"message": "도움말"}).status_code == 429


def test_rate_limit_은_클라이언트별이고_XFF_는_신뢰_설정시에만_본다(chat_env):
    chat_env.configure(rate_limit=1, cache_ttl=30)
    app = chat_env.app
    assert _post(app, {"message": "통원 뜻"}).status_code == 200
    #: 기본은 XFF 를 무시한다 — 헤더를 바꿔도 같은 클라이언트다.
    assert _post(app, {"message": "통원 뜻"}, {"X-Forwarded-For": "192.0.2.2"}).status_code == 429
    chat_env.configure(rate_limit=1, cache_ttl=30, trust_xff=True)
    assert _post(app, {"message": "통원 뜻"}, {"X-Forwarded-For": "192.0.2.2"}).status_code == 200
    assert _post(app, {"message": "통원 뜻"}, {"X-Forwarded-For": "192.0.2.3"}).status_code == 200
    assert _post(app, {"message": "통원 뜻"}, {"X-Forwarded-For": "192.0.2.3"}).status_code == 429


def test_전역_LLM_예산_초과는_429_이고_고정문구로_바꾸지_않는다(chat_env):
    chat_env.configure(rate_limit=0, llm_budget=1, cache_ttl=30)
    app = chat_env.app
    assert _post(app, {"message": "통원 뜻"}).status_code == 200
    assert _post(app, {"message": "통원 뜻"}).json()["llm"]["source"] == "cache"  # 예산 안 씀
    r = _post(app, {"message": "도수치료 뜻"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert "설명 생성 한도" in r.json()["detail"]
    assert chat_env.model.calls == 1


def test_보장질문과_못찾은_용어는_LLM_을_부르지_않고_llm_used_가_false_다(chat_env):
    """★작업지시서 요구 6 + 검사 6(llm.used 일치)."""
    app = chat_env.app
    for msg in ["도수치료 보장되나요?", "존재할리없는낱말이 뭐야", "도움말"]:
        r = _post(app, {"message": msg})
        assert r.status_code == 200, msg
        assert r.json()["llm"] == {"used": False, "provider": None, "model": None, "source": None}
    assert chat_env.model.calls == 0


def test_llm_이_꺼져_있으면_어떤_경로도_provider_를_부르지_않는다(chat_env, monkeypatch):
    monkeypatch.setattr(
        chat_env.router, "get_settings",
        lambda: SimpleNamespace(LLM_CHAT_ENABLED=False, LLM_PROVIDER="openai"),
    )
    rs = _concurrent(chat_env.app, {"message": "통원 뜻"}, 5)
    assert all(r.status_code == 200 and r.json()["found"] for r in rs)
    assert all(r.json()["llm"]["used"] is False and r.json()["llm"]["source"] is None for r in rs)
    assert chat_env.model.calls == 0


def test_등록_에이전트와_MCP_경로는_클라이언트_상한을_안_타지만_과금은_같이_센다(chat_env):
    chat_env.configure(rate_limit=1, cache_ttl=30)
    from app.routers.chat import ChatRequest, chat_turn, chat_turn_for_registered_agent

    #: 공개 채널은 1회 뒤 429.
    assert _post(chat_env.app, {"message": "통원 뜻"}).status_code == 200
    assert _post(chat_env.app, {"message": "통원 뜻"}).status_code == 429
    #: 프로세스 안 채널은 상한 없음 — 그러나 같은 캐시를 타서 provider 호출은 늘지 않는다.
    a = chat_turn_for_registered_agent(ChatRequest(message="통원 뜻"))
    m = chat_turn(ChatRequest(message="통원 뜻"))
    assert a["llm"]["source"] == "cache" and m["llm"]["source"] == "cache"
    assert chat_env.model.calls == 1


def test_비밀_문자열은_429_503_응답에_나오지_않는다(chat_env):
    chat_env.configure(rate_limit=1, cache_ttl=0)
    chat_env.model = _FakeModel(fail=InfraError("sk-live-SECRET-KEY postgresql://u:pw@h/db"))
    r1 = _post(chat_env.app, {"message": "통원 뜻"})
    r2 = _post(chat_env.app, {"message": "통원 뜻"})
    assert (r1.status_code, r2.status_code) == (503, 429)
    for r in (r1, r2):
        assert "SECRET" not in r.text and "pw@" not in r.text
