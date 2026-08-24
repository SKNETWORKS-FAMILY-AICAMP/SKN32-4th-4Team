"""검색된 약관 구절만 사용해 보험 용어를 쉬운 말로 설명한다.

보장 여부 판정은 이 유스케이스의 범위가 아니다. 입력 근거가 없으면 모델을 호출하지 않고,
출력이 보장·지급을 단정하면 차단한다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.application.ports import ModelGateway
from app.core.errors import LLMOutputError, ValidationErr

_VERDICT_CLAIM = re.compile(
    r"(?:보장|보상)(?:됩니다|됩니다\.|되지\s*않습니다|불가합니다)|"
    r"보험금(?:이|은|을)?\s*(?:지급|수령)(?:됩니다|되지\s*않습니다|할\s*수\s*있습니다)"
)


def explain_term(*, term: str, quotes: Sequence[str], model: ModelGateway) -> str:
    """원문 구절에 한정한 설명을 생성하고 위험한 판정 단정을 차단한다."""
    clean = [q.strip() for q in quotes if q and q.strip()]
    if not clean:
        raise ValidationErr("설명에 사용할 약관 원문이 없습니다.")

    evidence = "\n\n".join(f"[근거 {i}] {q}" for i, q in enumerate(clean[:5], 1))
    prompt = f"""당신은 보험 약관 용어 설명 도우미다.

용어: {term}

아래 근거에 적힌 내용만 사용해 한국어로 2~3문장으로 쉽게 설명하라.
- 보장 여부, 보험금 지급 가능성, 예상 금액은 절대 판단하지 마라.
- 근거에 없는 조건이나 일반 상식을 추가하지 마라.
- 불명확하면 불명확하다고 말하라.
- 인용 번호나 마크다운 제목을 만들지 마라. 원문 인용은 화면이 별도로 표시한다.

{evidence}
"""
    answer = model.complete(prompt, max_tokens=256, temperature=0.0).strip()
    if not answer:
        raise LLMOutputError("용어 설명 모델이 빈 응답을 반환했습니다.")
    if len(answer) > 2000:
        raise LLMOutputError("용어 설명 모델 출력이 허용 길이를 초과했습니다.")
    if _VERDICT_CLAIM.search(answer):
        raise LLMOutputError("용어 설명이 보장·지급 여부를 단정해 차단했습니다.")
    return answer
