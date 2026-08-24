"""MCP ↔ REST 동등성 — 보험 도구 4종.

★parity 의 의미가 **바뀌었다**

    커머스 시절에는 MCP 서버가 서비스·도구를 각자 래핑했고, 그래서 "두 구현이
    같은 답을 내는가"를 시험해야 했다. 지금은 **MCP 가 REST 라우터 함수를 그대로 부른다** —
    파생 구현이 아예 없다. 그래서 이 파일이 지키는 것은 "두 구현의 일치"가 아니라
    **"파생 구현이 생기지 않았음"** 이다.

    두 시험을 둔다:
      1. 같은 입력에 REST 와 MCP 응답이 **완전히 같다**(고정 Fake 유스케이스로 결정론 확보).
      2. MCP 서버 소스가 유스케이스·어댑터를 **직접 부르지 않는다**(래핑 재발 방지).

★`app/mcp` 는 2026-08-04 에 되살아났다

    v3 커머스와 함께 레거시로 격리돼 있었고, 그동안 이 파일은 `mcp` 마커로 빠져
    **아무것도 보증하지 않았다.** 보험 도구를 다시 만들면서 마커를 뗀다 —
    stdio 서브프로세스를 쓰지 않으므로 CI 에서 그냥 돈다.
"""

from __future__ import annotations

import asyncio
import io
import json
import pathlib
import tokenize

import pytest

from app.core.domain.precheck_result import PrecheckOutcome


def _run(coro):
    return asyncio.run(coro)


def _text(result) -> str:
    """FastMCP `call_tool` 결과에서 본문 텍스트를 꺼낸다(SDK 반환형 방어)."""
    if isinstance(result, tuple):
        result = result[-1]
    if isinstance(result, list):
        return result[0].text
    return getattr(result, "text", str(result))


#: 고정 판정 결과 — 어느 인터페이스로 부르든 이걸 낸다.
_FIXED = PrecheckOutcome(
    verdict="unlikely",
    abstained=False,
    reason_code="excluded_by_clause",
    message="",
    applied_policy=None,
    per_code=(),
    citations=(),
    candidates=(),
    rule_engine_version="rules-test",
    extractor="test",
    trace_id="trace-fixed",
    warnings=(),
)


@pytest.fixture
def _fixed_graph(monkeypatch):
    """판정 흐름을 고정 Fake 로 대체 — 실모델·PG 없이 DTO 동등성만 본다."""
    from app.routers import precheck as router

    class _Graph:
        def invoke(self, _body):
            return _FIXED, {}

    monkeypatch.setattr(router, "_graph", lambda: _Graph())


def test_precheck_는_rest_와_mcp_가_완전히_같은_응답을_낸다(client, _fixed_graph):
    from app.mcp.server import mcp

    payload = {"insurer": "NH농협생명", "enrolled_on": "20160301", "kcd_codes": ["N39.4"]}

    rest = client.post("/v1/prechecks", json={**payload, "client_ref": "mcp"}).json()
    mcp_out = json.loads(_text(_run(mcp.call_tool("precheck", payload))))

    assert rest == mcp_out, f"REST/MCP 응답이 다릅니다:\nREST={rest}\nMCP ={mcp_out}"
    #: 계약 고정 — 기권 여부를 에이전트가 필드로 읽을 수 있어야 한다.
    assert mcp_out["verdict"] == "unlikely"
    assert mcp_out["abstained"] is False
    assert mcp_out["trace_id"] == "trace-fixed"


def test_cohort_도_rest_와_같은_응답을_낸다(client):
    from app.mcp.server import mcp

    rest = client.get("/v1/cohorts", params={"code": "S72.0"}).json()
    mcp_out = json.loads(_text(_run(mcp.call_tool(
        "cohort_stats", {"code": "S72.0", "data_source": "verified_real"}))))

    assert rest == mcp_out
    #: ★출처와 등급 내역은 **절대 빠지면 안 되는 필드**다.
    assert mcp_out["data_source"] == "verified_real"
    assert "by_verification" in mcp_out


def test_용어설명도_rest와_mcp가_같고_llm상태를_보존한다(client, monkeypatch):
    from app.core.ports.glossary import TermPassage
    from app.mcp.server import mcp
    from app.routers import chat as chat_router

    class _Glossary:
        def find(self, term, *, insurer=None, limit=20):
            row = TermPassage(
                kind="clause",
                sha256="a" * 64,
                insurer="가보험",
                qualified_no="보통약관/2.",
                section="보통약관",
                title="용어의 정의",
                page_from=3,
                page_to=3,
                content_hash="deadbeefcafe",
                text="통원 의료기관에 입원하지 않고 방문하여 치료받는 것",
            )
            return [row] if term in row.text else []

        def meta(self):
            return {"built_from": "test"}

    monkeypatch.setattr(chat_router, "_source", lambda: _Glossary())
    # 공통 테스트 환경은 LLM_CHAT_ENABLED=false라 결정론적 원문 응답을 비교한다.
    payload = {"message": "통원 뜻"}
    rest = client.post("/v1/chat", json=payload).json()
    mcp_out = json.loads(_text(_run(mcp.call_tool("explain_term", payload))))

    assert rest == mcp_out
    assert mcp_out["found"] is True
    assert mcp_out["llm"]["used"] is False


def test_잘못된_data_source_는_예외가_아니라_구조화된_오류다():
    """★에이전트는 분기해야 한다. 예외만 던지면 무엇을 고칠지 알 수 없다."""
    from app.mcp.server import mcp

    out = json.loads(_text(_run(mcp.call_tool(
        "cohort_stats", {"code": "S72.0", "data_source": "무엇이든"}))))

    assert out["ok"] is False
    assert out["http_status"] == 422
    #: 입력 잘못은 재시도해도 소용없다 — 그걸 알려 준다.
    assert out["retryable"] is False


def test_잘못된_observation_outcome도_구조화된_422다():
    from app.mcp.server import mcp

    out = json.loads(_text(_run(mcp.call_tool(
        "submit_observation",
        {
            "client_ref": "mcp-test",
            "insurer": "가보험",
            "outcome": "anything",
            "kcd_codes": ["F32"],
        },
    ))))
    assert out["ok"] is False
    assert out["http_status"] == 422
    assert out["retryable"] is False


def test_도구는_정확히_네_개다():
    """LLM 용어 설명도 별도 구현 없이 REST 라우터를 그대로 쓴다."""
    from app.mcp.server import mcp

    names = {t.name for t in _run(mcp.list_tools())}
    assert names == {"precheck", "explain_term", "cohort_stats", "submit_observation"}


def test_mcp_서버는_유스케이스를_직접_부르지_않는다():
    """★★**파생 구현이 생기지 않았는지** 구조로 확인한다.

    커머스 시절 MCP 서버는 서비스를 각자 래핑했고 그래서 두 경로가 어긋났다.
    라우터만 부르면 어긋날 자리가 없다.
    """
    from app.mcp import server

    src = pathlib.Path(server.__file__).read_text(encoding="utf-8")
    code = "".join(
        t.string
        for t in tokenize.generate_tokens(io.StringIO(src).readline)
        if t.type not in (tokenize.COMMENT, tokenize.STRING)
    )
    for banned in ("app.core.usecases", "app.adapters", "app.workflow"):
        assert banned not in code, (
            f"MCP 서버가 {banned} 를 직접 부릅니다 — 라우터를 거쳐야 파생 구현이 안 생깁니다."
        )


def test_런타임_설정_리소스에_시크릿이_없다():
    """TEST-MCP-NOSECRET-001."""
    from app.mcp.server import runtime_config

    payload = runtime_config()
    keys = {k.lower() for k in payload}
    for banned in ("api_key", "openai_api_key", "google_api_key", "secret_key", "token"):
        assert banned not in keys

    blob = json.dumps(payload, ensure_ascii=False).lower()
    assert "sk-" not in blob and "secret" not in blob


def test_llms_txt_가_에이전트_주의사항을_담는다(client):
    """★OpenAPI 는 "무엇을 조심해야 하나"를 말하지 못한다. 그건 문장이어야 한다."""
    resp = client.get("/llms.txt")
    assert resp.status_code == 200
    body = resp.text

    #: 이 도메인에서 가장 위험한 오해 네 가지가 반드시 적혀 있어야 한다.
    assert "기권은 오류가 아니다" in body
    assert "면책 목록에 없다" in body
    assert "data_source" in body
    assert "admin_attested" in body
