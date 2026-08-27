"""용어 챗봇 — **보장 여부를 답하지 않는가**.

★이 테스트가 지키는 것은 하나다.
  대화창은 자연스럽게 "그래서 저 보장되나요?" 로 흘러가고,
  거기서 답하면 약관버전 확정·인용검증·4단 판정을 전부 건너뛴다.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import register_exception_handlers
from app.core.ports.glossary import TermPassage
from app.core.usecases import chat


def _p(text: str, *, insurer="가보험", kind="clause") -> TermPassage:
    return TermPassage(
        kind=kind, sha256="a" * 64, insurer=insurer, qualified_no="보통약관/2.",
        section="보통약관", title="용어의 정의", page_from=3, page_to=3,
        content_hash="deadbeefcafe", text=text,
    )


class _Fake:
    def __init__(self, rows):
        self.rows = rows

    def find(self, term, *, insurer=None, limit=20):
        hit = [r for r in self.rows if term in r.text and (not insurer or r.insurer == insurer)]
        return hit[:limit] if limit else hit

    def meta(self):
        return {"built_from": "s5"}


_SRC = _Fake([
    _p("2. (용어의 정의)\n도수치료 치료자가 손을 이용해 실시하는 치료행위"),
    _p("2. (용어의 정의)\n통원 의료기관에 입원하지 않고 방문하여 치료받는 것"),
])


def _chat_app():
    from app.routers import chat as chat_router

    chat_router._reset_guard_for_tests()
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(chat_router.router)
    return app


# ---------------------------------------------------------------- 의도


@pytest.mark.parametrize(
    "text",
    [
        "도수치료 보장되나요?",
        "F32 청구 가능한가요",
        "이거 보상 받을 수 있나요",
        "얼마나 받을 수 있나요",
        "이거 면책인가요",
        "보험금 지급되나요",
    ],
)
def test_보장_질문에는_답하지_않는다(text):
    """★가장 중요한 경계. 여기서 답하면 규칙엔진을 우회한다."""
    turn = chat.reply(text, source=_SRC)
    assert turn.intent == chat.INTENT_PRECHECK
    assert turn.next_action == "precheck_form"
    #: ★인용도 판정도 싣지 않는다.
    assert turn.explanation is None
    assert not hasattr(turn, "verdict")


@pytest.mark.parametrize(
    "text,term",
    [
        ("도수치료가 뭐야", "도수치료"),
        ("통원 뜻", "통원"),
        ("도수치료", "도수치료"),
        ("약관에서 통원이 뭐야", "통원"),
        ("비급여 설명해줘", "비급여"),
    ],
)
def test_용어_질문은_용어만_남긴다(text, term):
    """★`"통원 뜻"` 에서 "뜻"이 안 떨어져 **찾을 수 있는 것을 못 찾았다**고 한 적이 있다."""
    assert chat.classify(text) == chat.INTENT_TERM
    assert chat.extract_term(text) == term


def test_설명해줘가_보장질문으로_새지_않는다():
    """★`해줘` 를 보장 신호에 넣었더니 "비급여 설명해줘" 가 판정으로 샜다."""
    assert chat.classify("비급여 설명해줘") == chat.INTENT_TERM
    assert chat.classify("도수치료 알려줘") == chat.INTENT_TERM


# ---------------------------------------------------------------- 답변


def test_찾으면_원문을_인용한다():
    turn = chat.reply("도수치료가 뭐야", source=_SRC)
    assert turn.intent == chat.INTENT_TERM
    assert turn.explanation and turn.explanation.found
    assert "치료자가 손을" in turn.explanation.quotes[0].quote


def test_못_찾으면_지어내지_않는다():
    """★실손 용어는 일상어와 뜻이 다르다. 상식으로 메우면 사람이 손해를 본다."""
    turn = chat.reply("존재할리없는낱말이 뭐야", source=_SRC)
    assert turn.explanation is not None and turn.explanation.found is False
    assert "찾지 못했습니다" in turn.message


def test_알아듣지_못하면_아무_용어나_찍지_않는다():
    turn = chat.reply("ㅁ", source=_SRC)
    assert turn.intent == chat.INTENT_UNKNOWN
    assert turn.explanation is None


def test_모든_답에_판정_아님_경고가_붙는다():
    for text in ["도수치료가 뭐야", "도수치료 보장되나요?"]:
        turn = chat.reply(text, source=_SRC)
        assert any("보장 여부는 여기서 판정하지 않습니다" in w for w in turn.warnings), text


# ---------------------------------------------------------------- API


@pytest.fixture()
def client(monkeypatch):
    from app import composition
    monkeypatch.setattr(composition, "build_glossary", lambda: _SRC)
    return TestClient(_chat_app())


def test_보장질문_응답에_verdict가_없다(client):
    b = client.post("/v1/chat", json={"message": "도수치료 보장되나요?"}).json()
    assert b["intent"] == "precheck"
    assert b["next_action"] == "precheck_form"
    assert "verdict" not in b
    assert b["quotes"] == []


def test_용어질문은_200과_인용(client):
    r = client.post("/v1/chat", json={"message": "통원 뜻"})
    assert r.status_code == 200
    b = r.json()
    assert b["found"] is True and b["term"] == "통원" and b["quotes"]


def test_못찾음도_200이다(client):
    """★"약관에 없다"는 오류가 아니라 정상 결과다."""
    r = client.post("/v1/chat", json={"message": "존재할리없는낱말이 뭐야"})
    assert r.status_code == 200 and r.json()["found"] is False


def test_색인이_없으면_503(monkeypatch):
    from app import composition
    from app.core.errors import InfraError

    class _Broken:
        def find(self, *a, **k):
            raise InfraError("postgresql://admin:secret@internal/db C:/private/index.jsonl")

        def meta(self):
            raise InfraError("용어 색인 메타가 없습니다")

    monkeypatch.setattr(composition, "build_glossary", lambda: _Broken())
    r = TestClient(_chat_app()).post("/v1/chat", json={"message": "통원 뜻"})
    assert r.status_code == 503
    assert "secret" not in r.text and "private" not in r.text
    assert r.json()["detail"] == "서비스 의존 시스템을 사용할 수 없습니다."


def test_설정한_llm이_승인_원문을_설명한다(monkeypatch):
    from types import SimpleNamespace

    from app import composition
    from app.routers import chat as chat_router

    class _Model:
        def complete(self, prompt, **kwargs):
            assert "통원" in prompt and "의료기관에 입원하지 않고" in prompt
            return "통원은 입원하지 않고 의료기관을 방문해 치료받는 것을 뜻합니다."

    monkeypatch.setattr(composition, "build_glossary", lambda: _SRC)
    monkeypatch.setattr(chat_router, "_model", lambda: _Model())
    monkeypatch.setattr(
        chat_router,
        "get_settings",
        lambda: SimpleNamespace(
            LLM_CHAT_ENABLED=True,
            LLM_PROVIDER="openai",
            LLM_REQUEST_TIMEOUT_SECONDS=5.0,
            CHAT_RATE_LIMIT_PER_MINUTE=100,
            CHAT_LLM_MAX_CALLS_PER_MINUTE=0,
            CHAT_LLM_CACHE_TTL_SECONDS=0,
            CHAT_TRUST_FORWARDED_FOR=False,
        ),
    )
    monkeypatch.setattr(chat_router, "get_active_model", lambda: "configured-model")
    chat_router._reset_guard_for_tests()

    r = TestClient(_chat_app()).post("/v1/chat", json={"message": "통원 뜻"})
    assert r.status_code == 200
    body = r.json()
    assert body["llm"] == {
        "used": True,
        "provider": "openai",
        "model": "configured-model",
        "source": "call",
    }
    assert "입원하지 않고" in body["message"]
