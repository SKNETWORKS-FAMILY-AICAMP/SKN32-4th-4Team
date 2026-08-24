"""테스트 공통 픽스처.

임시 SQLite DB + 테스트용 SECRET_KEY를 앱 import 전에 환경변수로 설정해 실 DB를
건드리지 않고 격리한다.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import uuid

# --- 앱 import 이전에 환경 설정 (엔진이 이 값으로 바인딩됨) ---
_TMP_DB = os.path.join(tempfile.gettempdir(), f"insurance_test_{uuid.uuid4().hex}.sqlite3")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["SECRET_KEY"] = "test-secret-key-do-not-use-in-prod"
os.environ["LLM_PROVIDER"] = "local"
# 기본 테스트는 실제 네트워크·모델을 호출하지 않는다. LLM 경로는 전용 Fake 테스트에서 켠다.
os.environ["LLM_CHAT_ENABLED"] = "false"
os.environ["DEMO_STORE_BACKEND"] = "file"
# 등록 에이전트 테스트의 HMAC 전용 고정값. 실제 키가 아니며 테스트 프로세스 밖에 쓰지 않는다.
os.environ["AGENT_HASH_SECRET"] = "test-only-agent-hash-secret-32-characters"
os.environ["AGENT_API_ENABLED"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

# --- ★판정 모드를 **운영 파일에서 떼어낸다** -------------------------------
#
#   `config/precheck_mode.json` 은 관리자가 대시보드에서 토글하는 **운영 상태**다.
#   그런데 판정 목록(`load_versions`)이 이 파일을 읽으므로, 개발자가 화면에서
#   엄격 모드로 바꿔 두면 **테스트가 깨진다.**
#
#   실제로 그랬다(2026-08-04): `demo_admin` 이 대시보드에서 엄격으로 바꿔 두자
#   `test_확정이_부분이면_판정_응답이_그_사실을_말한다` 가 지원 보험사 0곳으로 실패했다.
#   **테스트가 사람의 화면 조작에 좌우되면 그건 테스트가 아니다.**
#
#   존재하지 않는 임시 경로를 가리키면 `identification_mode.current()` 가
#   기본값(자동승인)을 돌려준다 — 결정론적이고 운영 파일을 건드리지 않는다.
#   모드 자체를 시험하는 곳은 이 값을 각자 monkeypatch 한다.
from app.core.domain import identification_mode as _mode  # noqa: E402

_mode._MODE_FILE = pathlib.Path(tempfile.gettempdir()) / f"precheck_mode_{uuid.uuid4().hex}.json"

from app.db.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _prepare_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    #: ★커머스 상품을 시딩하지 않는다.
    #:   시딩 CSV 는 `legacy/` 로 옮겼고, **현행 코드가 레거시를 참조하면
    #:   레거시를 지울 수 없게 된다.** 지금 남은 테스트는 상품이 필요 없다.
    #:   보험 픽스처가 필요해지면 `tests/fixtures/` 에 따로 만든다.
    yield
    try:
        os.remove(_TMP_DB)
    except OSError:
        pass


@pytest.fixture
def client() -> TestClient:
    # lifespan을 다시 돌리지 않도록 이미 준비된 앱에 TestClient만 붙인다
    return TestClient(app)


@pytest.fixture
def unique_user():
    def _make():
        return f"user_{uuid.uuid4().hex[:8]}", "pass1234"

    return _make


def auth_header(client: TestClient, username: str, password: str) -> dict:
    client.post("/auth/signup", json={"username": username, "password": password})
    resp = client.post("/auth/login", data={"username": username, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
