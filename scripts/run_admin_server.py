"""운영/관리자 서버(내부 포트 8081) — 관리자 대시보드 + 운영 도구 전체.

실제 프로덕션에서는 이 쪽을 VPN·사내망·IP 화이트리스트 뒤에 두어 공개 인터넷에 노출하지 않는다.
여기서는 별도 포트(8081)로 분리해 그 패턴을 재현한다. 고객 웹은 `run_customer_server.py`(8080).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

os.environ.setdefault("SECRET_KEY", "demo-only-key-do-not-use-in-prod")

import uvicorn  # noqa: E402


#: ★포트를 코드에 박지 않는다 — `--port` 인자 > 환경변수 > 기존 기본값 순으로 읽는다.
#:   같은 기계에서 원본 프로젝트(8080/8081)와 이 사본을 **동시에** 띄워 비교해야
#:   해서 필요했다. 인자·환경변수가 없으면 예전과 똑같이 동작한다.
def _resolve_port(env_name: str, default: int) -> int:
    argv = sys.argv[1:]
    if "--port" in argv:
        i = argv.index("--port")
        if i + 1 >= len(argv):
            raise SystemExit("--port 뒤에 포트 번호가 없습니다.")
        raw = argv[i + 1]
    else:
        raw = os.environ.get(env_name, "")
    if not raw:
        return default
    try:
        port = int(raw)
    except ValueError:
        raise SystemExit(f"포트가 숫자가 아닙니다: {raw!r}") from None
    if not (1 <= port <= 65535):
        raise SystemExit(f"포트 범위를 벗어났습니다: {port}")
    return port


if __name__ == "__main__":
    # 운영 앱 — 내부 포트. 관리자 대시보드 + 운영 도구 전체.
    uvicorn.run("app.main:admin_app", host="127.0.0.1",
                port=_resolve_port("ADMIN_PORT", 8081), log_level="warning")
