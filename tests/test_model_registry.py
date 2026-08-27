"""Phase 1 — 모델 레지스트리 스키마·하드코딩 스캔.

REQ-LLM-REG-01: TEST-LLM-REG-001(스키마 검증). REQ-LLM-REG-02: TEST-LLM-REG-002(신 코드 하드코딩 모델ID 0).
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.core.errors import ConfigError
from app.core.model_registry import ModelProfile, get_active_profile, load_registry

_APP = pathlib.Path(__file__).resolve().parents[1] / "app"


def test_llm_reg_001_registry_loads_and_validates():
    reg = load_registry()
    assert reg, "레지스트리에 최소 1개 프로필"
    for p in reg.values():
        assert p.provider_model_id and "latest" not in p.provider_model_id.lower()
        assert p.provider and p.runtime


def test_llm_reg_001_rejects_latest_alias():
    with pytest.raises(Exception):
        ModelProfile(profile_id="x", provider="local", provider_model_id="gemma-latest", runtime="llama-cpp-python")


def test_llm_reg_001_rejects_empty_model_id():
    with pytest.raises(Exception):
        ModelProfile(profile_id="x", provider="local", provider_model_id="  ", runtime="rt")


def test_active_profile_resolves():
    p = get_active_profile()
    assert p.profile_id  # 기본 ACTIVE_MODEL_PROFILE이 레지스트리에 존재


def test_unknown_active_profile_raises(monkeypatch):
    from app.core import model_registry as mr

    monkeypatch.setattr(mr, "load_registry", lambda path=None: {})
    with pytest.raises(ConfigError):
        get_active_profile()


def test_verified_flag():
    # 현재 프로필은 checksum/verified_at이 null → verified=False
    for p in load_registry().values():
        assert p.verified == bool(p.artifact_sha256 and p.verified_at)


# 알려진 모델 ID 패턴. 넓혔지만 **완전 보장은 아니다**(동적 문자열 조합·미등록 모델명은
# 정적 스캔으로 못 잡음 — 일반적 한계, Codex 지적 반영). 이 테스트는 신 Clean Arch 코드가
# 모델 ID를 소스에 리터럴로 박지 않았는지 확인하는 **휴리스틱 가드**다.
_MODEL_ID_PATTERNS = [
    r"gpt-\d", r"gpt-4", r"gpt-5", r"gemini-\d", r"gemma-\d",
    r"qwen[\d.]", r"claude-", r"\bo1-", r"\bo3-", r"\bo4-", r"koelectra",
    r"text-embedding", r"sroberta", r"electra", r"llama-\d", r"mistral",
]
# 신 Clean Arch 계층만 검사(범위 의도적): 레거시 app/core/config.py·rag/qa.py의 모델ID는
# Phase 8(MCP 수렴)에서 제거. 전 리포지토리 보장이 아니라 '신 코드 무하드코딩'만 강제한다.
_NEW_CODE_DIRS = [_APP / "application", _APP / "adapters"]


def _code_only(source: str) -> str:
    """주석과 문서화 문자열을 **뺀** 소스만 돌려준다.

    ★설명문에 적힌 모델 이름은 하드코딩이 아니다.

        `pgvector_clause_index.py` 주석에 "지금 모델의 최대 입력이 512인 줄 알았는데
        실제로는 128이었다"는 **결함 기록**을 적었더니 이 가드가 잡았다.
        지우면 다음 사람이 같은 함정을 다시 밟는다 —
        **규칙이 기록을 죽이면 안 된다**(`ARCH-004` 에서도 같은 결정을 했다).

    ★그렇다고 느슨해지지 않는다. 주석은 실행되지 않으므로 모델을 고를 수 없다.
      실제 하드코딩(변수 대입·함수 인자)은 그대로 잡힌다.
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    #: 문서화 문자열이 차지하는 줄을 모은다.
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                doc_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))

    kept: list[str] = []
    for i, line in enumerate(source.splitlines(), 1):
        if i in doc_lines:
            continue
        #: 줄 주석(전체 줄 · 꼬리 주석 모두)을 잘라 낸다.
        #: 문자열 안의 `#` 까지 정확히 가리려면 토큰화가 필요하지만,
        #: 모델 ID 가 `#` 뒤 문자열 안에 숨는 경우는 없다 — 과하게 복잡해질 뿐이다.
        cut = line.find("#")
        kept.append(line if cut < 0 else line[:cut])
    return "\n".join(kept)


def test_llm_reg_002_no_hardcoded_model_ids_in_new_code():
    """휴리스틱: 신 Clean Arch 코드(application/adapters)에 리터럴 모델ID 0.

    한계(정직): 동적 조합·미등록 패턴·범위 밖 디렉터리는 못 잡는다. 전면 보장은 legacy 수렴
    (Phase 8) + 이 스캔의 지속 보강으로 달성한다.
    ★주석·문서화 문자열은 검사하지 않는다 — 실행되지 않으므로 모델을 고를 수 없다.
    """
    offenders: list[str] = []
    for root in _NEW_CODE_DIRS:
        for py in root.rglob("*.py"):
            code = _code_only(py.read_text(encoding="utf-8"))
            for pat in _MODEL_ID_PATTERNS:
                for m in re.finditer(pat, code, re.IGNORECASE):
                    offenders.append(f"{py.name}: '{m.group(0)}'")
    assert offenders == [], f"신 코드에 하드코딩 모델ID: {offenders}"


def test_주석_제외가_실제_하드코딩까지_눈감지_않는다():
    """★가드를 느슨하게 했으면 **여전히 잡는지** 확인해야 한다."""
    real = "MODEL = 'jhgan/ko-sroberta-multitask'\n"
    doc = '"""설명: ko-sroberta 를 쓰던 때 128토큰에서 잘렸다."""\n'
    comment = "#: ko-sroberta 는 128토큰이었다\n"
    assert "sroberta" in _code_only(real)
    assert "sroberta" not in _code_only(doc)
    assert "sroberta" not in _code_only(comment)
