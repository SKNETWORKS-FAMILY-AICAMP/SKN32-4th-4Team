"""보험 MCP stdio 서버의 도구 목록과 LLM 용어 설명을 실제 왕복 검증한다."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


async def _run() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        cwd=str(_PROJECT_ROOT),
        env=os.environ.copy(),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("explain_term", {"message": "통원 뜻"})
            body = json.loads(result.content[0].text)
            runtime = await session.read_resource("insurance://runtime-config")
            runtime_body = json.loads(runtime.contents[0].text)
            output = {
                "tools": sorted(tool.name for tool in tools.tools),
                "provider": runtime_body.get("llm_provider"),
                "explain_term": {
                    "is_error": bool(result.isError),
                    "intent": body.get("intent"),
                    "found": body.get("found"),
                    "llm": body.get("llm"),
                    "quotes": len(body.get("quotes", [])),
                    "message_nonempty": bool(body.get("message")),
                },
            }
            # Windows CP949에서도 결과 확인이 끊기지 않도록 ASCII-safe JSON으로 출력한다.
            print(json.dumps(output, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(_run())
