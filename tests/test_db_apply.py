"""번호형 DB 마이그레이션 적용기의 안전 계약을 고정한다."""

from __future__ import annotations

import sys

import pytest

from scripts.db import apply


class _DryRunCursor:
    def __init__(self, done: dict[str, str]) -> None:
        self._done = done

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query: str, _params=None) -> None:
        self._query = query

    def fetchone(self):
        assert "to_regclass" in self._query
        return ("public.schema_migration",)

    def fetchall(self):
        assert "filename, checksum" in self._query
        return list(self._done.items())


class _DryRunConnection:
    def __init__(self, done: dict[str, str]) -> None:
        self._cursor = _DryRunCursor(done)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor


@pytest.mark.parametrize(
    ("recorded_checksum", "expected_code", "expected_mark"),
    [
        (None, 0, "would"),
        ("same", 0, "skip"),
        ("different-checksum", 1, "STOP"),
    ],
)
def test_dry_run_exit_code_reflects_checksum_conflict(
    tmp_path, monkeypatch, capsys, recorded_checksum, expected_code, expected_mark
):
    migration = tmp_path / "001_sample.sql"
    migration.write_text("SELECT 1;\n", encoding="utf-8")
    current_checksum = apply._sha(migration.read_text(encoding="utf-8"))
    ledger_name = "test/001_sample.sql"
    done = {}
    if recorded_checksum == "same":
        done[ledger_name] = current_checksum
    elif recorded_checksum is not None:
        done[ledger_name] = recorded_checksum

    import psycopg

    monkeypatch.setitem(apply.TRACKS, "test", (tmp_path, 12345))
    monkeypatch.setattr(psycopg, "connect", lambda _dsn: _DryRunConnection(done))
    monkeypatch.setattr(
        sys,
        "argv",
        ["apply.py", "--dsn", "postgresql://test", "--track", "test", "--dry-run"],
    )

    assert apply.main() == expected_code
    assert expected_mark in capsys.readouterr().out
