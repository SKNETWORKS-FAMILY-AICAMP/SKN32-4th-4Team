"""PII 마스킹 (Phase 9) — 지식보강 큐에 질문을 남기기 전 개인정보를 가린다.

큐의 목적은 "문서에 없어 답 못한 질문"을 모으는 것이지 개인정보 수집이 아니다. 목적에
불필요한 원문은 저장하지 않는다는 원칙에 따라, 저장 **전에** 흔한 식별자 패턴을 치환한다.

정직한 한계(과장 금지): 정규식 기반이라 **완전한 비식별을 보장하지 않는다**.
- 이름·주소·계좌번호·여권번호·IP 등은 잡지 못한다.
- 카드 판정에 Luhn 검증을 하지 않아 일반 긴 숫자열도 마스킹될 수 있다(과잉 마스킹은
  안전한 방향의 오차라 의도적으로 허용).
그래서 마스킹 하나에 기대지 않고 **관리자 전용 접근 + 보존기간 파기(`purge-gaps`)**를 함께 둔다.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]*\w")
# 카드형: 13~16자리(구분자 허용). 더 긴 숫자열의 **일부만** 치환되지 않도록 앞뒤를 모두 막는다:
# 뒤 lookahead만 두면 엔진이 시작 위치를 뒤로 옮겨 중간부터 매칭한다("1234 [CARD]") →
# 앞에 숫자(또는 숫자+구분자)가 오는 시작 위치도 거부해야 한다(Codex 지적 + 테스트로 확인).
_CARD = re.compile(r"(?<!\d)(?<!\d[ -])\d(?:[ -]?\d){12,15}(?![ -]?\d)")
# 전화번호: 국내(010-1234-5678, 02-123-4567) + 국제표기(+82 10 1234 5678)
_PHONE = re.compile(r"(?:\+82[ -]?\d{1,2}|\b0\d{1,2})[ -]?\d{3,4}[ -]?\d{4}\b")
# 주문번호: 'O' + 16진수 11자(생성이 uuid4().hex 기반이라 hex만 나온다). 대소문자 무관.
_ORDER_NO = re.compile(r"\bO[0-9A-Fa-f]{11}\b", re.IGNORECASE)
# 주민등록번호형
_RRN = re.compile(r"\b\d{6}[ -]?[1-4]\d{6}\b")

_RULES = [
    (_EMAIL, "[EMAIL]"),
    (_RRN, "[RRN]"),
    (_CARD, "[CARD]"),
    (_PHONE, "[PHONE]"),
    (_ORDER_NO, "[ORDER_NO]"),
]


def mask_pii(text: str) -> str:
    """흔한 식별자 패턴을 자리표시자로 치환한다. 원문을 그대로 저장하지 않기 위함."""
    if not text:
        return text
    masked = text
    for pattern, placeholder in _RULES:
        masked = pattern.sub(placeholder, masked)
    return masked
