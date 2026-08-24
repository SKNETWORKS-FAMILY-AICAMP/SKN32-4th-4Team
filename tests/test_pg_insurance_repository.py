"""보험 repository의 포트·transaction·오류 경계 단위 테스트."""

from __future__ import annotations

import pytest

from app.adapters.pg_insurance_repository import (
    PgInsuranceRepository,
    _postgres_error,
)
from app.core.errors import (
    ConflictErr,
    ForbiddenErr,
    InfraError,
    TransientInfraError,
    ValidationErr,
)
from app.core.ports.insurance_repository import InsuranceRepositoryPort


class _FakeConnection:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class _PgError(Exception):
    def __init__(self, sqlstate: str, constraint: str | None = None):
        self.sqlstate = sqlstate
        self.diag = type("Diag", (), {"constraint_name": constraint})()


def test_repository가_안쪽_포트를_만족한다():
    repository = PgInsuranceRepository("postgresql://example.invalid/db")
    assert isinstance(repository, InsuranceRepositoryPort)


def test_dsn이_없으면_다른_저장소로_폴백하지_않는다():
    with pytest.raises(InfraError, match="INSURANCE_PG_DSN"):
        PgInsuranceRepository("  ")


def test_transaction은_성공시_commit하고_예외시_rollback한다(monkeypatch):
    repository = PgInsuranceRepository("postgresql://example.invalid/db")
    committed = _FakeConnection()
    monkeypatch.setattr(repository, "_connect", lambda: committed)
    with repository.transaction():
        pass
    assert (committed.commits, committed.rollbacks, committed.closed) == (1, 0, True)

    rolled_back = _FakeConnection()
    monkeypatch.setattr(repository, "_connect", lambda: rolled_back)
    with pytest.raises(RuntimeError, match="probe"):
        with repository.transaction():
            raise RuntimeError("probe")
    assert (rolled_back.commits, rolled_back.rollbacks, rolled_back.closed) == (0, 1, True)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("23505", ConflictErr),
        ("23P01", ConflictErr),
        ("23503", ValidationErr),
        ("23514", ValidationErr),
        ("42501", ForbiddenErr),
        ("40001", TransientInfraError),
        ("40P01", TransientInfraError),
        ("53300", TransientInfraError),
        ("08006", TransientInfraError),
        ("XX000", InfraError),
    ],
)
def test_postgresql_sqlstate를_도메인_오류로_매핑한다(state, expected):
    assert isinstance(_postgres_error(_PgError(state, "probe_constraint")), expected)
