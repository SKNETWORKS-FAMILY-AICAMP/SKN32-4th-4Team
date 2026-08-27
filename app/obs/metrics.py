"""메트릭 노출 — Prometheus 텍스트 형식.

★**긁는 요청은 싸야 한다.** 스크레이퍼는 15초마다 두드린다.
  그래서 여기서는 **프로세스 안에 이미 있는 수치만** 낸다 —
  DB 를 세거나 모델을 만지지 않는다. 조항 발생 36만 행을 매 스크레이프마다
  세면 그것 자체가 부하가 된다.

  DB 수치가 필요하면 별도 배치가 주기적으로 재서 게이지에 넣어야 한다. 아직 안 했다.

★**`prometheus_client` 에 기대지 않는다.** 설치돼 있긴 하지만
  `requirements/*` 어디에도 없는 **전이 의존성**이다. 남의 패키지가 딸려 온 것을
  런타임 계약으로 삼으면, 그 패키지가 빠지는 날 관측이 통째로 죽는다.
  노출 형식은 단순해서 직접 쓰는 편이 낫다.

형식 (https://prometheus.io/docs/instrumenting/exposition_formats/)

    # HELP <이름> <설명>
    # TYPE <이름> <counter|gauge>
    <이름>{<라벨>="<값>"} <숫자>
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from threading import Lock
from typing import Literal

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

LlmPurpose = Literal["term_explanation", "ai2_explanation"]
LlmOutcome = Literal[
    "success", "rate_limited", "timeout", "connection_error", "api_error", "empty_response"
]
_LLM_PURPOSES = frozenset(("term_explanation", "ai2_explanation"))
_LLM_OUTCOMES = frozenset(
    ("success", "rate_limited", "timeout", "connection_error", "api_error", "empty_response")
)
_llm_lock = Lock()
_llm_calls: dict[tuple[str, str, str, str], int] = defaultdict(int)
_llm_latency_seconds: dict[tuple[str, str, str, str], float] = defaultdict(float)
_llm_tokens: dict[tuple[str, str, str, str, str], int] = defaultdict(int)


def observe_llm_call(
    *, provider: str, model: str, purpose: LlmPurpose, outcome: LlmOutcome,
    latency_seconds: float, usage: dict[str, int] | None = None,
) -> None:
    """Record one LLM attempt without retaining prompt, response, key, or exception data."""
    if purpose not in _LLM_PURPOSES:
        raise ValueError("unsupported LLM purpose")
    if outcome not in _LLM_OUTCOMES:
        raise ValueError("unsupported LLM outcome")
    labels = (str(provider), str(model), purpose, outcome)
    with _llm_lock:
        _llm_calls[labels] += 1
        _llm_latency_seconds[labels] += max(0.0, float(latency_seconds))
        if usage is not None:
            for token_type in ("input", "output", "total"):
                _llm_tokens[labels + (token_type,)] += max(0, int(usage[token_type]))


def _reset_llm_metrics_for_tests() -> None:
    with _llm_lock:
        _llm_calls.clear()
        _llm_latency_seconds.clear()
        _llm_tokens.clear()


def _escape_label(v: str) -> str:
    """라벨 값 이스케이프. 순서가 중요하다 — 역슬래시를 먼저 바꾼다."""
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _escape_help(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace("\n", "\\n")


def _num(v) -> str:
    """숫자로 만든다. 참/거짓은 1/0, 없는 값은 낸다고 하지 않는다."""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        rendered = repr(v)
        return format(Decimal(rendered), "f") if "e-" in rendered.lower() else rendered
    return str(int(v))


class Metrics:
    """한 번의 스크레이프 동안 줄을 모은다."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._declared: set[str] = set()

    def add(self, name: str, value, *, kind: str, help_: str,
            labels: dict | None = None) -> None:
        """값이 `None` 이면 **줄을 내지 않는다.**

        ★없는 것을 0 으로 내면 「0 건」과 「모른다」가 섞인다.
          적재 시간을 모르는데 0 으로 내면 그래프가 「즉시 적재」로 보인다.
        """
        if value is None:
            return
        if name not in self._declared:
            self._lines.append(f"# HELP {name} {_escape_help(help_)}")
            self._lines.append(f"# TYPE {name} {kind}")
            self._declared.add(name)
        if labels:
            inner = ",".join(f'{k}="{_escape_label(v)}"' for k, v in sorted(labels.items()))
            self._lines.append(f"{name}{{{inner}}} {_num(value)}")
        else:
            self._lines.append(f"{name} {_num(value)}")

    def render(self) -> str:
        return "\n".join(self._lines) + "\n"


def render_metrics() -> str:
    """지금 프로세스의 메트릭을 텍스트로 낸다."""
    m = Metrics()
    _llm(m)
    _clause_rerank(m)
    return m.render()


def _llm(m: Metrics) -> None:
    with _llm_lock:
        calls = dict(_llm_calls)
        latencies = dict(_llm_latency_seconds)
        tokens = dict(_llm_tokens)
    for key, value in calls.items():
        provider, model, purpose, outcome = key
        labels = {"provider": provider, "model": model, "purpose": purpose, "outcome": outcome}
        m.add("llm_calls_total", value, kind="counter", help_="LLM 호출 수", labels=labels)
        m.add("llm_call_latency_seconds_sum", latencies[key], kind="counter",
              help_="LLM 호출 지연 누적(초)", labels=labels)
        m.add("llm_call_latency_seconds_count", value, kind="counter",
              help_="LLM 호출 지연 표본 수", labels=labels)
    for key, value in tokens.items():
        provider, model, purpose, outcome, token_type = key
        m.add("llm_tokens_total", value, kind="counter", help_="LLM 토큰 수",
              labels={"provider": provider, "model": model, "purpose": purpose,
                      "outcome": outcome, "type": token_type})


def _clause_rerank(m: Metrics) -> None:
    from app.adapters import rerank_worker as rw
    from app.core.config import get_settings

    st = get_settings()
    m.add("clause_rerank_enabled", st.INSURANCE_CLAUSE_RERANK_ENABLED,
          kind="gauge", help_="조항 리랭킹 스위치(1=켜짐)")
    m.add("clause_rerank_timeout_seconds", float(st.CLAUSE_RERANK_TIMEOUT_SECONDS),
          kind="gauge", help_="리랭킹 시한(초). 넘으면 504")
    m.add("clause_rerank_max_candidates", st.CLAUSE_RERANK_MAX_CANDIDATES,
          kind="gauge", help_="리랭커에 넣는 후보 상한")

    #: ★어느 방식으로 도는가. **설정값을 라벨로** 낸다 — 워커가 아직 없어도 보여야
    #:   「무엇이 켜져 있는지」를 안다. `process` 로 바꿔 놓고 안 바뀐 줄 아는 일이 없게.
    m.add("clause_rerank_worker_mode", 1, kind="gauge",
          help_="리랭크 워커 방식(라벨로 구분). thread=취소 불가 · process=시한에 죽인다",
          labels={"mode": st.CLAUSE_RERANK_WORKER})

    #: ★**워커를 만들지 않는다.** 스크레이프가 4B 무게추를 올리면 안 된다.
    worker = rw.peek_worker()
    if worker is None:
        m.add("clause_rerank_worker_up", 0, kind="gauge",
              help_="리랭크 워커가 떠 있는가(1=떠 있음)")
        return
    s = worker.stats()
    m.add("clause_rerank_worker_up", bool(s.get("alive")), kind="gauge",
          help_="리랭크 워커가 떠 있는가(1=떠 있음)")
    m.add("clause_rerank_worker_loaded", bool(s.get("loaded")), kind="gauge",
          help_="무게추 적재·예열이 끝났는가(1=끝남)")
    m.add("clause_rerank_worker_load_seconds", s.get("load_seconds"), kind="gauge",
          help_="무게추 적재+예열에 걸린 시간(초). 모르면 내지 않는다")

    #: ★결과별로 **한 이름에 라벨**로 낸다. 이름을 쪼개면 합계를 못 낸다.
    for result, key in (("completed", "completed"), ("failed", "failed"),
                        ("timeout", "timeouts"), ("rejected_busy", "rejected_busy"),
                        ("abandoned", "abandoned_in_flight")):
        m.add("clause_rerank_jobs_total", s.get(key, 0), kind="counter",
              help_="리랭킹 일감 수(결과별 누적)", labels={"result": result})
    m.add("clause_rerank_jobs_submitted_total", s.get("submitted", 0), kind="counter",
          help_="워커에 넣은 일감 누적")

    m.add("clause_rerank_queue_depth", s.get("queue_depth", 0), kind="gauge",
          help_="워커 대기열 깊이")
    #: ★시한을 넘긴 일감이 아직 도는 상태. 이 값이 1이면 새 요청이 503 으로 거절된다.
    #:   추론을 강제로 끊을 수 없어 생기는 상태라, **밖에서 보여야** 원인을 안다.
    m.add("clause_rerank_busy_with_abandoned", bool(s.get("busy_with_abandoned")),
          kind="gauge", help_="시한 초과 일감이 아직 계산 중인가(1=그렇다, 새 요청 503)")

    #: ★프로세스 워커에서만 뜻이 있는 값 둘. **스레드일 땐 아예 안 낸다** —
    #:   0 으로 내면 「죽인 적 없다」와 「죽일 수 없는 방식이다」가 구분되지 않는다.
    if s.get("mode") == "process":
        m.add("clause_rerank_worker_starts_total", s.get("starts", 0), kind="counter",
              help_="자식 프로세스를 띄운 누적 횟수. 늘면 그만큼 무게추를 다시 올렸다")
        m.add("clause_rerank_worker_kills_total", s.get("kills", 0), kind="counter",
              help_="시한 초과·정리로 자식을 죽인 누적 횟수")

    lat = s.get("latency_ms") or {}
    for q in ("p50", "p95"):
        m.add("clause_rerank_latency_ms", lat.get(q), kind="gauge",
              help_="리랭킹 지연(밀리초). 최근 200건 기준",
              labels={"quantile": "0.5" if q == "p50" else "0.95"})
    m.add("clause_rerank_latency_samples", lat.get("n"), kind="gauge",
          help_="지연 계산에 쓴 표본 수")
