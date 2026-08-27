"""용어 설명 API — "이 말이 무슨 뜻인가"에만 답한다.

★응답에 `verdict` 가 **없다.**

    챗봇이 "그래서 저 보장되나요?" 로 넘어가는 순간 규칙엔진을 우회한다.
    보장 여부는 `POST /v1/prechecks` 로만 답한다.

★HTTP 상태 규칙은 판정 API 와 같다

    200  답했다. **"약관에서 못 찾았다"(`found=false`)도 200 이다** — 정상 결과다.
    422  입력이 잘못됐다(용어가 너무 짧다).
    503  색인이 없다. 이때만 "우리 잘못"이다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.core.errors import InfraError, ValidationErr, public_error_message
from app.core.usecases import glossary

router = APIRouter(prefix="/v1/terms", tags=["terms"])


def _source():
    from app.composition import build_glossary

    return build_glossary()


@router.get("/explain")
def explain_term(
    term: str = Query(description="약관 용어. 예: 도수치료"),
    insurer: str | None = Query(
        default=None,
        description="보험사로 좁힌다(선택). 비우면 수집한 약관 전체에서 찾는다.",
    ),
    max_quotes: int = Query(default=glossary.DEFAULT_MAX_QUOTES, ge=1, le=10),
) -> dict:
    """약관 원문에서 용어의 정의를 찾아 **그대로 인용한다.**

    ★못 찾으면 못 찾았다고 한다(HTTP 200 · `found=false`).
      LLM 상식으로 메우지 않는다 — 실손보험 용어는 일상어와 뜻이 다르다.
    """
    src = _source()
    try:
        ans = glossary.explain(term, source=src, insurer=insurer, max_quotes=max_quotes)
        idx = src.meta()
    except ValidationErr as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except InfraError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=public_error_message(e),
        ) from e

    return {
        "schema_version": "v1",
        "term": ans.term,
        "found": ans.found,
        "message": ans.message,
        #: ★인용은 가공하지 않은 원문이다. 붙임 정의표는 칸이 무너져 보인다.
        "quotes": [
            {
                "quote": q.quote,
                "kind": q.kind,
                "insurer": q.insurer,
                "title": q.title,
                "locator": q.locator,
                "sha256": q.sha256,
                "qualified_no": q.qualified_no,
                "page_from": q.page_from,
                "page_to": q.page_to,
            }
            for q in ans.quotes
        ],
        "total_passages": ans.total_passages,
        "insurers": list(ans.insurers),
        "warnings": list(ans.warnings),
        #: ★무엇으로 만든 색인인지 밝힌다. 재추출하면 결과가 달라진다.
        "index": {
            "built_from": idx.get("built_from"),
            "documents": idx.get("documents"),
            "clause_passages": idx.get("clause_passages"),
            "appendix_passages": idx.get("appendix_passages"),
            #: ★색인이 **닿지 못한 문서 수**도 밝힌다. 안 밝히면
            #:   "못 찾음"이 약관에 없다는 뜻인지 색인이 못 봤다는 뜻인지 모른다.
            "appendix_not_found": idx.get("appendix_not_found"),
        },
    }
