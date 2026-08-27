"""Final claim outcome persistence contract without a database."""

from __future__ import annotations

from datetime import date

import pytest

from app.core.errors import ValidationErr
from app.core.usecases.persist_outcome import (
    PersistOutcomeCommand,
    _key_hash,
    persist,
)


def _command(**changes):
    values = {
        "precheck_trace_id": "trace-001",
        "precheck_idempotency_key": "precheck-key-001",
        "claimed_on": date(2025, 1, 2),
        "decided_on": date(2025, 2, 1),
        "outcome": "paid",
        "outcome_reason": "approved",
        "evidence_doc_type": "decision_notice",
        "evidence_sha256": "a" * 64,
        "evidence_stored_ref": "object://decision",
        "idempotency_key": "outcome-key-001",
        "idempotency_secret": "x" * 32,
        "client_ref": "client-a",
        "channel": "public-api",
        "request_snapshot": {"outcome": "paid"},
    }
    values.update(changes)
    return PersistOutcomeCommand(**values)


def test_outcome_key는_client_scope_hmac이고_raw_key를_포함하지_않는다():
    first = _key_hash(_command())
    other_client = _key_hash(_command(client_ref="client-b"))
    assert len(first) == 64
    assert first != other_client
    assert "outcome-key-001" not in first


@pytest.mark.parametrize(
    "command",
    [
        _command(outcome="pending"),
        _command(decided_on=date(2024, 12, 31)),
        _command(evidence_sha256="bad"),
        _command(idempotency_key="short"),
        _command(idempotency_secret="short"),
    ],
)
def test_추정하거나_원장에_넣을_수_없는_outcome입력은_쓰기전에_거절한다(command):
    class MustNotWrite:
        def transaction(self):
            raise AssertionError("invalid input must fail before opening a transaction")

    with pytest.raises(ValidationErr):
        persist(command, repository=MustNotWrite())
