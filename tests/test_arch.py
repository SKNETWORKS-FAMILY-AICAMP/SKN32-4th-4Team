"""TEST-ARCH-001 — Application 계층 프레임워크 무-import 검사(정적 스캔).

v3.2 §2 의존성 규칙: app/application/*는 FastAPI/LangChain/SQLAlchemy/openai를 import하지 않는다.
"""

from __future__ import annotations

import hashlib
import pathlib
import re
import zipfile

_APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "application"
_FORBIDDEN = ("fastapi", "langchain", "sqlalchemy", "openai")


def _import_lines(text: str) -> list[str]:
    """실제 import 문만 고른다.

    ★주석과 문서화 문자열은 뺀다. 주석에 `app.schemas` 를 **설명하려고** 적었는데
      그게 위반으로 잡혔다. 규칙을 적어 두는 것과 어기는 것은 다르다.
    """
    lines = []
    in_doc = False
    for raw in text.splitlines():
        s = raw.strip()
        #: 삼중따옴표 블록을 건너뛴다(한 줄에 열고 닫는 경우도 처리).
        if in_doc:
            if '"""' in s or "'''" in s:
                in_doc = False
            continue
        if s.startswith(('"""', "'''")) and not (
            s.count('"""') >= 2 or s.count("'''") >= 2
        ):
            in_doc = True
            continue
        if s.startswith("#"):
            continue
        if s.startswith("import ") or s.startswith("from "):
            lines.append(s)
    return lines


def test_arch_001_application_has_no_framework_imports():
    offenders: list[str] = []
    for py in _APP_DIR.rglob("*.py"):
        for line in _import_lines(py.read_text(encoding="utf-8")):
            for fb in _FORBIDDEN:
                if re.search(rf"\b{fb}\b", line):
                    offenders.append(f"{py.name}: {line}")
    assert offenders == [], f"Application 계층 금지 import 발견: {offenders}"


def test_application_package_exists():
    assert (_APP_DIR / "ports.py").exists()


# ── TEST-ARCH-002 — 클린아키텍처 2단계 경계 ────────────────────────────────
# 안쪽(app/core/{domain,ports,usecases})은 프레임워크도, 바깥 계층도 모른다.
# 경계를 하나만 두는 대신 그 하나는 절대 넘지 않는다.

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_INNER_DIRS = (
    _ROOT / "app" / "core" / "domain",
    _ROOT / "app" / "core" / "ports",
    _ROOT / "app" / "core" / "usecases",
)
#: 안쪽에서 import 하면 안 되는 바깥 패키지. 의존 방향은 항상 안쪽으로만.
_OUTER_PACKAGES = (
    "app.outer",
    "app.adapters",
    "app.routers",
    "app.db",
    "app.services",
    "app.mcp",
    "app.a2a",
    #: ★`app.schemas` 는 **pydantic DTO** 다 — HTTP 로 나가는 모양이다.
    #:   목록에 없어서 `app/core/usecases/precheck.py` 가 그걸 import 하고도
    #:   테스트를 빠져나갔다. **테스트의 빈틈이었다.**
    #:   판정 결과는 `app/core/domain/precheck_result.py` 에 순수 dataclass 로 두고,
    #:   HTTP 변환은 라우터가 한다.
    "app.schemas",
)


def _scan_inner() -> list[tuple[pathlib.Path, str]]:
    found: list[tuple[pathlib.Path, str]] = []
    for d in _INNER_DIRS:
        if not d.exists():
            continue
        for py in d.rglob("*.py"):
            for line in _import_lines(py.read_text(encoding="utf-8")):
                found.append((py, line))
    return found


def test_arch_002_inner_layer_exists():
    missing = [str(d.relative_to(_ROOT)) for d in _INNER_DIRS if not d.exists()]
    assert missing == [], f"안쪽 계층 디렉터리 누락: {missing}"


def test_arch_002_inner_layer_has_no_framework_imports():
    offenders = [
        f"{py.relative_to(_ROOT)}: {line}"
        for py, line in _scan_inner()
        for fb in _FORBIDDEN
        if re.search(rf"\b{fb}\b", line)
    ]
    assert offenders == [], f"안쪽 계층 금지 import 발견: {offenders}"


def test_arch_002_inner_layer_does_not_import_outer():
    offenders = [
        f"{py.relative_to(_ROOT)}: {line}"
        for py, line in _scan_inner()
        for pkg in _OUTER_PACKAGES
        if re.search(rf"\b{re.escape(pkg)}\b", line)
    ]
    assert offenders == [], f"안쪽 계층이 바깥을 참조합니다(의존 방향 역전): {offenders}"


# ── ARCH-003 ─────────────────────────────────────────────────────────────
# 도메인 코드가 계층 밖에 새로 생기는 것을 막는다.
#
# ★왜 필요한가 — 실제로 그랬다.
#   `app/core/domain/insurance.py` 에 `Verdict`·`PolicyVersion` 이 이미 있는데,
#   `app/insurance/` 라는 패키지를 새로 만들어 같은 것을 또 정의했다.
#   `app/core/domain/generation.py` 가 있는데 세대 로직도 다시 짰다.
#   같은 사실이 두 곳에 있으면 반드시 갈라진다. 갈라지면 어느 쪽이 맞는지
#   아무도 모르게 된다.

#: 도메인 로직이 있어서는 안 되는 자리(패키지 이름).
_FORBIDDEN_DOMAIN_PKGS = ("app/insurance", "app/domain", "app/models")

#: ★타입 이름을 하드코딩하면 **목록에 없는 중복을 놓친다.**
#:
#:   실제로 `KcdCode` 가 두 곳(`insurance.py` · `kcd_ranges.py`)에 정의돼 있었는데
#:   세 이름만 검사해서 빠져나갔다(코덱스가 잡았다).
#:   이제 `app/core/` 안에서 **같은 이름의 클래스가 두 번 정의됐는지** 스스로 찾는다.
#:
#: 예외 — 프레임워크 관례상 같은 이름이 여러 번 나오는 것.
_ALLOWED_DUP_CLASSES = {"Settings", "Config", "Meta"}


def test_arch_003_no_domain_package_outside_core():
    """`app/insurance` 같은 경계 밖 도메인 패키지를 만들지 않는다."""
    offenders = [
        pkg for pkg in _FORBIDDEN_DOMAIN_PKGS if (_ROOT / pkg).is_dir()
    ]
    assert offenders == [], (
        f"도메인 코드는 app/core/{{domain,ports,usecases}} 에 둔다. "
        f"경계 밖 패키지 발견: {offenders}"
    )


def test_arch_003_domain_types_defined_once():
    """★`app/core/` 안에서 같은 이름의 클래스가 두 번 정의되지 않는다.

    이름이 같으면 팀원이 어느 쪽을 import 해야 할지 모른다.
    역할이 다르면 **이름을 다르게** 지어야 한다.
    """
    seen: dict[str, set[str]] = {}
    for py in (_ROOT / "app" / "core").rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"^class\s+(\w+)", text, re.M):
            name = m.group(1)
            if name in _ALLOWED_DUP_CLASSES:
                continue
            seen.setdefault(name, set()).add(str(py.relative_to(_ROOT)))
    offenders = {k: sorted(v) for k, v in seen.items() if len(v) > 1}
    assert offenders == {}, (
        "같은 이름의 클래스가 여러 곳에 정의됐습니다. "
        f"역할이 다르면 이름을 나누세요: {offenders}"
    )


def test_arch_003_usecases_do_not_import_adapters():
    """유스케이스는 어댑터를 직접 부르지 않는다 — 포트로 주입받는다."""
    usecases = _ROOT / "app" / "core" / "usecases"
    offenders = [
        f"{py.relative_to(_ROOT)}: {line}"
        for py in usecases.rglob("*.py")
        for line in _import_lines(py.read_text(encoding="utf-8"))
        if re.search(r"\bapp\.adapters\b", line)
    ]
    assert offenders == [], f"유스케이스가 어댑터를 직접 import: {offenders}"


# ── ARCH-004 ─────────────────────────────────────────────────────────────
# 현행 코드가 레거시를 참조하지 않는다.
#
# ★왜 필요한가 — 실제로 그럴 뻔했다.
#   커머스 시딩 CSV 를 `legacy/` 로 옮기고 `settings.LEGACY_DATA_DIR` 을 만들어
#   현행 코드가 그걸 읽게 했다. 그러면 "레거시"가 아니라 **그냥 다른 폴더에 있는
#   현행 코드**다. 레거시는 통째로 지워도 되는 것이어야 한다.
#
#   그래서 압축본으로만 둔다. 이 테스트는 그 규칙이 다시 무너지지 않게 막는다.


#: 레거시를 코드로 참조하는 흔적.
_LEGACY_REF = re.compile(
    r'''["']legacy["']|legacy[/\\]|LEGACY_DATA_DIR|import\s+legacy|from\s+legacy'''
)


def _docstring_lines(source: str) -> set[int]:
    """문서화 문자열이 차지하는 줄 번호.

    ★줄 앞이 `#` 인지만 보면 **여러 줄 문서화 문자열의 가운데 줄**을 놓친다.
      실제로 "이 데이터셋은 `legacy/v3_commerce.zip` 안에 있다"고 적은 설명이
      코드 참조로 잡혀 ARCH-004 가 틀리게 실패했다(2026-08-02).
      설명을 지우면 **자료가 어디로 갔는지 아무도 모르게 된다** — 규칙이 기록을 죽이면 안 된다.
      그래서 AST 로 문서화 문자열만 정확히 골라 뺀다. 보통 문자열 리터럴은 그대로 검사한다.
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    lines: set[int] = set()
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
                lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def test_arch_004_current_code_does_not_reference_legacy():
    """`app/` · `tests/` · `scripts/` 가 `legacy/` 를 코드로 참조하지 않는다."""
    targets = [_ROOT / d for d in ("app", "tests", "scripts")]
    offenders: list[str] = []
    for base in targets:
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            if "__pycache__" in str(py) or py.name == "test_arch.py":
                continue
            source = py.read_text(encoding="utf-8", errors="replace")
            prose = _docstring_lines(source)
            for i, line in enumerate(source.splitlines(), 1):
                stripped = line.strip()
                #: 주석·문서화 문자열의 언급은 참조가 아니다.
                if stripped.startswith("#") or stripped.startswith("*") or i in prose:
                    continue
                if re.search(_LEGACY_REF, line):
                    offenders.append(f"{py.relative_to(_ROOT)}:{i}: {stripped[:70]}")
    assert offenders == [], (
        "현행 코드가 legacy/ 를 참조합니다. 레거시는 지워도 되는 것이어야 합니다:\n"
        + "\n".join(offenders)
    )


def test_arch_004_legacy_is_archived_not_importable():
    """레거시는 README와 **압축본**으로만 둔다 — 풀린 파일은 형식과 무관하게 금지한다.

    예전 검사는 `.py`만 찾아서 `legacy/_unified_mall/app/static/rag.html`·`rag.js`가
    풀린 채 남은 것을 통과시켰다. HTML/JS도 현행 코드가 경로로 읽으면 다시 의존성이
    생기므로 확장자 예외를 두지 않는다.
    """
    legacy = _ROOT / "legacy"
    if not legacy.exists():
        return  # 레거시가 없는 것은 정상이다
    loose = [
        str(p.relative_to(_ROOT))
        for p in legacy.rglob("*")
        if p.is_file() and p.name != "README.md" and p.suffix.lower() != ".zip"
    ]
    assert loose == [], (
        f"레거시에 풀린 파일이 있습니다. README와 zip만 허용합니다: {loose[:5]}"
    )


def test_arch_004_removed_commerce_seed_is_preserved() -> None:
    """제거된 커머스 시더와 제거 직전 CLI는 원래 경로로 보존한다."""
    archive_path = _ROOT / "legacy" / "v9_commerce_seed_cli.zip"
    assert archive_path.is_file()

    with zipfile.ZipFile(archive_path) as archive:
        entries = {name.replace("\\", "/"): name for name in archive.namelist()}
        expected_hashes = {
            "app/db/seed.py": "23662a2eb499676703f1099b6d47cfcc892a2039a0ac63b0954c2aba5135444c",
            "scripts/manage.py": "7a123af5ed0392de303b13923a709086c465dd58ac3c0b18f00d08898a1851ac",
        }
        actual_hashes = {
            path: hashlib.sha256(archive.read(entries[path])).hexdigest()
            for path in expected_hashes
            if path in entries
        }

    assert actual_hashes == expected_hashes
    assert not (_ROOT / "app" / "db" / "seed.py").exists()


def test_arch_004_removed_commerce_ingest_cli_is_preserved() -> None:
    """제거 직전의 구형 FAISS ingest CLI를 원래 경로로 보존한다."""
    archive_path = _ROOT / "legacy" / "v14_commerce_faiss_ingest_cli.zip"
    assert archive_path.is_file()
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == (
        "39c3097e943c449274d4bc5627abd842c870364ca4d6cb4347add58f3a15481a"
    )

    with zipfile.ZipFile(archive_path) as archive:
        entries = {name.replace("\\", "/"): name for name in archive.namelist()}
        content = archive.read(entries["scripts/manage.py"])

    assert hashlib.sha256(content).hexdigest() == (
        "4d6095dcbc812db74b7106dbdca8d9844ec483cd8e2139941f5317f2c255b5b5"
    )
    current = (_ROOT / "scripts" / "manage.py").read_text(encoding="utf-8")
    assert "cmd_ingest" not in current
    assert "app.rag.build_index" not in current


def test_arch_004_removed_commerce_html_is_preserved() -> None:
    """구형 HTML 3개는 ZIP에 원래 경로로 보존하고 loose 파일을 남기지 않는다."""
    archive_path = _ROOT / "legacy" / "v15_commerce_html_docs.zip"
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == (
        "d09b34a1ffb503a34486cc268c2d3399d091c5ebb54405c8bd738a2c14585bba"
    )
    expected_hashes = {
        "docs/AI_에이전트_커머스_지식바운티_통합_계획서_시각화.html":
            "38a2f82df8a428df90901ed458f1aee01450dafd8aabeb5f7f21a3c423af2fe3",
        "docs/project_overview.html":
            "19688ecdfbc7418731eb69407344c205db4d50f9e2d8bfa13a461830a0f4c334",
        "docs/screen_walkthrough.html":
            "7087c045b1b782d1d5c47d4ccc3a5bc3e52bb834befec84426220dd3d003920f",
    }

    with zipfile.ZipFile(archive_path) as archive:
        entries = {name.replace("\\", "/"): name for name in archive.namelist()}
        actual_hashes = {
            path: hashlib.sha256(archive.read(entries[path])).hexdigest()
            for path in expected_hashes
        }

    assert actual_hashes == expected_hashes
    assert not [path for path in expected_hashes if (_ROOT / path).exists()]


def test_arch_004_removed_legacy_runtime_sources_are_preserved() -> None:
    """구형 RAG·바운티·CS 묶음은 원래 경로가 든 고정 ZIP으로만 남는다."""
    archives = {
        "v16_commerce_rag_runtime.zip": (
            "d5ac8fac839e539199152989f6acac172da96e951e3b5d7cfc91e73bcad75b83",
            {"app/rag/service.py", "app/routers/rag.py", "data/vector_store/index.faiss"},
        ),
        "v17_knowledge_bounty_runtime.zip": (
            "4aacea8e3fe7673d734f54787f72f4a8e34c17361e49a861be7854e495dd3acf",
            {"app/routers/bounty.py", "app/application/bounty.py"},
        ),
        "v18_commerce_cs_workflow.zip": (
            "2746730791d696dc2ebf468b76422bcf7b6c875cb4ebfe377800aa9ad11a7a4e",
            {"app/routers/workflow.py", "app/workflow/ticket_graph.py"},
        ),
    }
    removed = set()
    for name, (expected_sha, expected_entries) in archives.items():
        archive_path = _ROOT / "legacy" / name
        assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == expected_sha
        with zipfile.ZipFile(archive_path) as archive:
            entries = {entry.replace("\\", "/") for entry in archive.namelist()}
        assert expected_entries <= entries
        removed.update(expected_entries)

    assert not [path for path in removed if (_ROOT / path).exists()]


def test_arch_004_clause_index_legacy_import_snapshot_is_preserved() -> None:
    """현행 조항 빌더에서 구형 RAG import를 걷기 전 소스도 원래 경로로 보존한다."""
    archive_path = _ROOT / "legacy" / "v19_clause_index_legacy_import_snapshot.zip"
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == (
        "4b4bfad134141db72c1c650c584f4ad8d8f7333734af324e681e4e04bfe67101"
    )
    with zipfile.ZipFile(archive_path) as archive:
        entries = {entry.replace("\\", "/"): entry for entry in archive.namelist()}
        content = archive.read(entries["scripts/index/build_clause_index.py"])
    assert hashlib.sha256(content).hexdigest() == (
        "dbbbb0437f7c1a1985843999eb14c323a3ab312d0ea06573328f01959858cb70"
    )
    current = (_ROOT / "scripts" / "index" / "build_clause_index.py").read_text(
        encoding="utf-8"
    )
    assert "app.rag.embeddings" not in current
    assert "clause_document_embedder" in current


def test_arch_004_removed_commerce_cs_nlp_is_preserved() -> None:
    """CS 분류·감성 고립섬과 수정 전 공유 파일은 v20에 원래 경로로 보존한다."""
    archive_path = _ROOT / "legacy" / "v20_commerce_cs_nlp.zip"
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == (
        "50fa760cccdec667cd4fe02fdb311bbea941e027512b18092feff0a4cdc235e3"
    )
    expected = {
        "app/prompts/classifier.py",
        "app/ml/sentiment.py",
        "scripts/finetune/prepare_splits.py",
        "scripts/finetune/eval_baselines.py",
        "tests/test_classifier.py",
        "tests/test_sentiment.py",
        "tests/test_sentiment_labels.py",
        "app/core/config.py",
        "app/workflow/__init__.py",
        "docs/TODO/finetuning_20260804-2109.md",
    }
    with zipfile.ZipFile(archive_path) as archive:
        entries = {entry.replace("\\", "/") for entry in archive.namelist()}
    assert entries == expected

    removed = {
        "app/prompts/classifier.py",
        "app/ml/sentiment.py",
        "scripts/finetune/prepare_splits.py",
        "scripts/finetune/eval_baselines.py",
        "tests/test_classifier.py",
        "tests/test_sentiment.py",
        "tests/test_sentiment_labels.py",
    }
    assert not [path for path in removed if (_ROOT / path).exists()]
    current_config = (_ROOT / "app" / "core" / "config.py").read_text(encoding="utf-8")
    assert "SENTIMENT_MODEL" not in current_config
