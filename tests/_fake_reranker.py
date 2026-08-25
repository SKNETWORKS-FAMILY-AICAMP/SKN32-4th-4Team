"""시험용 가짜 리랭커 — **별도 프로세스 워커 시험 전용.**

★spawn 자식은 부모의 monkeypatch 를 못 물려받는다. 그래서 자식이 import 할 수 있는
  **진짜 모듈**로 두고, `spec["reranker_class"]` 로 가리킨다.
  동작은 환경변수로 바꾼다 — 환경변수는 자식에 전달된다.
"""

from __future__ import annotations

import os
import time


class FakeReranker:
    def __init__(self, model_name, **kw):
        if os.environ.get("FAKE_RERANK_LOAD_FAIL") == "1":
            raise RuntimeError("no CUDA")
        if os.environ.get("FAKE_RERANK_LOAD_CRASH") == "1":
            #: ★적재 중 **네이티브 크래시**를 흉내 낸다(세그폴트·OOM 킬).
            #:   예외가 아니라 프로세스가 그냥 사라지는 경우다 — 큐에 아무것도 안 남는다.
            os._exit(139)
        self._delay = float(os.environ.get("FAKE_RERANK_DELAY", "0"))

    def rerank(self, query, evidence, top_n=None):
        if self._delay:
            time.sleep(self._delay)
        out = list(reversed(evidence))
        return out[:top_n] if top_n else out
