import json
from types import SimpleNamespace

import pytest

from app.adapters.llm_assessment_explainer import OpenAICompatibleAssessmentExplainer
from app.core.domain.insurance import Verdict
from app.core.domain.precheck_result import PrecheckInput
from app.core.errors import LLMOutputError
from app.core.ports.precheck import ClauseRow
from app.core.usecases.assess import assess, explain_generated
from app.core.usecases.retrieval import EvidenceBundleV1


SHA = "a" * 64


def _bundle():
    row = ClauseRow(
        sha256=SHA,
        qualified_no="보통약관/제1조",
        clause_no="제1조",
        section="보통약관",
        title="",
        text="F32 질병의 치료비는 보상하지 않습니다.",
        page_from=1,
        page_to=1,
        content_hash="1" * 64,
        citation_eligible=True,
        chunk_type="clause",
        parse_status="ok",
    )
    return EvidenceBundleV1(policy_version_sha=SHA, clauses=[row])


class _Completions:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def _client(content):
    completions = _Completions(content)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def test_LLM은_설명만_만들고_인용검증을_통과해야_나간다():
    bundle = _bundle()
    assessment = assess(bundle, PrecheckInput("보험사", "20200101", ("F32",)))
    content = json.dumps({
        "verdict": "unlikely",
        "abstained": False,
        "citations": [{
            "handle": "E001",
            "quote": "F32 질병의 치료비는 보상하지 않습니다.",
        }],
        "reason": "근거 E001: “F32 질병의 치료비는 보상하지 않습니다.”",
    }, ensure_ascii=False)
    client, calls = _client(content)
    result = explain_generated(
        assessment,
        bundle,
        generator=OpenAICompatibleAssessmentExplainer(client=client, model="test-model"),
    )
    assert result.verdict is Verdict.UNLIKELY
    assert result.abstained is False
    assert calls.calls[0]["temperature"] == 0
    response_format = calls.calls[0]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


def test_LLM_스키마오류를_고정문구로_숨기지_않는다():
    bundle = _bundle()
    assessment = assess(bundle, PrecheckInput("보험사", "20200101", ("F32",)))
    client, _ = _client("not-json")
    with pytest.raises(LLMOutputError, match="JSON 스키마"):
        explain_generated(
            assessment,
            bundle,
            generator=OpenAICompatibleAssessmentExplainer(client=client, model="test-model"),
        )


def test_LLM이_판정을_바꾸면_검증단계가_폐기한다():
    bundle = _bundle()
    assessment = assess(bundle, PrecheckInput("보험사", "20200101", ("F32",)))
    content = json.dumps({
        "verdict": "likely_covered",
        "abstained": False,
        "citations": [{"handle": "E001", "quote": "F32"}],
        "reason": "F32",
    })
    client, _ = _client(content)
    result = explain_generated(
        assessment,
        bundle,
        generator=OpenAICompatibleAssessmentExplainer(client=client, model="test-model"),
    )
    assert result.verdict is Verdict.NEEDS_EXPERT
    assert result.abstained is True
