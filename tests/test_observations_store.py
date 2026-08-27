"""외부 에이전트 보고 저장.

★지금까지 202 만 돌려주고 **아무것도 안 쌓았다.**
  데모에서 보고를 보내면 영영 사라지는 상태였다.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.adapters import external_submission_store as store
from app.main import create_app

_BODY = {
    "client_ref": "agent-test",
    "insurer": "DB손해보험",
    "enrolled_on": "20200301",
    "kcd_codes": ["F32"],
    "outcome": "denied",
    "outcome_reason": "면책 조항",
    "idempotency_key": "test-key-001",
}


def test_원본을_그대로_남긴다(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_ROOT", tmp_path)
    monkeypatch.setattr(store, "_SUBMISSIONS", tmp_path / "submissions")
    monkeypatch.setattr(store, "_EVENTS", tmp_path / "events")

    r = store.store(dict(_BODY))
    assert r.stored
    raw = list((tmp_path / "submissions").rglob("*.json"))
    assert len(raw) == 1
    saved = json.loads(raw[0].read_text(encoding="utf-8"))
    #: ★원본은 손대지 않는다 — 나중에 파싱 규칙이 바뀐다.
    assert saved["payload"] == _BODY


def test_정규화_이벤트를_append_한다(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_ROOT", tmp_path)
    monkeypatch.setattr(store, "_SUBMISSIONS", tmp_path / "submissions")
    monkeypatch.setattr(store, "_EVENTS", tmp_path / "events")

    store.store(dict(_BODY))
    store.store({**_BODY, "idempotency_key": "test-key-002"})
    lines = [
        line
        for p in (tmp_path / "events").glob("*.jsonl")
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 2


def test_같은_멱등키는_다시_쌓지_않는다(monkeypatch, tmp_path):
    """★에이전트는 재시도한다. 재시도가 통계를 부풀리면 안 된다."""
    monkeypatch.setattr(store, "_ROOT", tmp_path)
    monkeypatch.setattr(store, "_SUBMISSIONS", tmp_path / "submissions")
    monkeypatch.setattr(store, "_EVENTS", tmp_path / "events")

    first = store.store(dict(_BODY))
    second = store.store(dict(_BODY))
    assert first.stored
    assert not second.stored
    assert second.duplicate
    assert len(list((tmp_path / "submissions").rglob("*.json"))) == 1


def test_같은_멱등키에_다른_payload는_충돌이다(monkeypatch, tmp_path):
    """멱등 재시도와 payload 바꿔치기를 같은 것으로 취급하지 않는다."""
    from app.core.errors import ConflictErr

    monkeypatch.setattr(store, "_ROOT", tmp_path)
    monkeypatch.setattr(store, "_SUBMISSIONS", tmp_path / "submissions")
    monkeypatch.setattr(store, "_EVENTS", tmp_path / "events")

    store.store(dict(_BODY))
    with pytest.raises(ConflictErr):
        store.store({**_BODY, "outcome": "paid"})
    assert len(list((tmp_path / "submissions").rglob("*.json"))) == 1


def test_원본만_남은_부분기록은_재시도에서_이벤트를_복구한다(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_ROOT", tmp_path)
    monkeypatch.setattr(store, "_SUBMISSIONS", tmp_path / "submissions")
    monkeypatch.setattr(store, "_EVENTS", tmp_path / "events")

    raw_dir = tmp_path / "submissions" / "2026-08" / "agent-test"
    raw_dir.mkdir(parents=True)
    raw = raw_dir / "20260804T010203Z_test-key-001.json"
    raw.write_text(
        json.dumps(
            {
                "received_at": "2026-08-04T01:02:03+00:00",
                "client_ref": "agent-test",
                "idempotency_key": "test-key-001",
                "payload": _BODY,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = store.store(dict(_BODY))
    assert result.duplicate is True
    events = list((tmp_path / "events").glob("*.jsonl"))
    assert len(events) == 1
    row = json.loads(events[0].read_text(encoding="utf-8").strip())
    assert row["idempotency_key"] == "test-key-001"
    assert row["verification"] == "unverified"


def test_멱등키_문자와_긴_client_identity가_서로_충돌하지_않는다(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_ROOT", tmp_path)
    monkeypatch.setattr(store, "_SUBMISSIONS", tmp_path / "submissions")
    monkeypatch.setattr(store, "_EVENTS", tmp_path / "events")

    dotted = store.store({**_BODY, "idempotency_key": "case.key.0001"})
    tilded = store.store({**_BODY, "idempotency_key": "case~key~0001"})
    assert dotted.stored and tilded.stored
    assert dotted.submission_id != tilded.submission_id

    prefix = "agent-" + "x" * 50
    first = store.store({**_BODY, "client_ref": prefix + "a", "idempotency_key": "same-key-0001"})
    second = store.store({**_BODY, "client_ref": prefix + "b", "idempotency_key": "same-key-0001"})
    assert first.stored and second.stored
    assert first.submission_id != second.submission_id


def test_공개와_등록_agent_namespace는_서로_선점하지_못한다(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_ROOT", tmp_path)
    monkeypatch.setattr(store, "_SUBMISSIONS", tmp_path / "submissions")
    monkeypatch.setattr(store, "_EVENTS", tmp_path / "events")

    payload = {**_BODY, "client_ref": "agent-a", "idempotency_key": "shared-key-0001"}
    public = store.store(payload)
    protected = store.store(
        payload,
        channel="registered_agent",
        authenticated_client_id="agent-a",
    )
    assert public.stored and protected.stored
    assert public.channel == "public"
    assert protected.channel == "registered_agent"
    assert public.submission_id != protected.submission_id

    protected_raw = json.loads(
        (tmp_path / protected.raw_path).read_text(encoding="utf-8")
        if not (tmp_path / protected.raw_path).is_absolute()
        else (tmp_path / protected.raw_path).read_text(encoding="utf-8")
    )
    assert protected_raw["provenance"] == {
        "channel": "registered_agent",
        "authenticated_client_id": "agent-a",
    }


def test_legacy_무키_payload는_현재_none필드_재시도와_같다(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_ROOT", tmp_path)
    monkeypatch.setattr(store, "_SUBMISSIONS", tmp_path / "submissions")
    monkeypatch.setattr(store, "_EVENTS", tmp_path / "events")

    legacy_payload = {key: value for key, value in _BODY.items() if key != "idempotency_key"}
    key = store._idem_key(legacy_payload, "agent-test")
    raw_dir = tmp_path / "submissions" / "2026-08" / "agent-test"
    raw_dir.mkdir(parents=True)
    (raw_dir / f"20260804T010203Z_{key}.json").write_text(
        json.dumps(
            {
                "received_at": "2026-08-04T01:02:03+00:00",
                "client_ref": "agent-test",
                "idempotency_key": key,
                "payload": legacy_payload,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    current_payload = {**legacy_payload, "idempotency_key": None}
    result = store.store(current_payload)
    assert result.duplicate is True
    assert len(list((tmp_path / "submissions").rglob("*.json"))) == 1


def test_클라이언트가_검증됨이라_해도_무시한다(monkeypatch, tmp_path):
    """★남이 자기 데이터를 스스로 '검증됨'이라 하면 그건 검증이 아니다."""
    monkeypatch.setattr(store, "_ROOT", tmp_path)
    monkeypatch.setattr(store, "_SUBMISSIONS", tmp_path / "submissions")
    monkeypatch.setattr(store, "_EVENTS", tmp_path / "events")

    store.store({**_BODY, "verification": "confirmed"})
    line = next(
        line
        for p in (tmp_path / "events").glob("*.jsonl")
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    assert json.loads(line)["verification"] == "unverified"


def test_경로_조작을_막는다(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_ROOT", tmp_path)
    monkeypatch.setattr(store, "_SUBMISSIONS", tmp_path / "submissions")
    monkeypatch.setattr(store, "_EVENTS", tmp_path / "events")

    store.store({**_BODY, "client_ref": "../../etc/passwd"})
    #: 상위로 새어 나가지 않는다.
    assert not (tmp_path.parent / "etc").exists()
    assert list((tmp_path / "submissions").rglob("*.json"))


def test_API_가_저장_결과를_알려준다(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "_ROOT", tmp_path)
    monkeypatch.setattr(store, "_SUBMISSIONS", tmp_path / "submissions")
    monkeypatch.setattr(store, "_EVENTS", tmp_path / "events")

    c = TestClient(create_app())
    r = c.post("/v1/observations", json=_BODY)
    assert r.status_code == 202
    j = r.json()
    assert j["stored"] is True
    assert j["verification"] == "unverified"

    again = c.post("/v1/observations", json=_BODY).json()
    assert again["duplicate"] is True


def test_postgres_저장소_장애는_503으로_명시한다(monkeypatch):
    from app.core.config import get_settings
    from app.core.errors import InfraError
    from db.postgres.pg_insurance_repository import PgInsuranceRepository

    def fail_from_settings(_cls):
        raise InfraError("보험 PostgreSQL에 연결할 수 없습니다.")

    monkeypatch.setenv("OUTCOME_PERSISTENCE", "postgres")
    monkeypatch.setenv("INSURANCE_PG_DSN", "postgresql://unavailable/insurance")
    monkeypatch.setenv("INSURANCE_IDEMPOTENCY_SECRET", "x" * 32)
    get_settings.cache_clear()
    monkeypatch.setattr(
        PgInsuranceRepository, "from_settings", classmethod(fail_from_settings)
    )
    body = {
        **_BODY,
        "precheck_trace_id": "trace-001",
        "precheck_idempotency_key": "precheck-key-001",
        "claimed_on": "20250102",
        "decided_on": "20250201",
        "evidence_doc_type": "decision_notice",
        "evidence_sha256": "a" * 64,
        "evidence_stored_ref": "object://decision",
    }
    try:
        response = TestClient(create_app(), raise_server_exceptions=False).post(
            "/v1/observations", json=body
        )
        assert response.status_code == 503
        assert response.json()["ok"] is False
        assert response.json()["error_code"] == "infra_error"
    finally:
        get_settings.cache_clear()
