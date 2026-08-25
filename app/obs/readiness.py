"""Readiness — 기동/데이터 분리(REQ-OPS-01, TEST-OPS-READY-001).

앱 기동 시 자동으로 테이블 생성·인덱스 빌드를 하지 않는다(v3.2 ADR). 이 readiness는
승인 릴리스·활성 clause store·candidate fact 무결성·선택된 persistence가 실제로 준비됐는지
보고하고, 미준비면 조용히 진행하지 않고 명시적으로 알린다(무폴백).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import get_settings

# migration으로 생성돼야 하는 핵심 테이블(존재로 준비 여부 판정)
#: ★보험 서비스가 **쇼핑몰 테이블 때문에 "준비 안 됨"** 이 되고 있었다.
#:   (products · orders 는 커머스 실습 테이블이다 — legacy 로 옮겼다)
#:   지금 판정은 파일을 읽으므로 필수 테이블이 없다. DB 적재 후 다시 채운다.
_REQUIRED_TABLES: tuple[str, ...] = ()
_SQLITE_AUTH_TABLES = ("users", "face_credentials")
_SQLITE_OPS_TABLES = ("run_events", "knowledge_gaps")


def _required_sqlite_tables(settings) -> tuple[str, ...]:
    """Return tables required by the active SQLite-backed application paths."""
    required = (
        set(_REQUIRED_TABLES)
        if getattr(settings, "SQLITE_LEGACY_ENABLED", True)
        else set()
    )
    if getattr(settings, "AUTH_PERSISTENCE", "sqlite") == "sqlite":
        required.update(_SQLITE_AUTH_TABLES)
    if getattr(settings, "OPS_PERSISTENCE", "sqlite") == "sqlite":
        required.update(_SQLITE_OPS_TABLES)
    return tuple(sorted(required))


def _existing_sqlite_tables(settings, required: tuple[str, ...]) -> set[str]:
    """Inspect an active legacy DB without creating a missing SQLite file."""
    if not required or not getattr(settings, "SQLITE_LEGACY_ENABLED", True):
        return set()

    try:
        url = make_url(str(settings.DATABASE_URL))
    except Exception:  # noqa: BLE001 - invalid configuration is reported as missing
        return set()
    if url.get_backend_name() != "sqlite":
        return set()

    database = url.database
    if database in (None, "", ":memory:"):
        return set()

    path = Path(database)
    if not path.is_file():
        return set()
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=1) as conn:
            return {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
    except sqlite3.Error:
        # A concurrent quarantine/removal is simply not ready. mode=ro ensures
        # the readiness probe cannot recreate the file while reporting it.
        return set()


def _pg_readiness_failure(
    backend: str, *, required: bool, configured: bool, exc: Exception
) -> dict[str, object]:
    """Return public-safe failure data without echoing a DSN or credentials."""
    return {
        "backend": backend,
        "configured": configured,
        "required": required,
        "ready": False,
        "reason": f"{backend} readiness check failed ({type(exc).__name__})",
    }


def _accepted_release_state(clause_store: str) -> dict[str, object]:
    """승인 포인터와 활성 clause store가 요구하는 릴리스 산출물을 검증한다."""
    try:
        from app.core import release
        from app.core.errors import ConfigError

        rel = release.current()
        # tag 형식도 여기서 검증한다. 단순히 JSON을 읽었다고 승인 릴리스가 준비된 것은 아니다.
        rel.index_generation
        if clause_store == "file":
            rel.ensure_ready()
        elif clause_store == "pg":
            if not rel.embed_profile.is_set:
                raise ConfigError("PG clause store에 필요한 승인 임베딩 프로필이 없습니다.")
        else:
            raise ConfigError(f"지원하지 않는 CLAUSE_STORE입니다: {clause_store!r}")
        return {
            "backend": clause_store,
            "required": True,
            "ready": True,
            "release_id": rel.release_id,
            "clause_tag": rel.clause_tag,
            "document_count": rel.document_count,
        }
    except Exception as exc:  # noqa: BLE001 - readiness must report, not crash
        return {
            "backend": clause_store,
            "required": True,
            "ready": False,
            "reason": f"accepted release readiness check failed ({type(exc).__name__})",
        }


def check_readiness() -> dict[str, object]:
    """보험 판정 릴리스와 선택된 저장소의 준비 상태를 보고한다(실 모델 호출 없음)."""
    settings = get_settings()
    sqlite_enabled = getattr(settings, "SQLITE_LEGACY_ENABLED", True)
    required_sqlite_tables = _required_sqlite_tables(settings)
    existing = _existing_sqlite_tables(settings, required_sqlite_tables)
    missing_tables = [t for t in required_sqlite_tables if t not in existing]
    sqlite_configuration_error = not sqlite_enabled and bool(required_sqlite_tables)
    db_ready = not missing_tables and not sqlite_configuration_error
    if sqlite_configuration_error:
        readiness_hint = (
            "SQLite 저장 경로가 선택되어 있습니다. SQLite를 사용하려면 "
            "`SQLITE_LEGACY_ENABLED=true`로 설정하세요."
        )
    elif not db_ready:
        readiness_hint = "선택된 SQLite 저장소에 필요한 스키마가 없습니다."
    else:
        readiness_hint = None
    out: dict[str, object] = {
        "ready": db_ready,
        "db_tables_ready": db_ready,
        "missing_tables": missing_tables,
        "hint": readiness_hint,
    }

    def fail(hint: str) -> None:
        out["ready"] = False
        if out.get("hint") is None:
            out["hint"] = hint

    from app.composition import _clause_store_kind

    clause_store = _clause_store_kind()
    accepted_release = _accepted_release_state(clause_store)
    out["accepted_release"] = accepted_release
    if not accepted_release.get("ready"):
        fail("승인된 보험 약관 릴리스의 무결성을 확인하세요.")

    if clause_store == "pg" and accepted_release.get("ready"):
        clause = _clause_index_state()
        clause.setdefault("ready", False)
        clause.setdefault("required", True)
    elif clause_store == "file":
        clause = {
            "backend": clause_store,
            "checked": True,
            "required": True,
            "ready": bool(accepted_release.get("ready")),
        }
    else:
        clause = {
            "backend": clause_store,
            "checked": True,
            "required": True,
            "ready": False,
            "reason": "unsupported clause store",
        }
    out["clause_index"] = clause
    out["clause_index_ready"] = bool(clause.get("ready"))
    if not clause.get("ready"):
        fail(clause.get("hint") or "활성 clause store가 승인 릴리스와 맞지 않습니다.")

    #: ★리랭크 워커 상태를 **밖에서 보이게** 한다.
    #:   적재 실패·시한초과·버려진 일감이 안 보이면 「느리다」로만 읽힌다.
    #:   ★준비 조건에는 넣지 않는다 — 리랭킹은 꺼져 있는 것이 기본이고,
    #:     꺼진 상태를 `ready:false` 로 만들면 늘 미준비라 아무도 안 본다.
    out["clause_rerank"] = _clause_rerank_state()

    from app.core.candidate_fact_registry import check_candidate_fact_sources

    candidates = check_candidate_fact_sources()
    candidates.setdefault("required", True)
    out["candidate_fact_sources"] = candidates
    if not candidates.get("ready"):
        fail("candidate fact 산출물 무결성 검증에 실패했습니다.")
    #: ★**지금 쓰는 저장소가 요구하는 것만** 준비 조건에 넣는다.
    #:
    #:   `CLAUSE_STORE=file` 이면 인덱스 A 가 비어도 판정은 돈다 —
    #:   그때 `ready:false` 로 만들면 늘 미준비라 아무도 안 본다.
    #:   반대로 `pg` 인데 색인이 어긋나면 **검색이 전부 실패**하므로
    #:   `ready:true` 라고 말하면 거짓이다.
    #:   실측 2026-08-03: 하위는 false 인데 상위가 true 였다.
    from app.adapters.demo_submission_store import backend_name as demo_backend

    if demo_backend() == "postgres":
        try:
            from db.postgres.pg_demo_submission_store import readiness as demo_readiness

            demo = demo_readiness()
        except Exception as exc:  # noqa: BLE001 - readiness must report, not crash
            demo = _pg_readiness_failure(
                "postgres-demo", required=True,
                configured=bool(getattr(settings, "DEMO_PG_DSN", "").strip()),
                exc=exc,
            )
    else:
        demo = {"backend": "file", "required": True, "ready": True}
    demo.setdefault("required", True)
    out["demo_store"] = demo
    if not demo.get("ready"):
        fail(
            "합성 PostgreSQL 저장소가 준비되지 않았습니다. "
            "insurance_demo DB에 demo migration을 적용하세요."
        )

    insurance_pg_required = any(
        (
            getattr(settings, "AUTH_PERSISTENCE", "sqlite") == "postgres",
            getattr(settings, "PRECHECK_PERSISTENCE", "off") == "postgres",
            getattr(settings, "OUTCOME_PERSISTENCE", "file") == "postgres",
            #: ★코호트 조회(app/adapters/cohort_stats.py)도 같은 PgInsuranceRepository
            #:   DSN을 쓴다(db/postgres/pg_insurance_cohort_stats.py). 빠지면 이것만
            #:   postgres인 배포가 "준비됨"으로 잘못 보고된다(코덱스 리뷰 지적).
            getattr(settings, "VERIFIED_COHORT_STORE", "file") == "postgres",
        )
    )
    if insurance_pg_required:
        from db.postgres.pg_insurance_repository import PgInsuranceRepository

        try:
            insurance_pg = PgInsuranceRepository.from_settings().readiness()
        except Exception as exc:  # noqa: BLE001 - readiness must report, not crash
            insurance_pg = _pg_readiness_failure(
                "postgres",
                required=True,
                configured=bool(getattr(settings, "INSURANCE_PG_DSN", "").strip()),
                exc=exc,
            )
        insurance_pg.setdefault("required", True)
        out["insurance_postgres"] = insurance_pg
        if not insurance_pg.get("ready"):
            fail("insurance PostgreSQL schema/migration readiness를 확인하세요.")
    else:
        out["insurance_postgres"] = {"configured": False, "required": False}

    if getattr(settings, "AGENT_API_ENABLED", False):
        from db.postgres.pg_agent_access import PgAgentAccess

        try:
            agent_pg = PgAgentAccess(settings.AGENT_PG_DSN).readiness()
        except Exception as exc:  # noqa: BLE001 - readiness must report, not crash
            agent_pg = _pg_readiness_failure(
                "postgres-agent",
                required=True,
                configured=bool(getattr(settings, "AGENT_PG_DSN", "").strip()),
                exc=exc,
            )
        agent_pg.setdefault("required", True)
        out["agent_postgres"] = agent_pg
        if not agent_pg.get("ready"):
            fail("registered-agent PostgreSQL schema/readiness를 확인하세요.")
    else:
        out["agent_postgres"] = {"configured": False, "required": False}

    if getattr(settings, "OPS_PERSISTENCE", "sqlite") == "postgres":
        from db.postgres.ops_repository import PgOpsStore

        try:
            ops_pg = PgOpsStore.from_settings().readiness()
        except Exception as exc:  # noqa: BLE001 - readiness must report, not crash
            ops_pg = _pg_readiness_failure(
                "postgres-ops",
                required=True,
                configured=bool(getattr(settings, "INSURANCE_PG_DSN", "").strip()),
                exc=exc,
            )
        ops_pg.setdefault("required", True)
        out["ops_postgres"] = ops_pg
        if not ops_pg.get("ready"):
            fail("runtime ops PostgreSQL schema/readiness를 확인하세요.")
    else:
        out["ops_postgres"] = {"configured": False, "required": False}
    return out


def public_readiness() -> dict[str, object]:
    """Expose only boolean component health on the unauthenticated endpoint."""
    status = check_readiness()

    def component(name: str) -> bool | None:
        value = status.get(name)
        if not isinstance(value, dict) or value.get("required") is False:
            return None
        ready = value.get("ready")
        return ready if isinstance(ready, bool) else False

    return {
        "ready": bool(status.get("ready")),
        "db_tables_ready": bool(status.get("db_tables_ready")),
        "clause_index_ready": bool(status.get("clause_index_ready")),
        "components": {
            "accepted_release": component("accepted_release"),
            "clause_index": component("clause_index"),
            "candidate_fact_sources": component("candidate_fact_sources"),
            "demo_store": component("demo_store"),
            "insurance_postgres": component("insurance_postgres"),
            "agent_postgres": component("agent_postgres"),
            "ops_postgres": component("ops_postgres"),
        },
    }


def _clause_index_state() -> dict[str, object]:
    """인덱스 A 가 **승인 릴리스와 맞나.**

    ★왜 여기서도 보나 — 검색 경로가 막아 주기는 하지만, 그건 **요청이 와야**
      드러난다. 실측 2026-08-03 에 승인 세대 's5' 로 적재된 행이 0건인 채
      한참 있었는데 아무도 몰랐다. 준비 상태는 **묻기 전에** 말해야 한다.

    ★PG 가 없거나 못 붙어도 여기서 죽지 않는다 — 이 함수는 **보고**다.
      다만 "확인 못 함"과 "준비됨"을 **구분해서** 적는다. 섞으면 폴백이다.
    """
    #: ★★**절대 매달리지 않는다.** 준비 상태는 **보고**이지 작업이 아니다.
    #:
    #:   실측 2026-08-03 — 이 함수가 연 연결이 `idle in transaction` 으로
    #:   **3시간 9분** 남아 `policy_clause_chunk` 에 읽기 락을 쥐고 있었다.
    #:   그 뒤로 병행 트랙의 `ALTER TABLE ... ADD COLUMN` 이 막히고,
    #:   그 뒤로 다시 이 함수의 조회 **12개**가 줄줄이 밀렸다.
    #:   그 상태로 테스트를 돌리니 **64% 에서 15분 넘게 멈췄다.**
    #:
    #:   세 가지가 겹쳤다 —
    #:     ① 읽고 나서 트랜잭션을 **안 닫았다**(SELECT 도 트랜잭션을 연다)
    #:     ② 시간 제한이 **없었다** — 락을 만나면 영원히 기다린다
    #:     ③ `pg` 마커가 없는 테스트 경로에서 **PG 를 요구했다**
    try:
        from db.postgres import pgvector_clause_index as ix
        from db.postgres.pgvector_index import get_conn

        conn = get_conn()
        try:
            #: ★락을 만나면 **기다리지 않고 실패한다.** 보고하려다 남을 막으면 안 된다.
            with conn.cursor() as cur:
                cur.execute("SET LOCAL lock_timeout = '2s'")
                cur.execute("SET LOCAL statement_timeout = '5s'")
            st = ix.index_state(conn)
        finally:
            #: ★**읽기만 했어도 닫는다.** 이걸 안 해서 3시간짜리 락이 생겼다.
            try:
                conn.rollback()
            finally:
                conn.close()
    except Exception as exc:  # noqa: BLE001
        #: ★"확인 못 함"과 "준비됨"을 **구분해서** 적는다. 섞으면 그게 폴백이다.
        return {
            "backend": "pg",
            "checked": False,
            "required": True,
            "reason": f"clause index readiness check failed ({type(exc).__name__})",
        }
    st["checked"] = True
    if not st["ready"]:
        st["hint"] = ("승인 릴리스와 색인이 어긋납니다. "
                      "`python -m scripts.index.build_clause_index` 로 다시 적재하세요.")
    return st


def _clause_rerank_state() -> dict[str, object]:
    """리랭크 워커가 어떤 상태인가. **켜지 않았으면 그렇다고 말한다.**

    ★워커를 여기서 **만들지 않는다.** 준비 상태를 물었을 뿐인데 4B 무게추가
      올라가면 곤란하다 — 이미 떠 있는 것만 들여다본다.
    """
    from app.core.config import get_settings

    st = get_settings()
    state: dict[str, object] = {
        "enabled": st.INSURANCE_CLAUSE_RERANK_ENABLED,
        "timeout_seconds": st.CLAUSE_RERANK_TIMEOUT_SECONDS,
        "max_candidates": st.CLAUSE_RERANK_MAX_CANDIDATES,
        "score_body": st.CLAUSE_RERANK_SCORE_BODY,
    }
    if not st.INSURANCE_CLAUSE_RERANK_ENABLED:
        state["worker"] = None
        state["note"] = "꺼져 있습니다(INSURANCE_CLAUSE_RERANK_ENABLED=false)."
        return state

    from app.adapters import rerank_worker as rw

    worker = rw.peek_worker()
    if worker is None:
        state["worker"] = None
        state["note"] = "켜져 있으나 워커가 아직 뜨지 않았습니다(첫 요청에서 적재)."
        return state
    state["worker"] = worker.stats()
    return state
