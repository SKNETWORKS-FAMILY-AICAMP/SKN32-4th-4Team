"""Django 전달 계층이 FastAPI 코어를 복제하는 import를 막는다."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.delivery

_ROOT = Path(__file__).resolve().parents[2]
_DJANGO_PACKAGE = _ROOT / "delivery" / "django_app"
_FORBIDDEN_IMPORTS = ("app.core.usecases", "app.workflow", "app.routers")


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


def test_django_패키지는_도메인_구현을_import하지_않는다():
    offenders = [
        f"{path.relative_to(_ROOT)}: {module}"
        for path in _DJANGO_PACKAGE.rglob("*.py")
        for module in _imported_modules(path)
        if any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for forbidden in _FORBIDDEN_IMPORTS
        )
    ]
    assert offenders == [], f"Django 전달 계층의 금지 import: {offenders}"
