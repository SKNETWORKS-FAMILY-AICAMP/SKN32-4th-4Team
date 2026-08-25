"""Read-only audit of the local PostgreSQL databases used by this workspace."""

from __future__ import annotations

import argparse
import json
from typing import Any

import psycopg
from psycopg import sql


def _connect(database: str, host: str, port: int, user: str) -> psycopg.Connection:
    return psycopg.connect(
        host=host,
        port=port,
        user=user,
        dbname=database,
        connect_timeout=5,
    )


def _databases(conn: psycopg.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT datname FROM pg_database "
            "WHERE datistemplate = false ORDER BY datname"
        ).fetchall()
    ]


def _database_report(
    database: str, *, host: str, port: int, user: str
) -> dict[str, Any]:
    with _connect(database, host, port, user) as conn:
        tables = conn.execute(
            "SELECT table_schema, table_name "
            "FROM information_schema.tables "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
            "ORDER BY table_schema, table_name"
        ).fetchall()
        views = conn.execute(
            "SELECT table_schema, table_name "
            "FROM information_schema.views "
            "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
            "ORDER BY table_schema, table_name"
        ).fetchall()
        foreign_keys = conn.execute(
            "SELECT count(*) FROM information_schema.table_constraints "
            "WHERE constraint_type = 'FOREIGN KEY'"
        ).fetchone()[0]
        row_counts: dict[str, int] = {}
        for schema, table in tables:
            identifier = sql.SQL("SELECT count(*) FROM {}.{}").format(
                sql.Identifier(schema), sql.Identifier(table)
            )
            row_counts[f"{schema}.{table}"] = conn.execute(identifier).fetchone()[0]

        migration = None
        if any(schema == "public" and table == "schema_migration" for schema, table in tables):
            migration = conn.execute(
                "SELECT count(*), max(filename) FROM public.schema_migration"
            ).fetchone()

    return {
        "tables": [f"{schema}.{table}" for schema, table in tables],
        "views": [f"{schema}.{table}" for schema, table in views],
        "foreign_keys": foreign_keys,
        "row_counts": row_counts,
        "migration": {
            "count": migration[0],
            "latest": migration[1],
        }
        if migration
        else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="read-only PostgreSQL state audit")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5433)
    parser.add_argument("--user", default="postgres")
    args = parser.parse_args()

    with _connect("postgres", args.host, args.port, args.user) as conn:
        databases = _databases(conn)

    reports: dict[str, dict[str, Any]] = {}
    skipped_databases: dict[str, str] = {}
    for database in databases:
        try:
            reports[database] = _database_report(
                database, host=args.host, port=args.port, user=args.user
            )
        except psycopg.OperationalError as exc:
            # A stale pg_database entry can remain after an interrupted test
            # database cleanup. Keep the audit read-only and continue with the
            # databases that are actually connectable.
            skipped_databases[database] = str(exc).splitlines()[0]

    print(
        json.dumps(
            {
                "read_only": True,
                "host": args.host,
                "port": args.port,
                "databases": databases,
                "reports": reports,
                "skipped_databases": skipped_databases,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
