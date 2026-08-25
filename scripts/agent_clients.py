"""등록 외부 에이전트 키 관리 CLI.

원문 키는 create/rotate 성공 시 stdout에 한 번만 표시하고 DB·로그에는 저장하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys

from db.postgres.pg_agent_access import PgAgentAccess
from db.postgres.pg_insurance_repository import PgInsuranceAdminRepository
from app.core.config import get_settings
from app.core.domain.agent_access import AGENT_SCOPES, generate_api_key, validate_scopes
from app.core.errors import AppError
from app.core.usecases.sync_agent_clients import SyncAgentClients


def _store() -> PgAgentAccess:
    return PgAgentAccess(get_settings().AGENT_ADMIN_PG_DSN)


def _print_once(client_id: str, raw_key: str) -> None:
    print(json.dumps({"client_id": client_id, "api_key": raw_key}, ensure_ascii=False))
    print("주의: api_key 원문은 다시 조회할 수 없습니다. 지금 안전한 비밀 저장소에 옮기세요.", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="등록 외부 에이전트 클라이언트 관리")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--client-id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--scope", action="append", required=True, choices=sorted(AGENT_SCOPES))
    create.add_argument("--rate-limit", type=int, default=60)

    rotate = sub.add_parser("rotate")
    rotate.add_argument("--client-id", required=True)

    disable = sub.add_parser("disable")
    disable.add_argument("--client-id", required=True)

    sub.add_parser("list")
    sync = sub.add_parser("sync-real")
    sync.add_argument("--apply", action="store_true")
    sync.add_argument("--disable-extras", action="store_true")
    sub.add_parser("prune", help="보존기간이 지난 auth/rate/audit/idempotency 이력 파기")
    args = parser.parse_args(argv)

    try:
        store = _store()
        if args.command == "create":
            scopes = validate_scopes(args.scope)
            key = generate_api_key(args.client_id)
            store.create_client(
                client_id=args.client_id,
                display_name=args.name,
                raw_key=key,
                scopes=scopes,
                rate_limit_per_minute=args.rate_limit,
            )
            _print_once(args.client_id, key)
        elif args.command == "rotate":
            key = generate_api_key(args.client_id)
            store.rotate_client_key(client_id=args.client_id, raw_key=key)
            _print_once(args.client_id, key)
        elif args.command == "disable":
            store.disable_client(client_id=args.client_id)
            print(json.dumps({"client_id": args.client_id, "status": "disabled"}, ensure_ascii=False))
        elif args.command == "list":
            print(json.dumps(store.list_clients(), ensure_ascii=False, indent=2))
        elif args.command == "sync-real":
            report = SyncAgentClients(
                store,
                PgInsuranceAdminRepository(get_settings().INSURANCE_ADMIN_PG_DSN),
            ).run(apply=args.apply, disable_extras=args.disable_extras)
            print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(store.prune_history(), ensure_ascii=False, indent=2))
        return 0
    except (AppError, ValueError) as exc:
        print(f"[agent-clients] 실패: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
