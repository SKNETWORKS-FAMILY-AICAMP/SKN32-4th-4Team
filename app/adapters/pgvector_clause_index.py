"""Backward-compatible module alias; implementation lives in ``db.postgres``."""

import sys

from db.postgres import pgvector_clause_index as _implementation

# Preserve the old module's complete public surface, including names outside
# ``__all__`` that existing tests and operational tooling monkeypatch.
sys.modules[__name__] = _implementation
