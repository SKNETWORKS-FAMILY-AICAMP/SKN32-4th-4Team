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

if __name__ == "__main__":
    # 운영 앱 — 내부 포트. 관리자 대시보드 + 운영 도구 전체.
    uvicorn.run("app.main:admin_app", host="127.0.0.1", port=8081, log_level="warning")
