"""학습·평가가 **같이 쓰는** 프롬프트 조립기.

★05D §3-1 — "학습 형식과 서빙 형식이 다르면 그 차이만큼 성능이 새어 나간다."
그래서 이 파일 하나만 고치도록 두 스크립트가 여기서 가져다 쓴다.

★★왜 지시문이 필요한가 (실측 2026-08-05)
   처음엔 `prompt` 객체를 JSON 으로 덤프해 그대로 넣었다. `output_schema` 필드에
   `"PrecheckResult@v1"` 이라고 적혀 있으니 알아서 낼 줄 알았다. **안 냈다.**
   baseline 120건의 `schema_validity` 가 **0.0** 이었고, 원문을 열어 보니
   자유문장 한국어였다.

       제공해주신 정보만으로는 'S83.5'라는 특정 질병 코드에 대한 통원 치료
       보장 여부를 명확하게 확인할 수 없습니다. ...

   지시 없이 잰 0.0 은 "모델이 스키마를 못 지킨다"가 아니라 **"물어보지 않았다"** 이다.
   그대로 baseline 으로 쓰면, 학습 뒤 숫자가 오르는 것이 파인튜닝 효과처럼 보인다.
   그건 측정이 아니라 **연출**이다.
"""

from __future__ import annotations

import json

#: 05D §3-1 의 출력 계약을 말로 옮긴 것. enum·필드는 `precheck_result.py` 와 같다.
INSTRUCTION = """너는 실손보험 보장 사전판정의 **설명만** 생성한다.

규칙
1. 아래 JSON 객체 **하나만** 출력한다. 앞뒤에 어떤 문장도 붙이지 않는다.
2. `verdict` 는 다음 넷 중 하나다: "covered", "not_covered", "needs_documents", "needs_expert".
3. `citations` 에는 제공된 `evidence` 의 `clause_id` 만 쓴다. **없는 것을 지어내지 않는다.**
4. `evidence` 가 비어 있으면 판정하지 않는다 —
   `verdict: "needs_expert"`, `abstained: true`, `reason_code: "no_evidence"`.

출력 형식
{"verdict": ..., "abstained": ..., "reason_code": ..., "message": ...,
 "citations": [...], "abstain_reason": ... }"""


def build_user_message(prompt_obj: dict) -> str:
    """서빙·학습이 함께 쓰는 user 메시지."""
    return f"{INSTRUCTION}\n\n입력\n{json.dumps(prompt_obj, ensure_ascii=False)}"


def build_target(target_obj: dict) -> str:
    """모델이 내야 하는 문자열."""
    return json.dumps(target_obj, ensure_ascii=False)
