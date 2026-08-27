"""실모델 적재 가드가 **실제로 무는지** 확인한다.

★가드는 있는데 안 무는 것이 가장 나쁘다 — 「막고 있다」고 믿으면서 안 막는 것이라
  가드가 없는 것보다 위험하다. 그래서 가드 자체를 시험한다.

배경: `tests/test_clause_search_route.py` 의 시험 하나가 `_fake_worker()` 를 빠뜨려
`Qwen3-Reranker-4B`(7.5GB)를 실제로 적재했고, **그 시험은 통과한 채** 데몬 스레드의
적재가 한참 뒤 다른 시험 도중 pytest 프로세스를 죽였다(2026-08-26 · segfault 재현 3/3).
→ `docs/reports/debugs/2026-08-26_기본CI_segfault_원인규명_한번_틀렸다.md`
"""

from __future__ import annotations

import sys

import pytest

#: ★`tests/conftest.py` 를 직접 import 하면 **두 번째 사본**이 생겨
#:   가드 상태(`_REAL_MODEL_ALLOWED`)가 다른 객체를 보게 된다.
#:   pytest 가 이미 올려 둔 모듈을 집는다 — rootdir 설정에 따라 이름이 갈리므로 둘 다 본다.
_conftest = sys.modules.get("tests.conftest") or sys.modules["conftest"]


def test_가드가_켜져_있다():
    """`sentence_transformers` 가 있으면 생성자에 가드가 붙어 있어야 한다."""
    st = pytest.importorskip("sentence_transformers")
    for name in ("SentenceTransformer", "CrossEncoder"):
        cls = getattr(st, name, None)
        if cls is None:
            continue
        assert getattr(cls.__init__, "_real_model_guarded", False), (
            f"{name}.__init__ 에 가드가 안 붙었다 — 시험이 무게추를 내려받을 수 있다"
        )


def test_표시_없는_시험에서_실모델을_만들면_즉시_죽는다():
    """★조용히 통과한 뒤 나중에 프로세스를 죽이는 것보다 여기서 죽는 편이 낫다."""
    with pytest.raises(_conftest.RealModelLoadInTest) as e:
        _conftest._guard_real_model("CrossEncoder", "Qwen/Qwen3-Reranker-4B")
    msg = str(e.value)
    #: 메시지가 **무엇을 하라고** 말해야 한다. 원인만 적으면 다음 사람이 또 막힌다.
    assert "_fake_worker()" in msg
    assert "pytest.mark.ml" in msg
    #: ★일부러 부른 것이므로 위반 기록을 지운다.
    #:   안 지우면 정리 픽스처가 **이 시험을 실패시킨다** — 그게 정상 동작이다.
    #:   (실제로 그렇게 됐다. 가드가 무는 것을 이 자리에서 확인한 셈이다.)
    assert _conftest._REAL_MODEL_VIOLATIONS, "위반이 기록돼야 정리에서 잡을 수 있다"
    _conftest._REAL_MODEL_VIOLATIONS.clear()


@pytest.mark.ml
def test_ml_표시가_있으면_허용된다():
    """실모델이 정말 필요한 시험은 표시를 달고 기본 CI 에서 빠진다."""
    #: 이 시험이 도는 동안에는 허용 상태여야 한다(autouse 픽스처가 켠다).
    assert _conftest._REAL_MODEL_ALLOWED is True
    _conftest._guard_real_model("CrossEncoder", "무엇이든")   # 안 죽어야 한다
    assert not _conftest._REAL_MODEL_VIOLATIONS, "허용 상태에서는 기록조차 남지 않아야 한다"


def test_기본_상태는_차단이다():
    """표시가 없으면 막는 것이 기본값이다 — 안전한 쪽으로 기울인다."""
    assert _conftest._REAL_MODEL_ALLOWED is False
