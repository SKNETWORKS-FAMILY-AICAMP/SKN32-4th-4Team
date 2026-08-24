# SQLite legacy

The implementation is in `connection.py` and `models.py`. The old
`app/db/database.py` and `app/db/models.py` paths remain thin compatibility
imports for tests and recovery tooling.

This adapter is not an automatic PostgreSQL fallback. It is retained for
local tests and offline recovery only. The current runtime SQLite database is
quarantined at:

`data/db/_quarantine/20260811_sqlite_legacy/insurance.sqlite3.zip`

The loose runtime file is intentionally absent. The verified rollback copy is
stored beside the archive as `insurance.sqlite3.rollback`. Restore it only for
an explicitly authorized offline recovery/test, then remove it from the
runtime path again after verification.

Production cutover settings are documented in
`config/production.env.example`.
