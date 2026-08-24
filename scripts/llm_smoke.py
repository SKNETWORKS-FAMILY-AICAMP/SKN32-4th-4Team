"""선택된 LLM provider에 실제 최소 완성 요청을 보내는 수동 스모크.

API 키 값은 출력하지 않는다. 외부 provider를 선택하면 소량의 토큰 비용이 발생할 수 있다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from app.adapters.llm_gateway import LlmGateway
from app.adapters.llm_probe import probe_llm
from app.core.config import get_settings
from app.core.llm_clients import get_active_model


def main() -> None:
    settings = get_settings()
    probe = probe_llm(settings)
    print(
        f"provider={settings.LLM_PROVIDER} model={get_active_model(settings)} "
        f"configured={probe['configured']} ready={probe['ready']}"
    )
    if not probe["ready"]:
        raise SystemExit(f"LLM 연결 실패: {probe['error']}")
    answer = LlmGateway().complete(
        "연결 확인입니다. 다른 설명 없이 정확히 OK만 출력하세요.",
        max_tokens=8,
        temperature=0.0,
    )
    print(f"response={answer.strip()}")


if __name__ == "__main__":
    main()
