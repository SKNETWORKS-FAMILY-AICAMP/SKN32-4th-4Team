"""합성(데모) 트랙 전용 — 제출 보관과 **검증 승격**.

★★이 모듈은 실제 트랙을 **모른다.**

    계획서 §5-1 이 요구한 분리는 "필드 하나로 가르기"가 아니다. 다섯 층으로 나눈다 —
    저장·수집·집계·API·보증. 그 중 **수집** 층이 여기다.

        실제   `/v1/observations`      → `app/adapters/external_submission_store.py`
                                         → `data/external/submissions/…`
        합성   `/v1/demo/observations` → **이 파일**
                                         → `data/demo/submissions/…`
                                         → 승격 시 `data/cohort/synthetic/events.jsonl`

    ★그래서 이 파일에는 `verified_real` 이라는 문자열도, 그 폴더로 가는 경로 계산도
      **존재하지 않는다.** 실수로 섞으려면 코드를 새로 써야 한다.
      `data_source` 불리언 하나로 갈랐다면 `WHERE` 하나 빠뜨리는 순간 섞인다.

★승격이 하는 일과 하지 않는 일

    하는 일: `unverified` 인 합성 제출을 `synthetic_admin_review` 또는
             `synthetic_consistency` 로 올리고,
             그 순간 **합성 코호트 집계에만** 한 줄이 쌓인다(append-only).
    하지 않는 일: 진위 판단. 합성 데이터에 진위는 없다.
             이것이 시연하는 것은 **"정합성만으로는 통계가 안 움직이고,
             검증 단계를 거쳐야 움직인다"는 구조**이지 사실성이 아니다.

    ★그래서 승격 이벤트에는 `verification_method` 를 반드시 남긴다
      (`admin_review` / `simulated`). 나중에 "이 숫자가 어떻게 생겼나"를
      물었을 때 답할 수 있어야 한다.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.errors import InfraError, ValidationErr

_ROOT = Path(__file__).resolve().parents[2]

#: ★합성 트랙의 경로 **전부**. 여기 없는 곳에는 쓰지 않는다.
_SUBMISSIONS = _ROOT / "data" / "demo" / "submissions"
_COHORT_EVENTS = _ROOT / "data" / "cohort" / "synthetic" / "events.jsonl"
_VERIFICATION_EVENTS = _ROOT / "data" / "demo" / "verifications" / "events.jsonl"

#: 제출은 언제나 미검증으로 들어온다(실제 트랙과 같은 규칙).
_FIXED_VERIFICATION = "unverified"

#: 승격 방법. **지어내지 않는다** — 둘 중 하나만 허용한다.
METHOD_ADMIN = "admin_review"
METHOD_SIMULATED = "simulated"  # 이전 파일 이벤트 호환용
METHOD_SIMULATED_CONSISTENCY = "simulated_consistency"
_METHODS = frozenset({METHOD_ADMIN, METHOD_SIMULATED, METHOD_SIMULATED_CONSISTENCY})

_OUTCOMES = frozenset({"paid", "denied", "partial", "pending"})

_SAFE = re.compile(r"[^0-9A-Za-z가-힣._-]+")


def _safe(text: str, *, limit: int = 40) -> str:
    return (_SAFE.sub("-", text).strip("-") or "unknown")[:limit]


def _idem_key(payload: dict, client_ref: str) -> str:
    given = (payload.get("idempotency_key") or "").strip()
    if given:
        return _safe(given, limit=64)
    raw = json.dumps({"c": client_ref, "p": payload}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class DemoStoreResult:
    stored: bool
    submission_id: str
    duplicate: bool = False
    path: str = ""
    promoted: bool = False
    verification: str = "unverified"
    reason_codes: tuple[str, ...] = ()
    rule_version: str = ""


def backend_name() -> str:
    from app.core.config import get_settings

    return get_settings().DEMO_STORE_BACKEND


def store(
    payload: dict,
    *,
    received_at: datetime | None = None,
    auto_validate: bool = False,
) -> DemoStoreResult:
    """합성 제출 하나를 보관한다. **집계에는 아직 들어가지 않는다.**"""
    outcome = str(payload.get("outcome") or "").strip()
    if outcome not in _OUTCOMES:
        raise ValidationErr(f"outcome 은 {sorted(_OUTCOMES)} 중 하나여야 합니다: {outcome!r}")

    now = received_at or datetime.now(timezone.utc)
    client = _safe(str(payload.get("client_ref") or "unknown"))
    key = _idem_key(payload, client)
    decision = None
    if auto_validate:
        from app.core.domain.synthetic_validation import evaluate

        decision = evaluate(payload)

    if backend_name() == "postgres":
        from app.adapters import pg_demo_submission_store as pg

        return pg.store(
            payload,
            client_ref=client,
            idempotency_key=key,
            received_at=now,
            decision=decision,
        )

    day_dir = _SUBMISSIONS / now.strftime("%Y-%m") / client

    existing = list(day_dir.glob(f"*_{key}.json")) if day_dir.exists() else []
    if existing:
        return DemoStoreResult(
            stored=False, submission_id=key, duplicate=True,
            path=_rel(existing[0]),
        )

    record = {
        "submission_id": key,
        "client_ref": payload.get("client_ref"),
        "insurer": payload.get("insurer"),
        "enrolled_on": payload.get("enrolled_on", ""),
        "kcd_codes": payload.get("kcd_codes") or [],
        "product_id": payload.get("product_id", ""),
        "age_band": payload.get("age_band"),
        "outcome": outcome,
        "outcome_reason": payload.get("outcome_reason", ""),
        "precheck_trace_id": payload.get("precheck_trace_id"),
        #: ★클라이언트가 뭐라 보내든 미검증이다(실제 트랙과 동일).
        "verification": _FIXED_VERIFICATION,
        "received_at": now.isoformat(),
        #: ★이 줄이 이 레코드의 정체다. 화면·응답 어디서도 지우지 않는다.
        "data_source": "synthetic",
    }
    try:
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"{now.strftime('%Y%m%dT%H%M%SZ')}_{key}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        raise InfraError(f"합성 제출을 저장하지 못했습니다: {e}") from e

    result = DemoStoreResult(stored=True, submission_id=key, path=_rel(path))
    if decision is None:
        return result
    if decision.accepted:
        promote(
            key,
            method=METHOD_SIMULATED_CONSISTENCY,
            actor="simulator",
            at=now,
        )
        return DemoStoreResult(
            stored=True,
            submission_id=key,
            path=_rel(path),
            promoted=True,
            verification="synthetic_consistency",
            reason_codes=decision.reason_codes,
            rule_version=decision.rule_version,
        )
    _append_file_verification(
        submission_id=key,
        decision="rejected",
        method=METHOD_SIMULATED_CONSISTENCY,
        level="synthetic_consistency",
        rule_version=decision.rule_version,
        reason_codes=decision.reason_codes,
        evidence=decision.evidence,
        actor="simulator",
        at=now,
    )
    return DemoStoreResult(
        stored=True,
        submission_id=key,
        path=_rel(path),
        verification="rejected",
        reason_codes=decision.reason_codes,
        rule_version=decision.rule_version,
    )


def _append_file_verification(
    *, submission_id: str, decision: str, method: str, level: str,
    rule_version: str, reason_codes: tuple[str, ...], evidence: dict,
    actor: str, at: datetime,
) -> None:
    event = {
        "submission_id": submission_id,
        "decision": decision,
        "verification_method": method,
        "verification": level,
        "rule_version": rule_version,
        "reason_codes": list(reason_codes),
        "evidence": evidence,
        "verified_by": actor,
        "verified_at": at.isoformat(),
        "data_source": "synthetic",
    }
    try:
        _VERIFICATION_EVENTS.parent.mkdir(parents=True, exist_ok=True)
        with _VERIFICATION_EVENTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        raise InfraError(f"합성 검증 이벤트를 저장하지 못했습니다: {e}") from e


def _rel(path: Path) -> str:
    """저장소 기준 상대경로. **테스트가 임시 폴더로 옮겨도 깨지지 않게** 한다.

    ★`relative_to` 만 쓰면 경로가 저장소 밖일 때 `ValueError` 로 죽는다.
      경로 표시 하나 때문에 저장 자체가 실패하는 것은 과하다.
    """
    try:
        return str(path.relative_to(_ROOT))
    except ValueError:
        return str(path)


def _iter_submission_files():
    if not _SUBMISSIONS.exists():
        return
    yield from sorted(_SUBMISSIONS.rglob("*.json"))


def _promoted_ids() -> set[str]:
    """이미 승격된 제출 id. 승격은 **한 번만** 반영된다(중복 집계 금지)."""
    if not _COHORT_EVENTS.exists():
        return set()
    out: set[str] = set()
    for line in _COHORT_EVENTS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.add(json.loads(line).get("submission_id", ""))
        except json.JSONDecodeError as e:
            raise InfraError(f"합성 코호트 이벤트가 깨졌습니다: {e}") from e
    return out


def pending(limit: int = 100) -> list[dict]:
    """검수 대기 목록 — 아직 승격되지 않은 합성 제출."""
    if backend_name() == "postgres":
        from app.adapters import pg_demo_submission_store as pg

        return pg.pending(limit)
    done = _promoted_ids()
    out: list[dict] = []
    for p in _iter_submission_files():
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise InfraError(f"합성 제출 파일이 깨졌습니다: {p.name} — {e}") from e
        if rec.get("submission_id") in done:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def promote(submission_id: str, *, method: str, actor: str,
            at: datetime | None = None) -> dict:
    """합성 제출 하나를 승격해 **합성 코호트 집계에** 한 줄 쌓는다.

    ★`method` 를 강제로 받는다. "누가 어떻게 올렸나"가 없는 통계는
      나중에 설명할 수 없다.
    """
    if method not in _METHODS:
        raise ValidationErr(f"승격 방법은 {sorted(_METHODS)} 중 하나여야 합니다: {method!r}")
    if backend_name() == "postgres":
        if method != METHOD_ADMIN:
            raise ValidationErr(
                "PostgreSQL 자동 정합성 검사는 제출 트랜잭션 안에서만 실행할 수 있습니다."
            )
        from app.adapters import pg_demo_submission_store as pg

        return pg.promote(submission_id, actor=actor, at=at)
    sid = (submission_id or "").strip()
    if not sid:
        raise ValidationErr("submission_id 가 비어 있습니다.")

    if sid in _promoted_ids():
        raise ValidationErr(f"이미 승격된 제출입니다: {sid}")

    rec = None
    for p in _iter_submission_files():
        cand = json.loads(p.read_text(encoding="utf-8"))
        if cand.get("submission_id") == sid:
            rec = cand
            break
    if rec is None:
        raise ValidationErr(f"합성 제출을 찾을 수 없습니다: {sid}")

    now = at or datetime.now(timezone.utc)
    level = (
        "synthetic_consistency"
        if method in {METHOD_SIMULATED, METHOD_SIMULATED_CONSISTENCY}
        else "synthetic_admin_review"
    )
    event = {
        "submission_id": sid,
        "client_ref": rec.get("client_ref"),
        "insurer": rec.get("insurer"),
        "kcd_codes": rec.get("kcd_codes") or [],
        "product_id": rec.get("product_id", ""),
        "age_band": rec.get("age_band"),
        "outcome": rec.get("outcome"),
        #: ★이 값이 `_COUNTED` 에 들어가야 비로소 집계된다.
        "verification": level,
        "verification_method": method,
        "verified_by": actor,
        "verified_at": now.isoformat(),
        "data_source": "synthetic",
    }
    try:
        _COHORT_EVENTS.parent.mkdir(parents=True, exist_ok=True)
        with _COHORT_EVENTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        raise InfraError(f"합성 코호트 이벤트를 쓰지 못했습니다: {e}") from e
    return event


def counts() -> dict:
    """대시보드용 요약 — 제출 몇 건 중 몇 건이 승격됐나."""
    if backend_name() == "postgres":
        from app.adapters import pg_demo_submission_store as pg

        return pg.counts()
    total = sum(1 for _ in _iter_submission_files())
    promoted = len(_promoted_ids())
    return {"submitted": total, "promoted": promoted, "pending": total - promoted}


def reset() -> dict:
    """선택한 합성 백엔드만 비운다. 실제 트랙 경로는 알지 못한다."""
    if backend_name() == "postgres":
        from app.adapters import pg_demo_submission_store as pg

        return pg.reset()
    removed: list[str] = []
    for d in (_SUBMISSIONS, _COHORT_EVENTS.parent, _VERIFICATION_EVENTS.parent):
        if d.exists():
            shutil.rmtree(d)
            removed.append(_rel(d))
    return {"reset": True, "removed": removed, "data_source": "synthetic"}


__all__ = [
    "METHOD_ADMIN", "METHOD_SIMULATED", "METHOD_SIMULATED_CONSISTENCY",
    "DemoStoreResult", "backend_name", "counts",
    "pending", "promote", "reset", "store",
]
