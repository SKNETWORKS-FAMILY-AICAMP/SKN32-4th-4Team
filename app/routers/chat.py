"""용어 챗봇 API.

★응답에 `verdict` 가 **없다.** 보장 여부는 `POST /v1/prechecks` 로만 답한다.
  보장 질문이 오면 `next_action="precheck_form"` 으로 **넘긴다.**

★HTTP 상태 규칙은 다른 API 와 같다
    200  답했다. "약관에서 못 찾았다"도 200 이다 — 정상 결과다
    422  입력이 잘못됐다
    503  색인이 없다. 이때만 "우리 잘못"이다
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.errors import InfraError, ValidationErr, public_error_message
from app.core.llm_clients import get_active_model
from app.core.usecases import chat

router = APIRouter(prefix="/v1", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(description="사용자가 보낸 한 마디", max_length=500)
    insurer: str | None = Field(default=None, description="보험사로 좁힌다(선택)")


def _source():
    from app.composition import build_glossary

    return build_glossary()


def _model():
    from app.adapters.llm_gateway import LlmGateway

    return LlmGateway()


@router.post("/chat")
def chat_turn(body: ChatRequest) -> dict:
    return _chat_turn(body, record_knowledge_gap=True)


def chat_turn_for_registered_agent(body: ChatRequest) -> dict:
    """보호 기계 채널용. 사용자 질문 원문을 knowledge-gap 로그에 복제하지 않는다."""

    return _chat_turn(body, record_knowledge_gap=False)


def _chat_turn(body: ChatRequest, *, record_knowledge_gap: bool) -> dict:
    """용어 질문에 **약관 원문 인용으로** 답한다.

    ★보장 질문에는 답하지 않는다. 판정 양식으로 안내한다.
    """
    try:
        turn = chat.reply(body.message, source=_source(), insurer=body.insurer)
    except ValidationErr as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except InfraError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=public_error_message(e),
        ) from e

    ex = turn.explanation
    message = turn.message
    llm_used = False

    #: ★★**지식갭 큐를 보험 경로에 연결한다(2026-08-04).**
    #:
    #:   그동안 이 큐에 쓰는 곳은 커머스 RAG(`/api/rag/qa`) 하나뿐이었다.
    #:   그 경로는 고객 포트에 실리지도 않고 어떤 화면도 부르지 않는다 —
    #:   그래서 관리자 대시보드의 "지식갭" 패널이 **영원히 0건**이었다.
    #:   계획서 §2-1 은 "근거 인용 + abstention → 지식갭 큐"를 재사용 자산으로 꼽았는데
    #:   **정작 보험 쪽 abstention 이 큐에 닿지 않고 있었다.**
    #:
    #:   용어를 못 찾은 것은 **용어집 보강 대상**이다. 그것이 이 큐의 본래 용도다.
    if record_knowledge_gap and turn.term and not (ex and ex.found):
        from app.obs.knowledge_gaps import record_gap_safe

        record_gap_safe(f"[용어] {turn.term}")
    if ex and ex.found:
        settings = get_settings()
        if settings.LLM_CHAT_ENABLED:
            from app.application.grounded_term_answer import explain_term

            message = explain_term(
                term=turn.term,
                quotes=[q.quote for q in ex.quotes],
                model=_model(),
            )
            llm_used = True
    return {
        "schema_version": "v1",
        "intent": turn.intent,
        "message": message,
        "next_action": turn.next_action,
        "term": turn.term,
        "found": bool(ex and ex.found),
        #: 인용은 가공하지 않은 원문이다.
        "quotes": [
            {
                "quote": q.quote,
                "kind": q.kind,
                "insurer": q.insurer,
                "title": q.title,
                "locator": q.locator,
            }
            for q in (ex.quotes if ex else ())
        ],
        "total_passages": ex.total_passages if ex else 0,
        "insurers": list(ex.insurers) if ex else [],
        "warnings": list(turn.warnings),
        "llm": {
            "used": llm_used,
            "provider": get_settings().LLM_PROVIDER if llm_used else None,
            "model": get_active_model() if llm_used else None,
        },
    }


@router.get("/chat/terms")
def chat_terms(
    q: str = Query(default="", description="용어 검색(부분 일치)"),
    limit: int = Query(default=40, ge=1, le=200),
) -> dict:
    """**약관에 정의가 있는 용어** 목록 — 챗봇 입력 도우미.

    ★★**용어 사전이 아니다.** 뜻은 여기 담지 않는다 —
      뜻은 `POST /v1/chat` 이 **약관 원문 인용으로** 답한다.
      여기 담는 것은 「이 낱말은 약관에 정의가 있다」는 사실뿐이다.

    ★목록에 **없는 낱말도 물어볼 수 있다.** 못 찾으면 못 찾았다고 답한다.
      그 사실을 응답이 항상 말한다 — 목록을 「물어볼 수 있는 전부」로 읽으면
      사용자가 질문 자체를 포기한다.

    ★목록은 **실제 검색으로 검증된 것만** 담는다(`scripts/eval/glossary_terms.py`).
      검증을 안 하면 「목록에 있는데 물어보면 못 찾는」 용어가 생긴다 —
      입력 도우미로서 그게 최악이다.
    """
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "data" / "exports" / "glossary_terms.json"
    if not path.exists():
        #: ★조용히 빈 목록을 주지 않는다. 「약관에 정의된 용어가 없다」로 읽힌다.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="용어 목록이 아직 준비되지 않았습니다. 용어는 직접 물어보실 수 있습니다.",
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("items") or []
    total = len(items)
    if q.strip():
        needle = q.strip()
        items = [x for x in items if needle in x["term"]]
    return {
        "built_at": data.get("built_at", ""),
        "scanned_policies": data.get("scanned_policies", 0),
        #: ★거른 뒤에도 분모를 준다.
        "total_terms": total,
        "matched": len(items),
        "shown": min(len(items), limit),
        "items": items[:limit],
        "notes": [
            "약관에 정의가 실려 있는 낱말입니다. 뜻은 물어보시면 원문으로 알려 드립니다.",
            "★목록에 없는 낱말도 물어보실 수 있습니다.",
            "보장 여부는 이 대화창에서 답하지 않습니다 — 판정 양식으로 안내합니다.",
        ],
    }
