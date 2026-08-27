"""Migrate the remaining SQLite runtime records to PostgreSQL.

The command is deliberately read-only unless ``--apply`` is supplied. It never
deletes or modifies the SQLite source. Re-running against the same PostgreSQL
database is allowed only when existing rows match the source exactly.

Examples::

    python -m scripts.db.migrate_sqlite_runtime --dry-run
    python -m scripts.db.migrate_sqlite_runtime --dsn 'postgresql://...' --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE = ROOT / "data" / "db" / "insurance.sqlite3"


@dataclass(frozen=True)
class RuntimeSnapshot:
    users: tuple[dict[str, Any], ...]
    faces: tuple[dict[str, Any], ...]
    gaps: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _read_snapshot(path: Path) -> RuntimeSnapshot:
    if not path.is_file():
        raise FileNotFoundError(f"SQLite source does not exist: {path}")

    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        required = {"users", "face_credentials", "knowledge_gaps", "run_events"}
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = required - tables
        if missing:
            raise RuntimeError(f"SQLite schema is missing tables: {', '.join(sorted(missing))}")

        users = tuple(
            {
                "id": int(row[0]),
                "username": str(row[1]),
                "password_hash": str(row[2]),
                "role": str(row[3]).upper(),
                "created_at": _timestamp(row[4]),
            }
            for row in conn.execute(
                "SELECT id, username, hashed_password, role, created_at "
                "FROM users ORDER BY id"
            )
        )
        for user in users:
            if user["role"] not in {"USER", "ADMIN"}:
                raise RuntimeError(
                    f"Unsupported users.role for legacy id {user['id']}: {user['role']}"
                )

        faces = tuple(
            {
                "id": int(row[0]),
                "user_id": int(row[1]),
                "embedding": bytes(row[2]),
                "created_at": _timestamp(row[3]),
            }
            for row in conn.execute(
                "SELECT id, user_id, embedding, created_at "
                "FROM face_credentials ORDER BY id"
            )
        )
        user_ids = {user["id"] for user in users}
        for face in faces:
            if face["user_id"] not in user_ids:
                raise RuntimeError(f"face_credentials references unknown user: {face['user_id']}")
            if len(face["embedding"]) != 512 * 4:
                raise RuntimeError(
                    f"face embedding has invalid byte length for legacy id {face['id']}: "
                    f"{len(face['embedding'])}"
                )

        gaps = tuple(
            {
                "id": int(row[0]),
                "question": str(row[1]),
                "trace_id": str(row[2] or ""),
                "resolved": bool(row[3]),
                "created_at": _timestamp(row[4]),
            }
            for row in conn.execute(
                "SELECT id, question, trace_id, resolved, created_at "
                "FROM knowledge_gaps ORDER BY id"
            )
        )

        events: list[dict[str, Any]] = []
        for row in conn.execute(
            "SELECT id, trace_id, kind, detail, created_at "
            "FROM run_events ORDER BY id"
        ):
            raw_detail = str(row[3] or "{}")
            try:
                detail = json.loads(raw_detail)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"run_events.detail is not valid JSON for legacy id {row[0]}"
                ) from exc
            if not isinstance(detail, (dict, list, str, int, float, bool)) and detail is not None:
                raise RuntimeError(f"Unsupported JSON detail for legacy id {row[0]}")
            events.append(
                {
                    "id": int(row[0]),
                    "trace_id": str(row[1]),
                    "kind": str(row[2]),
                    "detail": detail,
                    "created_at": _timestamp(row[4]),
                }
            )

    return RuntimeSnapshot(users, faces, gaps, tuple(events))


def _insert_or_verify(cur, table: str, key_column: str, key: Any, values: dict[str, Any]) -> None:
    cur.execute(f"SELECT * FROM {table} WHERE {key_column} = %s", (key,))
    existing = cur.fetchone()
    if existing is not None:
        columns = [description.name for description in cur.description]
        current = dict(zip(columns, existing))
        for column, expected in values.items():
            if column == "id":
                continue
            actual = current.get(column)
            expected = getattr(expected, "obj", expected)
            if isinstance(expected, datetime) and isinstance(actual, datetime):
                if actual != expected:
                    raise RuntimeError(f"Existing {table}.{key_column}={key} differs at {column}")
            elif actual != expected:
                raise RuntimeError(f"Existing {table}.{key_column}={key} differs at {column}")
        return

    columns = list(values)
    placeholders = ", ".join(["%s"] * len(columns))
    cur.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
        tuple(values[column] for column in columns),
    )


def _apply_snapshot(conn, snapshot: RuntimeSnapshot) -> None:
    user_ids: dict[int, str] = {}
    with conn.transaction():
        with conn.cursor() as cur:
            for user in snapshot.users:
                cur.execute(
                    "SELECT id, username, password_hash, role, created_at "
                    "FROM app.user_account WHERE legacy_user_id=%s",
                    (user["id"],),
                )
                existing = cur.fetchone()
                if existing:
                    user_ids[user["id"]] = str(existing[0])
                    expected = (user["username"], user["password_hash"], user["role"])
                    if tuple(existing[1:4]) != expected:
                        raise RuntimeError(
                            f"Existing app.user_account legacy_user_id={user['id']} differs"
                        )
                    continue
                row = cur.execute(
                    "INSERT INTO app.user_account "
                    "(legacy_user_id, username, password_hash, role, created_at) "
                    "VALUES (%s,%s,%s,%s,%s) RETURNING id",
                    (
                        user["id"],
                        user["username"],
                        user["password_hash"],
                        user["role"],
                        user["created_at"],
                    ),
                ).fetchone()
                user_ids[user["id"]] = str(row[0])

            for face in snapshot.faces:
                user_id = user_ids[face["user_id"]]
                cur.execute(
                    "SELECT embedding, created_at FROM app.face_credential WHERE user_id=%s",
                    (user_id,),
                )
                existing = cur.fetchone()
                if existing:
                    if bytes(existing[0]) != face["embedding"]:
                        raise RuntimeError(f"Existing face credential differs for user {user_id}")
                    continue
                cur.execute(
                    "INSERT INTO app.face_credential (user_id, embedding, created_at) "
                    "VALUES (%s,%s,%s)",
                    (user_id, face["embedding"], face["created_at"]),
                )

            for gap in snapshot.gaps:
                _insert_or_verify(cur, "ops.knowledge_gap", "id", gap["id"], gap)

            for event in snapshot.events:
                values = {**event, "detail": Jsonb(event["detail"])}
                _insert_or_verify(cur, "ops.run_event", "id", event["id"], values)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", type=Path, default=DEFAULT_SQLITE)
    parser.add_argument("--dsn", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("--dry-run과 --apply는 함께 사용할 수 없습니다")
    if not args.apply and not args.dry_run:
        parser.error("기본은 안전을 위해 --dry-run 또는 명시적 --apply가 필요합니다")

    snapshot = _read_snapshot(args.sqlite)
    print(
        "source counts: "
        f"users={len(snapshot.users)} faces={len(snapshot.faces)} "
        f"gaps={len(snapshot.gaps)} events={len(snapshot.events)}"
    )
    if args.dry_run:
        print("dry-run: PostgreSQL에 쓰지 않았습니다")
        return 0
    if not args.dsn:
        parser.error("--apply에는 --dsn이 필요합니다")

    import psycopg

    with psycopg.connect(args.dsn) as conn:
        _apply_snapshot(conn, snapshot)
    print("apply: PostgreSQL 이관 완료")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
