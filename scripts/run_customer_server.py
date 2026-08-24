"""고객 웹 서버(공개 포트 8080) — 관리자 API·운영 페이지 없음.

실제 프로덕션 분리 패턴의 축소판: 이 프로세스에는 관리자 라우터가 실리지 않아 `/api/admin/*`이
물리적으로 404이고, 운영/개발 정적 페이지(admin/facebench/mcp/rag/orders)도 404다. 공개 인터넷에
노출되는 쪽이라고 가정한다. 운영 도구는 `run_admin_server.py`(내부 포트 8081)로 띄운다.
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

if __name__ == "__main__":
    # 고객 앱 — 공개 포트. 관리자 API/운영 페이지 미포함.
    uvicorn.run("app.main:customer_app", host="127.0.0.1", port=8080, log_level="warning")
