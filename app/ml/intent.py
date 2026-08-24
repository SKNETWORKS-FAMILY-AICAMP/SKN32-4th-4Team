"""의도 분류 (규칙기반, 결정론).

레거시(coffee/survey)의 시작시 학습 PyTorch MLP 대신 재현성·YAGNI를 위해 키워드
규칙기반으로 대체한다. 학습형은 별도 계획.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ValidationErr

# 의도 우선순위(동률 시 앞선 의도 우선) + 키워드
INTENT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("주문", ["주문", "구매", "살게", "살래", "결제할", "장바구니"]),
    ("추천", ["추천", "골라", "어떤 게 좋", "뭐가 좋", "골라줘"]),
    ("조회", ["재고", "가격", "얼마", "주문상태", "배송", "언제", "확인"]),
    ("불만", ["환불", "불만", "화가", "짜증", "최악", "실망", "고장"]),
    ("칭찬", ["감사", "좋아요", "만족", "최고", "친절"]),
    ("인사", ["안녕", "하이", "반가", "hello"]),
]


def classify_intent(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        raise ValidationErr("의도를 분류할 텍스트가 비어 있습니다.")

    scores: list[tuple[str, int, list[str], int]] = []
    for priority, (intent, keywords) in enumerate(INTENT_KEYWORDS):
        matched = [k for k in keywords if k in text]
        if matched:
            scores.append((intent, len(matched), matched, priority))

    if not scores:
        return {"intent": "기타", "confidence": 0.0, "matched": []}

    # 매칭 수 최대, 동률이면 우선순위(리스트 앞쪽=작은 priority) 우선 — 명시적 tie-break
    best = max(scores, key=lambda s: (s[1], -s[3]))
    total = sum(s[1] for s in scores)
    return {
        "intent": best[0],
        "confidence": round(best[1] / total, 4) if total else 0.0,
        "matched": best[2],
    }
