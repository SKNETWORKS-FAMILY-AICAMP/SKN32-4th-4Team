"""공개 챗봇 LLM 호출 보호 — rate limit · single-flight · 짧은 캐시.

★왜 있는가. 익명 `/v1/chat` 은 **로그인 없이** 외부 LLM 과금으로 이어진다.
  같은 질문 20건이 동시에 오면 provider 를 20번 부르고, 봇이 돌리면 상한이 없다.
  이 모듈은 그 사이에 서서 세 가지만 한다.

    1. `SlidingWindowLimiter`  클라이언트별 요청 수 상한 → 초과는 **명시적 429**
    2. `SingleFlight`          같은 키의 동시 요청은 **하나만** provider 로 보내고
                              나머지는 같은 결과 또는 **같은 예외**를 받는다
    3. `TtlCache`              짧은 TTL 응답 캐시(선택). 키에 릴리스 지문이 들어간다

★하지 않는 것.
  - 조용한 고정문구 폴백. leader 가 실패하면 대기자도 **같은 실패**를 받는다.
    「응답 생성 실패」를 「약관 원문만 드립니다」로 바꿔치기하면 상위가 실패를 못 본다.
  - 보장 질문·못 찾은 용어에 대한 개입. 그 경로는 애초에 LLM 을 부르지 않는다
    (`app/routers/chat.py`). 여기는 **부르기로 결정된 뒤**에만 선다.

★이 모듈은 프레임워크를 모른다(Application 계층). HTTP 상태 변환은 라우터가 한다.
★프로세스 안 메모리다. uvicorn worker 가 N 개면 상한도 N 배다 — 단일 worker 배포
  또는 앞단 프록시의 rate limit 과 함께 쓴다. 이 한계는 status 에 적는다.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

#: 프롬프트가 바뀌면 이 값을 올린다. 같은 term/quotes 라도 프롬프트가 다르면
#: 다른 답이 나와야 하므로 single-flight/캐시 키에 들어간다.
#: 원문 프롬프트는 `app/application/grounded_term_answer.py` 에 있다.
PROMPT_VERSION = "term_explanation/v1"

SOURCE_CALL = "call"  # 이 성공 응답의 leader가 provider 호출 경로를 탔다
SOURCE_SINGLE_FLIGHT = "single_flight"  # 동시에 진행 중이던 호출의 결과를 받았다
SOURCE_CACHE = "cache"  # TTL 안의 이전 호출 결과를 받았다


# ---------------------------------------------------------------- 예외


class ChatGuardError(Exception):
    """이 모듈이 내는 예외의 베이스. 라우터가 HTTP 상태로 바꾼다."""


class RateLimited(ChatGuardError):
    """클라이언트별 요청 상한 초과."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(f"rate limited; retry after {self.retry_after_seconds}s")


class LlmBudgetExceeded(ChatGuardError):
    """프로세스 전체 LLM 호출 예산(분당) 초과. 클라이언트가 여럿이어도 과금은 하나다."""

    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__(f"llm budget exceeded; retry after {self.retry_after_seconds}s")


class SingleFlightTimeout(ChatGuardError):
    """leader 가 제한 시간 안에 끝나지 않았다. 대기자를 영원히 붙들지 않는다."""


# ---------------------------------------------------------------- 키


def build_key(parts: Iterable[str]) -> str:
    """single-flight/캐시 키. 구성 요소가 하나라도 다르면 다른 키다.

    구성(라우터가 넣는다): term · insurer · provider · model · 프롬프트 버전 ·
    릴리스 지문 · 인용문 해시. 인용문 해시를 넣는 이유 — 메타가 그대로인데
    본문만 바뀐 색인을 「같은 릴리스」로 오인해 옛 답을 주는 것을 막는다.
    """
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\x1f")  # 구분자. "ab"+"c" 와 "a"+"bc" 를 다르게 만든다
    return h.hexdigest()


def fingerprint_of(obj: object) -> str:
    """dict/list/스칼라를 결정론적으로 해시한다(릴리스 지문·인용문 해시용)."""
    import json

    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- rate limit


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    retry_after_seconds: int
    remaining: int


class SlidingWindowLimiter:
    """키별 슬라이딩 윈도 카운터. `limit <= 0` 이면 꺼진 것이다(항상 허용).

    ★고정 윈도(매분 리셋)를 쓰지 않는다 — 경계에서 2배가 통과한다.
    """

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = int(limit)
        self.window = float(window_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}
        self._ops = 0

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def consume(self, key: str) -> LimitDecision:
        if not self.enabled:
            return LimitDecision(True, 0, -1)
        now = self._clock()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            cutoff = now - self.window
            while q and q[0] <= cutoff:
                q.popleft()
            if len(q) >= self.limit:
                retry = math.ceil(q[0] + self.window - now)
                return LimitDecision(False, max(1, retry), 0)
            q.append(now)
            self._ops += 1
            if self._ops % 256 == 0:
                self._prune(cutoff)
            return LimitDecision(True, 0, self.limit - len(q))

    def _prune(self, cutoff: float) -> None:
        """오래 안 온 키를 버린다. 호출자가 lock 을 쥔 상태에서 부른다."""
        dead = [k for k, q in self._hits.items() if not q or q[-1] <= cutoff]
        for k in dead:
            del self._hits[k]

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._ops = 0


# ---------------------------------------------------------------- single-flight


class _Flight:
    __slots__ = ("done", "value", "error", "waiters")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: str | None = None
        self.error: BaseException | None = None
        self.waiters = 0


class SingleFlight:
    """같은 키의 동시 실행을 하나로 합친다.

    leader 가 `fn` 을 돌리고, 그 사이 같은 키로 들어온 요청은 결과를 기다린다.
    leader 가 예외를 내면 대기자도 **같은 예외 객체**를 받는다 — 성공한 척하지 않는다.
    """

    def __init__(self, *, wait_timeout_seconds: float = 130.0) -> None:
        self.wait_timeout = float(wait_timeout_seconds)
        self._lock = threading.Lock()
        self._flights: dict[str, _Flight] = {}

    def run(self, key: str, fn: Callable[[], str]) -> tuple[str, bool]:
        """`(value, was_leader)` 를 돌려준다."""
        with self._lock:
            flight = self._flights.get(key)
            if flight is not None:
                flight.waiters += 1
                is_leader = False
            else:
                flight = _Flight()
                self._flights[key] = flight
                is_leader = True

        if not is_leader:
            if not flight.done.wait(self.wait_timeout):
                raise SingleFlightTimeout("동시 요청 대기 시간이 초과되었습니다.")
            if flight.error is not None:
                raise flight.error
            assert flight.value is not None
            return flight.value, False

        try:
            value = fn()
            flight.value = value
            return value, True
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            #: ★삭제를 먼저, 이벤트를 나중에. 이벤트 뒤에 들어오는 요청은 새 flight 를 연다.
            with self._lock:
                self._flights.pop(key, None)
            flight.done.set()

    def waiters(self, key: str) -> int:
        """테스트·관측용. 지금 이 키를 기다리는 요청 수."""
        with self._lock:
            f = self._flights.get(key)
            return f.waiters if f else 0

    def in_flight(self) -> int:
        with self._lock:
            return len(self._flights)


# ---------------------------------------------------------------- 캐시


class TtlCache:
    """짧은 TTL 응답 캐시. `ttl_seconds <= 0` 이면 꺼진 것이다.

    키에 릴리스 지문·인용문 해시가 들어가므로(`build_key`) 색인이 바뀌면 자연히 miss 다.
    실패는 절대 넣지 않는다 — 넣는 쪽이 성공 값만 `set` 한다.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float,
        max_entries: int = 2048,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl = float(ttl_seconds)
        self.max_entries = int(max_entries)
        self._clock = clock
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, str]] = {}

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def get(self, key: str) -> str | None:
        if not self.enabled:
            return None
        now = self._clock()
        with self._lock:
            hit = self._store.get(key)
            if hit is None:
                return None
            expires, value = hit
            if expires <= now:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: str) -> None:
        if not self.enabled:
            return
        now = self._clock()
        with self._lock:
            if len(self._store) >= self.max_entries:
                #: 만료분부터 비우고, 그래도 넘치면 가장 먼저 만료될 것부터 버린다.
                for k in [k for k, (exp, _) in self._store.items() if exp <= now]:
                    del self._store[k]
                while len(self._store) >= self.max_entries:
                    oldest = min(self._store.items(), key=lambda kv: kv[1][0])[0]
                    del self._store[oldest]
            self._store[key] = (now + self.ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


# ---------------------------------------------------------------- 조립


@dataclass(frozen=True)
class GuardOutcome:
    value: str
    #: `call` | `single_flight` | `cache`. 응답의 `llm.source` 로 나간다.
    #: 성공 응답이 어떤 논리 경로를 탔는지 설명하는 값이며, 실패·SDK 재시도까지 포함한
    #: 실제 네트워크 호출·토큰·과금의 진실 원장은 provider gateway 관측값이다.
    source: str


class ChatLlmGuard:
    """rate limit · single-flight · 캐시 · 전역 호출 예산을 한 객체로 묶는다.

    라우터는 이 객체 하나만 들고 두 가지를 부른다.

        guard.check_client(client_key)         → 요청 진입 시(공개 채널만)
        guard.explain(key=..., produce=...)    → LLM 을 부르기로 한 뒤
    """

    def __init__(
        self,
        *,
        client_limit_per_minute: int,
        llm_calls_per_minute: int,
        cache_ttl_seconds: float,
        wait_timeout_seconds: float = 130.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client_limiter = SlidingWindowLimiter(
            limit=client_limit_per_minute, window_seconds=60.0, clock=clock
        )
        self.llm_budget = SlidingWindowLimiter(
            limit=llm_calls_per_minute, window_seconds=60.0, clock=clock
        )
        self.cache = TtlCache(ttl_seconds=cache_ttl_seconds, clock=clock)
        self.single_flight = SingleFlight(wait_timeout_seconds=wait_timeout_seconds)
        self._lock = threading.Lock()
        #: 관측용 카운터. provider 를 실제로 부른 횟수만 센다.
        self.provider_calls = 0

    def check_client(self, client_key: str) -> LimitDecision:
        """공개 채널 요청 상한. 초과면 `RateLimited`."""
        d = self.client_limiter.consume(client_key)
        if not d.allowed:
            raise RateLimited(d.retry_after_seconds)
        return d

    def explain(self, *, key: str, produce: Callable[[], str]) -> GuardOutcome:
        """`produce` 는 실제 LLM 호출이다. 캐시 → single-flight → 예산 순으로 거른다."""
        cached = self.cache.get(key)
        if cached is not None:
            return GuardOutcome(cached, SOURCE_CACHE)

        def _leader() -> str:
            #: 예산은 **실제로 부르는 쪽**만 소비한다. 대기자·캐시 적중은 과금이 없다.
            budget = self.llm_budget.consume("llm")
            if not budget.allowed:
                raise LlmBudgetExceeded(budget.retry_after_seconds)
            with self._lock:
                self.provider_calls += 1
            value = produce()
            self.cache.set(key, value)
            return value

        value, was_leader = self.single_flight.run(key, _leader)
        return GuardOutcome(value, SOURCE_CALL if was_leader else SOURCE_SINGLE_FLIGHT)

    def reset(self) -> None:
        """테스트용. 카운터·캐시·윈도를 비운다."""
        self.client_limiter.reset()
        self.llm_budget.reset()
        self.cache.clear()
        with self._lock:
            self.provider_calls = 0


__all__ = [
    "PROMPT_VERSION",
    "SOURCE_CACHE",
    "SOURCE_CALL",
    "SOURCE_SINGLE_FLIGHT",
    "ChatGuardError",
    "ChatLlmGuard",
    "GuardOutcome",
    "LimitDecision",
    "LlmBudgetExceeded",
    "RateLimited",
    "SingleFlight",
    "SingleFlightTimeout",
    "SlidingWindowLimiter",
    "TtlCache",
    "build_key",
    "fingerprint_of",
]
