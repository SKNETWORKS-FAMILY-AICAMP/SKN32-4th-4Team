"""테스트 공통 픽스처.

임시 Postgres 스키마 + 테스트용 SECRET_KEY를 앱 import 전에 환경변수로 설정해 실 DB를
건드리지 않고 격리한다.

참고 — 2026-08-14. 원래는 임시 SQLite 파일로 격리했다. 프로젝트가 전량 PostgreSQL 로
전환되면서 conda 의 `sqlite3.dll` 이 없어져 `import sqlite3` 자체가 실패했다
(docs/reports/debugs/2026-08-14_1130_conda_sqlite3_dll_소실로_전체시험_중단.md).
원인은 DLL 이 아니라 이 하네스가 여전히 sqlite 를 켜고 있던 것이었다.
`db/sqlite_legacy/connection.py` 의 `create_engine` 은 `DATABASE_URL` 만 보고 방언을
고른다 — sqlite 전용이 아니다. 그래서 이 파일만 postgres DSN 을 넘기면 된다.
같은 사설 postgres(127.0.0.1:5433, `PGVECTOR_DSN`·`DEMO_PG_DSN`과 같은 인스턴스)
안에 **전용 테스트 DB**(`insurance_pytest`)를 두고, 프로세스마다 새 스키마를 만들어
SQLite 임시 파일이 주던 것과 같은 격리(매 실행마다 깨끗한 상태 · 실 DB 무관)를 지킨다.
`insurance_real`·`insurance_demo`·`insurance_agent`·`acop*` 등 기존 DB는 손대지 않는다.
참고 — 격리 단위는 **프로세스**다(옛 sqlite 파일도 그랬다). 같은 프로세스 안 여러
테스트 함수는 세션 스코프 스키마를 공유한다 — 테스트 함수 간 격리를 주장하지 않는다.

★코덱스 교차검증(2026-08-14)이 반례 둘을 찾았다 — 「내가 맞다고 안심시키지 마라」고
  시켰더니 실제로 걸렸다.
  1. `_ensure_test_database()` 의 SELECT→CREATE 는 원자적이지 않다. 두 pytest
     프로세스가 동시에 시작하면 둘 다 "없다"를 보고 둘 다 CREATE 를 시도해
     뒤쪽이 `DuplicateDatabase` 로 죽는다 — `except` 로 잡는다(경쟁에서 진 게 아니라
     상대가 이미 만들어 준 것이니 정상이다).
  2. 스키마는 **import 시점**에 만들고 정리는 `_prepare_db` 픽스처의 `yield` 뒤에만
     한다. `pytest --collect-only` 는 픽스처를 아예 실행하지 않고, import 자체가
     실패해도 픽스처까지 못 간다 — 그러면 스키마가 영영 안 지워진다.
     `tests/test_requirements_matrix.py` 가 내부에서 자식 `pytest --collect-only`
     프로세스를 반복 실행하므로, **정상적인 시험 실행마다** 스키마가 하나씩 샌다.
     `atexit` 를 추가로 등록해 이 경로들도 커버한다(정상 종료의 즉시 정리는
     픽스처가 그대로 맡는다 — 중복 호출은 `DROP SCHEMA IF EXISTS` 라 안전하다).

  코덱스가 남긴, 아직 반영하지 않은 지적(범위 밖으로 남긴다) —
    · `AUTH_PERSISTENCE`·`INSURANCE_PG_DSN` 등 별도 DSN·persistence 스위치는
      이 conftest 가 강제하지 않는다. 개발자 셸에 `insurance_real` 을 가리키는
      값이 남아 있으면 그쪽으로 샐 수 있다 — **이건 이 변경이 만든 구멍이 아니라
      옛 sqlite 버전에도 있던 구멍**이라 이번 범위에는 안 넣는다.
    · `postgres` superuser 로 접속한다 — 전용 role·최소권한으로 좁히지 않았다.
    · `app/obs/readiness.py` 의 최상단 `import sqlite3` 는 이 conftest 와 무관하게
      독립적으로 실패한다(지연 import 로 고쳐야 한다) — 이 파일은 다른 세션이
      작업 중이라 손대지 않는다.
"""

from __future__ import annotations

import atexit
import os
import pathlib
import tempfile
import uuid

import psycopg
import psycopg.errors

# --- 앱 import 이전에 환경 설정 (엔진이 이 값으로 바인딩됨) ---
_PG_HOST = "127.0.0.1"
_PG_PORT = 5433
_PG_ADMIN_USER = "postgres"
_TEST_DB_NAME = "insurance_pytest"
_TEST_SCHEMA = f"pytest_{uuid.uuid4().hex[:12]}"


#: ★PG 가 없을 때 **무엇을 해야 하는지** 알려 주는 메시지.
#:
#:   무폴백은 지키되(조용히 sqlite 로 안 돌아간다), 원인과 조치는 말해야 한다.
#:   고치기 전에는 `psycopg.errors.ConnectionTimeout` 이 conftest import 실패 안에
#:   그대로 튀어나와, 처음 보는 사람은 **테스트가 깨진 줄 안다**(`CLAUDE.md` §3 —
#:   오류 메시지가 사실을 잘못 전하지 않게 한다).
_PG_DOWN_HINT = f"""
시험 하네스가 PostgreSQL({_PG_HOST}:{_PG_PORT})에 붙지 못했습니다.

  이건 테스트 결함이 아니라 **전제 조건 미충족**입니다.
  기본 CI 도 PostgreSQL 을 씁니다 — 커밋 9cd48a3f 에서 하네스를 postgres 로 옮겼고,
  조용히 sqlite 로 되돌아가지 않습니다(CLAUDE.md §0 폴백 금지).

  먼저 이것을 하세요:
      python -m scripts.pg start
      python -m scripts.pg status      # "연결 OK" 를 확인하고 나서 pytest

  근거 문서: docs/submission/04_테스트_계획_및_결과.md §3
"""


def _ensure_test_database() -> None:
    #: 위험 — CREATE DATABASE 는 트랜잭션 안에서 못 돈다. autocommit 필수.
    try:
        admin = psycopg.connect(
            f"host={_PG_HOST} port={_PG_PORT} user={_PG_ADMIN_USER} dbname=postgres",
            connect_timeout=5,
            autocommit=True,
        )
    except psycopg.OperationalError as exc:
        #: ★삼키지 않는다 — 원래 예외를 원인으로 달고 죽는다. 다만 조치를 함께 적는다.
        raise RuntimeError(
            _PG_DOWN_HINT + f"  원래 오류: {type(exc).__name__}: {exc}"
        ) from exc
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (_TEST_DB_NAME,))
            if not cur.fetchone():
                try:
                    cur.execute(f'CREATE DATABASE "{_TEST_DB_NAME}"')
                except psycopg.errors.DuplicateDatabase:
                    #: 참고 — 코덱스 지적. SELECT→CREATE 사이 경쟁 — 동시에 시작한
                    #:   다른 pytest 프로세스가 먼저 만들었다는 뜻이다. 정상.
                    pass
    finally:
        admin.close()


def _create_test_schema() -> None:
    conn = psycopg.connect(
        f"host={_PG_HOST} port={_PG_PORT} user={_PG_ADMIN_USER} dbname={_TEST_DB_NAME}",
        connect_timeout=5,
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{_TEST_SCHEMA}"')
    finally:
        conn.close()


def _drop_test_schema() -> None:
    conn = psycopg.connect(
        f"host={_PG_HOST} port={_PG_PORT} user={_PG_ADMIN_USER} dbname={_TEST_DB_NAME}",
        connect_timeout=5,
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{_TEST_SCHEMA}" CASCADE')
    finally:
        conn.close()


#: 참고 — 실패하면 조용히 sqlite 로 되돌아가지 않는다(CLAUDE.md §0 폴백 금지).
#:   postgres 가 없으면 시험이 명시적으로 죽는 편이, 격리 안 된 DB로 도는 것보다 낫다.
_ensure_test_database()
_create_test_schema()
#: 참고 — 코덱스 지적. `--collect-only`·import 실패는 아래 `_prepare_db` 픽스처까지
#:   못 가서 그 쪽의 `_drop_test_schema()` 가 안 불린다. atexit 은 그런 경로에서도
#:   인터프리터 정상 종료 시 한 번은 불린다 — 픽스처 쪽 즉시 정리와 중복돼도
#:   `DROP SCHEMA IF EXISTS` 라 안전하다.
atexit.register(_drop_test_schema)

os.environ["DATABASE_URL"] = (
    f"postgresql+psycopg://{_PG_ADMIN_USER}@{_PG_HOST}:{_PG_PORT}/{_TEST_DB_NAME}"
    f"?options=-csearch_path%3D{_TEST_SCHEMA}"
)
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

_mode._MODE_FILE = (
    pathlib.Path(tempfile.gettempdir()) / f"precheck_mode_{uuid.uuid4().hex}.json"
)

from db.sqlite_legacy.connection import Base, engine  # noqa: E402
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
    _drop_test_schema()


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


# ── ★실모델 적재 가드 ───────────────────────────────────────────────────────
#
#   시험은 무게추를 내려받으면 안 된다. 그런데 문장으로만 적혀 있었고,
#   `tests/test_clause_search_route.py` 의 시험 하나가 `_fake_worker()` 를
#   빠뜨려 **`Qwen3-Reranker-4B`(7.5GB)를 실제로 적재**했다(2026-08-26).
#
#   ★그 시험 자체는 **통과했다.** 워커가 아직 안 떠서 503 을 받았기 때문이다.
#     적재는 데몬 스레드에서 계속 돌다가 **한참 뒤 다른 시험 도중**
#     `access violation` 으로 pytest 프로세스 전체를 죽였다
#     (전량 실행이 54~67% 에서 segfault · 재현 3/3).
#     증상과 원인이 멀어서 원인 규명에 여러 번 헛짚었다.
#
#   → 규칙을 **구조로 만든다.** `ml`·`llm` 표시가 없는 시험에서 실모델 생성자가
#     불리면 **그 자리에서 죽는다.** 조용히 통과한 뒤 나중에 프로세스를 죽이는 것보다
#     낫다(`CLAUDE.md` §0 — 신호 없는 자동 복구는 안전장치가 아니다).
#
#   ★가짜를 주입한 시험은 생성자를 아예 안 부르므로 이 가드에 걸리지 않는다.

_REAL_MODEL_ALLOWED = False

#: ★가드가 **데몬 스레드에서** 물릴 수 있다.
#:
#:   실제 사고가 그랬다 — 적재는 `rerank_worker` 의 워커 스레드에서 일어났다.
#:   거기서 예외를 올리면 워커의 `except Exception` 이 받아 `load_error` 에 적고 끝난다.
#:   **적재는 막히지만 시험은 그대로 통과한다.**
#:   그래서 위반을 여기 모아 두고 픽스처 정리에서 **시험을 실패시킨다.**
#:   (스레드 타이밍상 다음 시험에서 잡힐 수 있다. 그래도 조용히 지나가지는 않는다.)
_REAL_MODEL_VIOLATIONS: list[str] = []
NL = chr(10)   #: 역슬래시 이스케이프를 피한다(편집 도구에서 깨진 적이 있다)


class RealModelLoadInTest(RuntimeError):
    """시험이 실제 모델 무게추를 적재하려 했다."""


def _guard_real_model(kind: str, name: object) -> None:
    if _REAL_MODEL_ALLOWED:
        return
    _REAL_MODEL_VIOLATIONS.append(f"{kind}({name!r})")
    raise RealModelLoadInTest(
        f"\n시험이 실제 모델을 적재하려 했습니다: {kind}({name!r})\n"
        "\n"
        "  기본 CI 는 무게추를 내려받지 않습니다. 둘 중 하나를 하세요:\n"
        "    · 가짜를 주입하세요 — 예: 리랭커 경로는 `_fake_worker()`,\n"
        "      임베더는 `ClauseQueryEmbedder(profile, model=_FakeST())`\n"
        "    · 정말 실모델이 필요하면 `@pytest.mark.ml` 을 붙이세요(기본 CI 에서 제외됩니다)\n"
        "\n"
        "  ★이 가드가 없던 때 무슨 일이 났는지: 시험은 통과하고, 적재는 데몬 스레드에서\n"
        "    계속 돌다가 한참 뒤 **다른 시험 도중** 프로세스를 죽였습니다.\n"
        "    근거: docs/reports/debugs/2026-08-26_기본CI_segfault_원인규명_한번_틀렸다.md\n"
    )


def _install_real_model_guard() -> None:
    #: 세션 전체에 한 번만 건다. 데몬 스레드가 뒤늦게 적재해도 잡히도록
    #: 시험마다 끼웠다 빼지 않는다 — 허용 여부는 `_REAL_MODEL_ALLOWED` 로만 바꾼다.
    try:
        import sentence_transformers as _st
    except Exception:  # noqa: BLE001 — 없으면 가드도 필요 없다
        return
    for _cls_name in ("SentenceTransformer", "CrossEncoder"):
        _cls = getattr(_st, _cls_name, None)
        if _cls is None or getattr(_cls.__init__, "_real_model_guarded", False):
            continue
        _orig = _cls.__init__

        def _make(orig, kind):
            def _patched(self, *a, **k):
                _guard_real_model(kind, a[0] if a else k.get("model_name_or_path"))
                return orig(self, *a, **k)
            _patched._real_model_guarded = True
            return _patched

        _cls.__init__ = _make(_orig, _cls_name)


_install_real_model_guard()


@pytest.fixture(autouse=True)
def _real_model_guard(request):
    """`ml`·`llm` 표시가 있는 시험에서만 실모델 적재를 허용한다."""
    global _REAL_MODEL_ALLOWED
    allowed = any(request.node.get_closest_marker(m) for m in ("ml", "llm"))
    previous, _REAL_MODEL_ALLOWED = _REAL_MODEL_ALLOWED, allowed
    try:
        yield
    finally:
        _REAL_MODEL_ALLOWED = previous
    #: ★데몬 스레드에서 물린 위반도 여기서 드러난다. 비우고 실패시킨다 —
    #:   그대로 두면 뒤따르는 모든 시험이 같은 이유로 실패해 원인이 가려진다.
    if _REAL_MODEL_VIOLATIONS and not allowed:
        seen, _REAL_MODEL_VIOLATIONS[:] = list(_REAL_MODEL_VIOLATIONS), []
        raise RealModelLoadInTest(
            "실제 모델 적재 시도가 있었습니다: " + repr(seen) + NL
            + "  ★백그라운드 스레드에서 났다면 **이 시험이 아니라 앞선 시험**이 "
              "시작했을 수 있습니다." + NL
            + "  근거: docs/reports/debugs/"
              "2026-08-26_기본CI_segfault_원인규명_한번_틀렸다.md" + NL
        )


# --- ★`get_settings` 를 **import 시점에 묶은** 모듈이 패치를 물고 늘어지는 것 ------
#
#   `monkeypatch.setattr("app.core.config.get_settings", lambda: ...)` 로 갈아끼운
#   **그 사이에 어떤 모듈이 처음 임포트되면**, 그 모듈의
#   `from app.core.config import get_settings` 가 **람다를 영구히** 붙잡는다.
#   monkeypatch 는 `app.core.config` 쪽 이름만 되돌리므로 그 참조는 안 풀린다.
#
#   실제로 그랬다(2026-08-26): `db.postgres.pgvector_index` 가
#   `test_clause_search_route.py::_admin_app` 의 람다를 물어
#   `test_pgvector.py` 와 `test_pgvector_schema_pin.py` 가 **전량 실행에서만** 깨졌다.
#   두 시험 다 단독으론 통과해서 파일 단위로 돌려 보면 안 잡힌다.
#
#   ★그래서 **터진 자리가 아니라 흘린 자리에서** 잡는다.
_SETTINGS_SCAN_PREFIXES = ("app.", "db.", "scripts.")


def _leaked_settings_refs() -> list[str]:
    import sys as _sys

    from app.core import config as _cfg

    canonical = _cfg.get_settings
    bad = []
    for name, mod in list(_sys.modules.items()):
        if not name.startswith(_SETTINGS_SCAN_PREFIXES) or mod is None:
            continue
        fn = getattr(mod, "get_settings", None)
        if fn is None or fn is canonical:
            continue
        #: ★모듈이 **자기 것**을 정의한 경우는 정상이다(`pgvector_index` 의 지연 조회 래퍼).
        #:   `app.core.config` 에서 그대로 가져다 쓴 것도 정상 — 그건 `canonical` 과 같다.
        #:   남는 것은 **바깥에서 들어온 남의 함수**뿐이고, 그게 새어 들어온 패치다.
        if getattr(fn, "__module__", None) == name:
            continue
        bad.append(f"{name}.get_settings -> {fn!r}")
    return bad


@pytest.fixture(autouse=True)
def _settings_binding_guard():
    """시험이 끝난 뒤 `get_settings` 참조가 새어 있으면 **그 자리에서** 실패시킨다."""
    yield
    leaked = _leaked_settings_refs()
    if leaked:
        raise RuntimeError(
            "`get_settings` 참조가 이 시험 뒤에 남았습니다:" + NL
            + NL.join("  " + x for x in leaked) + NL
            + "  ★원인: 그 모듈이 `from app.core.config import get_settings` 로 "
              "**import 시점에 묶는데**, 패치가 걸린 사이에 처음 임포트됐습니다." + NL
            + "  ★고치는 법: 그 모듈이 **부를 때** 찾게 바꾼다 — "
              "`from app.core import config` 뒤 `config.get_settings()`." + NL
            + "  본보기: db/postgres/pgvector_index.py 의 `get_settings()` 주석" + NL
        )
