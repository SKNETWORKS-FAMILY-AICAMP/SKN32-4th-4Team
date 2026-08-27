"""Run the bounded chat load driver without credentials, sockets, or provider calls.

This is coordinator evidence for the agreed Codex substitution.  It exercises the
same 1 -> 2 -> 4 -> 8 scheduler, checkpoints, summaries, and stop gates as the
paid driver while injecting an in-memory HTTP client.  It does not claim to
measure provider latency, billing, token accounting, or provider-side rate limits.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from delivery.bench import paid_chat


class _Response:
    status_code = 200

    def json(self) -> dict[str, Any]:
        return {
            "llm": {
                "provider": "providerless-fake",
                "model": "codex-reviewed-contract",
                "source": "call",
                "used": True,
            },
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "semantic_safety": {"passed": True, "violations": []},
        }


class _Client:
    async def __aenter__(self) -> "_Client":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, *_: object, **__: object) -> _Response:
        # A short await makes concurrency scheduling observable without a socket.
        await asyncio.sleep(0.01)
        return _Response()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Credential-free chat ramp evidence")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--stage-call-cap", type=int, default=20)
    parser.add_argument("--total-call-cap", type=int, default=80)
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    driver_args = argparse.Namespace(
        allow_paid_llm=True,  # Required by the shared driver; the injected client is providerless.
        base_url="http://providerless.invalid",
        message="보험 약관 근거를 인용해 보장 여부를 설명해 주세요.",
        mode="load",
        stage_call_cap=args.stage_call_cap,
        total_call_cap=args.total_call_cap,
        max_seconds=30.0,
        timeout=5.0,
        out=str(args.out),
    )
    result = await paid_chat.run(driver_args, client_factory=_Client)
    result["substitution"] = {
        "name": "codex-review-plus-providerless-load",
        "external_provider_calls": 0,
        "api_keys_read": False,
        "network_transport": "in-memory",
        "measures": [
            "load-driver ramp and concurrency dispatch",
            "checkpoint durability and aggregate summaries",
            "HTTP and semantic stop-gate plumbing",
        ],
        "does_not_measure": [
            "real provider network latency or availability",
            "real provider billing or token accounting",
            "provider-side rate-limit behavior",
        ],
    }
    paid_chat._atomic_json(args.out, result)
    return result


def main() -> int:
    args = _parser().parse_args()
    result = asyncio.run(_run(args))
    print(json.dumps({"summary": result["summary"], "substitution": result["substitution"]},
                     ensure_ascii=False))
    return 2 if result["aborted_reason"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
