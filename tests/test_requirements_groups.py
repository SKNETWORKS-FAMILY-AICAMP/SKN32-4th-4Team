"""역할별 requirements 그룹과 루트 후방 호환 계약."""

from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
GROUPS = (
    "runtime.txt",
    "preprocess.txt",
    "index.txt",
    "local-ml-optional.txt",
    "dev.txt",
)


def _specs(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _package_name(spec: str) -> str:
    match = re.match(r"([A-Za-z0-9_.-]+)", spec)
    assert match, f"패키지 요구사항을 파싱할 수 없음: {spec}"
    return match.group(1).lower().replace("_", "-")


def test_root_requirements_keeps_full_install_compatibility() -> None:
    assert _specs(ROOT / "requirements.txt") == [
        f"-r requirements/{name}" for name in GROUPS
    ]
    assert all((ROOT / "requirements" / name).is_file() for name in GROUPS)


def test_grouped_requirements_have_one_owner_per_package() -> None:
    owners: dict[str, str] = {}
    duplicates: list[str] = []
    for group in GROUPS:
        for spec in _specs(ROOT / "requirements" / group):
            assert not spec.startswith("-r "), f"그룹 내 재귀 include 금지: {group}"
            package = _package_name(spec)
            if package in owners:
                duplicates.append(f"{package}: {owners[package]}, {group}")
            owners[package] = group

    assert not duplicates, f"의존성 그룹 중복: {duplicates}"


def test_critical_dependencies_are_in_their_declared_groups() -> None:
    expected = {
        "runtime.txt": "fastapi",
        "preprocess.txt": "pymupdf",
        "index.txt": "sentence-transformers",
        "local-ml-optional.txt": "insightface",
        "dev.txt": "pytest",
    }
    for group, package in expected.items():
        names = {
            _package_name(spec)
            for spec in _specs(ROOT / "requirements" / group)
        }
        assert package in names, f"{group}에 {package} 누락"
