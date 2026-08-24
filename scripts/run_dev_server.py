"""데모/스크린샷용 dev 서버 실행기. SECRET_KEY 등 필수 환경변수를 보장한 뒤 uvicorn을 띄운다.

프로덕션 실행 방식이 아니다 — .env가 없는 로컬 데모 환경에서 화면 캡처를 위해 최소
필요한 환경변수만 기본값으로 채운다(있으면 기존 값 우선, setdefault).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 어느 작업 디렉토리에서 실행되든(예: 워크스페이스 루트 기준 launch.json) 프로젝트 루트를
# sys.path에 넣고 그곳으로 이동한다 — "app.main:app" 임포트가 위치에 무관하게 되도록.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(_PROJECT_ROOT)
sys.path.insert(0, str(_PROJECT_ROOT))

os.environ.setdefault("SECRET_KEY", "demo-only-key-do-not-use-in-prod")

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8080, log_level="warning")
