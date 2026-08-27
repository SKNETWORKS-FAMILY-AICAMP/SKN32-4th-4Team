from __future__ import annotations

import pytest

from app.application.grounded_term_answer import explain_term
from app.core.errors import LLMOutputError, ValidationErr


class _Model:
    def __init__(self, answer: str):
        self.answer = answer
        self.prompt = ""

    def complete(self, prompt: str, **kwargs) -> str:
        self.prompt = prompt
        return self.answer


def test_원문만_프롬프트에_넣어_쉬운설명을_만든다():
    model = _Model("통원은 입원하지 않고 의료기관을 방문해 치료받는 것입니다.")
    answer = explain_term(
        term="통원",
        quotes=["통원 의료기관에 입원하지 않고 방문하여 치료받는 것"],
        model=model,
    )
    assert "입원하지 않고" in answer
    assert "보장 여부" in model.prompt and "절대 판단하지 마라" in model.prompt


def test_근거가_없으면_모델을_호출하지_않는다():
    model = _Model("호출되면 안 됨")
    with pytest.raises(ValidationErr):
        explain_term(term="통원", quotes=[], model=model)
    assert model.prompt == ""


@pytest.mark.parametrize(
    "answer",
    [
        "따라서 보험금이 지급됩니다.",
        "이 치료는 보장됩니다.",
        "이 경우 보상되지 않습니다.",
        "",
    ],
)
def test_판정단정이나_빈출력을_차단한다(answer):
    with pytest.raises(LLMOutputError):
        explain_term(term="통원", quotes=["통원은 방문 치료"], model=_Model(answer))
