"""공개 Git 저장소에 올릴 수 있는 코드 묶음인지 검사한다.

실제 약관 원문, 사용자 데이터, 모델 가중치와 내부 문서는 별도 전달 대상이다.
이 스크립트는 공개본의 파일 경계와 외부 자원 없이 실행 가능한 회귀검사를 함께 확인한다.
"""

from __future__ import annotations

import compileall
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

PUBLIC_TESTS = (
    "tests/test_agent_app.py",
    "tests/test_app_route_snapshots.py",
    "tests/test_auth.py",
    "tests/test_chat.py",
    "tests/test_citation_guard.py",
    "tests/test_clause_text.py",
    "tests/test_cohort.py",
    "tests/test_cohort_api.py",
    "tests/test_config.py",
    "tests/test_cover_match.py",
    "tests/test_db_apply.py",
    "tests/test_demo_simulator.py",
    "tests/test_demo_track_isolation.py",
    "tests/test_errors.py",
    "tests/test_graph.py",
    "tests/test_health.py",
    "tests/test_identification_mode.py",
    "tests/test_knowledge_gap_wiring.py",
    "tests/test_model_registry.py",
    "tests/test_non_pg_admin_regressions.py",
    "tests/test_observations_store.py",
    "tests/test_policy_version.py",
    "tests/test_precheck_citation_display.py",
    "tests/test_precheck_verify.py",
    "tests/test_prompts.py",
    "tests/test_public_api_error_sanitization.py",
    "tests/test_requirements_groups.py",
    "tests/test_run_events.py",
    "tests/test_security_cases.py",
    "tests/test_signup_secret.py",
    "tests/test_static_ui.py",
    "tests/test_terms_api.py",
    "tests/test_trace.py",
    "tests/test_user_admin.py",
    "tests/test_verify_normalized.py",
    "tests/test_verify_url_anchor.py",
)

FORBIDDEN_ROOTS = {
    ".env",
    ".env.example",
    ".github",
    ".venv",
    "CLAUDE.md",
    "RULE.md",
    "artifacts",
    "backups",
    "docs",
    "legacy",
}

SECRET_PATTERNS = {
    "PEM 비밀키": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "OpenAI 형식 키": re.compile(rb"sk-[A-Za-z0-9_-]{40,}"),
    "Google 형식 키": re.compile(rb"AIza[0-9A-Za-z_-]{30,}"),
    "GitHub 형식 토큰": re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}"),
    "AWS 형식 키": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "개인 Windows 사용자 경로": re.compile(rb"[A-Za-z]:\\Users\\[^\\\r\n]+"),
    "사설 IPv4 주소": re.compile(
        rb"(?<![0-9])(?:10(?:\.[0-9]{1,3}){3}|192\.168(?:\.[0-9]{1,3}){2}|"
        rb"172\.(?:1[6-9]|2[0-9]|3[01])(?:\.[0-9]{1,3}){2})(?![0-9])"
    ),
}


def _candidate_files() -> list[Path]:
    """Git이 추적 중이거나 다음 add에 포함할 파일만 반환한다."""

    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    )
    return [ROOT / raw.decode("utf-8") for raw in proc.stdout.split(b"\0") if raw]


def _check_public_boundary(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in files:
        rel = path.relative_to(ROOT)
        parts = rel.parts
        if parts[0] in FORBIDDEN_ROOTS:
            errors.append(f"금지된 경로: {rel.as_posix()}")
        if any(part.startswith(".") for part in parts) and rel.as_posix() != ".gitignore":
            errors.append(f"숨김 경로: {rel.as_posix()}")
        if any(part == "__pycache__" for part in parts) or path.suffix in {".pyc", ".pyo"}:
            errors.append(f"Python 캐시: {rel.as_posix()}")
        lowered = path.name.lower()
        if lowered.endswith((".bak", ".backup", ".tmp", ".orig", ".zip", ".tgz")):
            errors.append(f"백업 또는 임시 파일: {rel.as_posix()}")
        if parts[0] == "data":
            allowed = len(parts) == 4 and parts[1:3] == ("raw", "manifests") and path.suffix == ".jsonl"
            if not allowed:
                errors.append(f"공개 범위를 벗어난 데이터: {rel.as_posix()}")
        if path.is_file() and path.stat().st_size > 10 * 1024 * 1024:
            errors.append(f"10 MiB를 넘는 파일: {rel.as_posix()}")
    return errors


def _check_secrets(files: list[Path]) -> list[str]:
    errors: list[str] = []
    for path in files:
        if not path.is_file() or path.stat().st_size > 5 * 1024 * 1024:
            continue
        payload = path.read_bytes()
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(payload):
                errors.append(f"{label} 후보: {path.relative_to(ROOT).as_posix()}")
    return errors


def main() -> int:
    files = _candidate_files()
    errors = _check_public_boundary(files) + _check_secrets(files)
    if errors:
        print("[실패] 공개 저장소 경계 검사에서 문제가 발견됐습니다.", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"[통과] 공개 후보 파일 {len(files)}개: 금지 경로·대용량 파일·대표 비밀키 없음")

    for directory in ("app", "db", "scripts"):
        if not compileall.compile_dir(ROOT / directory, quiet=1):
            print(f"[실패] {directory} Python 문법 검사", file=sys.stderr)
            return 1
    print("[통과] Python 문법 검사")

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", *PUBLIC_TESTS],
        cwd=ROOT,
        check=False,
    )
    if proc.returncode:
        print("[실패] 공개본 회귀검사", file=sys.stderr)
        return proc.returncode
    print("[통과] 공개본 회귀검사")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
