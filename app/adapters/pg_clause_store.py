"""Backward-compatible module alias; implementation lives in ``db.postgres``."""

import sys

from db.postgres import pg_clause_store as _implementation

sys.modules[__name__] = _implementation
