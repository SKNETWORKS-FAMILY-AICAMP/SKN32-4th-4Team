"""Backward-compatible import wrapper; implementation moved to db.postgres."""

from db.postgres.pg_insurance_repository import *  # noqa: F401,F403
from db.postgres.pg_insurance_repository import _postgres_error
