"""외부 에이전트 보고 저장 (어댑터).

★지금까지 **받아놓고 버렸다.**

    `/v1/observations` 가 202 만 돌려주고 아무것도 안 쌓았다.
    `data/external/` 폴더 자체가 없었다.
    데모에서 에이전트가 보고를 보내면 **영영 사라진다.**

★설계는 `docs/handoff/03_에이전트_데이터_축적_설계.md` 를 따른다

    1. **받은 것은 손대지 않고 남긴다**

        공개: data/external/submissions/{YYYY-MM}/{client_ref}/{ts}_{idem}.json
        등록: data/external/submissions/registered_agent/{YYYY-MM}/{client_ref}/{ts}_{idem}.json

       나중에 파싱 규칙이 바뀐다. 정규화한 것만 두면 "그때 뭘 받았나"를
       다시 볼 수 없다. 약관 PDF 를 원본으로 두는 것과 같은 이유다.

    2. **정규화한 것은 append-only 이벤트 로그로**

        data/external/events/{YYYY-MM-DD}.jsonl

       수정·삭제하지 않는다 — 통계가 조용히 바뀌면 안 된다.
       정정이 필요하면 정정 이벤트를 **뒤에 붙인다.**

★`verification="unverified"` 로 고정한다

    클라이언트가 `verification="confirmed"` 를 보내도 **무시한다.**
    남이 자기 데이터를 스스로 "검증됨"이라 선언하면 그건 검증이 아니다.
    승급은 우리 쪽 검수·규칙엔진·발행처 확인으로만 한다.

★멱등키

    같은 `idempotency_key` 가 다시 오면 **새로 쓰지 않고** 기존 것을 알린다.
    에이전트는 재시도한다 — 재시도가 통계를 부풀리면 안 된다.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.errors import ConflictErr, InfraError, ValidationErr

_ROOT = Path(__file__).resolve().parents[2]
_BASE = _ROOT / "data" / "external"
_SUBMISSIONS = _BASE / "submissions"
_EVENTS = _BASE / "events"
_STORE_LOCK = threading.RLock()
_PUBLIC_CHANNEL = "public"
_REGISTERED_CHANNEL = "registered_agent"
_CHANNELS = {_PUBLIC_CHANNEL, _REGISTERED_CHANNEL}

#: ★클라이언트가 무엇을 보내든 이 값으로 고정한다.
_FIXED_VERIFICATION = "unverified"


@dataclass(frozen=True)
class StoreResult:
    """저장 결과."""

    stored: bool
    idempotency_key: str
    submission_id: str
    channel: str
    #: 이미 있던 것이면 True — 재시도다.
    duplicate: bool = False
    raw_path: str = ""


def _rel(path: Path) -> str:
    """저장소 기준 상대경로. **경로 표시 때문에 저장이 실패하면 안 된다.**

    ★테스트가 저장 위치를 임시 폴더로 돌리면 `relative_to` 가 `ValueError` 를 던진다.
      합성 저장소에서 같은 것을 이미 겪었다(`demo_submission_store._rel`).
    """
    try:
        return str(path.relative_to(_ROOT))
    except ValueError:
        return str(path)


def _legacy_safe(s: str, *, limit: int = 40) -> str:
    """기존 파일 경로를 찾기 위한 v1 token."""
    out = "".join(ch for ch in (s or "") if ch.isalnum() or ch in "-_")
    return (out or "unknown")[:limit]


def _path_token(value: str, *, limit: int = 72) -> str:
    """경로용 표현과 논리 identity를 분리한다.

    전체 값의 hash suffix를 항상 붙여 잘림·문자 정규화로 서로 다른 값이 합쳐지지 않게 한다.
    """

    raw = value or "unknown"
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in "-_.~") or "unknown"
    suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    head = cleaned[: max(1, limit - len(suffix) - 1)]
    return f"{head}-{suffix}"


def _payload_for_hash(payload: dict) -> dict:
    """값 없는 신규 메타필드는 배포 전 payload의 내용 멱등키를 바꾸지 않는다."""

    normalized = dict(payload)
    if not normalized.get("idempotency_key"):
        normalized.pop("idempotency_key", None)
    return normalized


def _idem_key(payload: dict, client_ref: str) -> str:
    """멱등키. 클라이언트가 안 주면 내용으로 만든다."""
    given = (payload.get("idempotency_key") or "").strip()
    if given:
        return given
    raw = json.dumps(
        {"c": client_ref, "p": _payload_for_hash(payload)},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _payload_hash(payload: dict) -> str:
    raw = json.dumps(
        _payload_for_hash(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _channel_root(channel: str) -> Path:
    return _SUBMISSIONS if channel == _PUBLIC_CHANNEL else _SUBMISSIONS / _REGISTERED_CHANNEL


def _record_channel(record: dict) -> str:
    provenance = record.get("provenance")
    if isinstance(provenance, dict):
        return str(provenance.get("channel") or _PUBLIC_CHANNEL)
    return _PUBLIC_CHANNEL


def _existing_for(client: str, key: str, *, channel: str) -> list[Path]:
    """월 경계를 넘어 재시도해도 같은 client/key를 다시 쌓지 않는다."""

    root = _channel_root(channel)
    if not root.exists():
        return []
    candidates = list(
        root.glob(f"*/{_path_token(client)}/*_{_path_token(key)}.json")
    )
    if channel == _PUBLIC_CHANNEL:
        # 배포 전 v1 경로도 논리 identity를 읽어 대조한다.
        candidates.extend(
            root.glob(
                f"*/{_legacy_safe(client)}/*_{_legacy_safe(key, limit=64)}.json"
            )
        )
    matched: list[Path] = []
    for path in sorted(set(candidates)):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise InfraError(f"기존 보고의 멱등성 정보를 읽지 못했습니다: {e}") from e
        if (
            str(record.get("client_ref") or "") == client
            and str(record.get("idempotency_key") or "") == key
            and _record_channel(record) == channel
        ):
            matched.append(path)
    return matched


def _stable_submission_id(*, channel: str, client: str, key: str) -> str:
    raw = f"{channel}\x1f{client}\x1f{key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _normalized_event(
    payload: dict,
    *,
    client: str,
    key: str,
    submission_id: str,
    channel: str,
    at: datetime,
) -> dict:
    return {
        "event": "claim_outcome",
        "at": at.isoformat(timespec="seconds"),
        "idempotency_key": key,
        "submission_id": submission_id,
        "client_ref": client,
        "ingest_channel": channel,
        "insurer": payload.get("insurer", ""),
        "enrolled_on": payload.get("enrolled_on", ""),
        "kcd_codes": payload.get("kcd_codes", []),
        "outcome": payload.get("outcome", ""),
        "outcome_reason": payload.get("outcome_reason", ""),
        "precheck_trace_id": payload.get("precheck_trace_id"),
        "verification": _FIXED_VERIFICATION,
    }


def _event_exists(*, client: str, key: str, channel: str) -> bool:
    if not _EVENTS.exists():
        return False
    for path in sorted(_EVENTS.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            raise InfraError(f"기존 보고 이벤트를 읽지 못했습니다: {e}") from e
        for line in lines:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                raise InfraError(f"기존 보고 이벤트가 깨졌습니다: {path.name} — {e}") from e
            event_channel = event.get("ingest_channel") or _PUBLIC_CHANNEL
            if (
                event.get("client_ref") == client
                and event.get("idempotency_key") == key
                and event_channel == channel
            ):
                return True
    return False


def _append_event(event: dict, *, day: str) -> None:
    _EVENTS.mkdir(parents=True, exist_ok=True)
    with (_EVENTS / f"{day}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def store(
    payload: dict,
    *,
    received_at: datetime | None = None,
    channel: str = _PUBLIC_CHANNEL,
    authenticated_client_id: str | None = None,
) -> StoreResult:
    """프로세스 안의 동시 제출을 직렬화한다.

    등록 에이전트 경로의 다중 프로세스 멱등성 정본은 PostgreSQL
    ``ops.agent_idempotency``다. 공개 UI 파일 경로도 최소한 같은 프로세스에서는
    검사와 쓰기가 원자적으로 이어지게 한다.
    """

    if channel not in _CHANNELS:
        raise ValidationErr(f"알 수 없는 observation channel입니다: {channel}")
    logical_client = str(payload.get("client_ref") or "unknown")
    if channel == _REGISTERED_CHANNEL and authenticated_client_id != logical_client:
        raise ValidationErr("인증 client와 observation client identity가 다릅니다.")
    with _STORE_LOCK:
        return _store_unlocked(
            payload,
            received_at=received_at,
            channel=channel,
            authenticated_client_id=authenticated_client_id,
        )


def _store_unlocked(
    payload: dict,
    *,
    received_at: datetime | None = None,
    channel: str,
    authenticated_client_id: str | None,
) -> StoreResult:
    """보고 하나를 저장한다.

    Args:
        payload: 클라이언트가 보낸 그대로. **손대지 않는다.**
        received_at: 시각(테스트용 주입).

    Returns:
        `StoreResult`. `duplicate=True` 면 이미 받은 것이다.
    """
    now = received_at or datetime.now(timezone.utc)
    client = str(payload.get("client_ref") or "unknown")
    key = _idem_key(payload, client)

    month = now.strftime("%Y-%m")
    day = now.strftime("%Y-%m-%d")
    ts = now.strftime("%Y%m%dT%H%M%SZ")

    raw_dir = _channel_root(channel) / month / _path_token(client)
    raw_path = raw_dir / f"{ts}_{_path_token(key)}.json"

    #: ★멱등 — 같은 키가 이미 있으면 새로 쓰지 않는다.
    existing = _existing_for(client, key, channel=channel)
    if existing:
        try:
            previous = json.loads(existing[0].read_text(encoding="utf-8"))
            previous_payload = previous.get("payload", previous)
        except (OSError, json.JSONDecodeError) as e:
            raise InfraError(f"기존 보고의 멱등성 정보를 읽지 못했습니다: {e}") from e
        if _payload_hash(previous_payload) != _payload_hash(payload):
            raise ConflictErr("같은 idempotency_key에 다른 payload가 제출됐습니다.")
        # 원본 작성 뒤 event append에서 죽었던 부분 기록을 재시도로 복구한다.
        submission_id = str(previous.get("submission_id") or key)
        if not _event_exists(client=client, key=key, channel=channel):
            try:
                previous_at = datetime.fromisoformat(str(previous.get("received_at") or ""))
            except ValueError:
                previous_at = now
            try:
                _append_event(
                    _normalized_event(
                        previous_payload,
                        client=client,
                        key=key,
                        submission_id=submission_id,
                        channel=channel,
                        at=previous_at,
                    ),
                    day=previous_at.strftime("%Y-%m-%d"),
                )
            except OSError as e:
                raise InfraError(f"누락된 보고 이벤트를 복구하지 못했습니다: {e}") from e
        return StoreResult(
            stored=False,
            idempotency_key=key,
            submission_id=submission_id,
            channel=channel,
            duplicate=True,
            raw_path=_rel(existing[0]),
        )

    try:
        submission_id = _stable_submission_id(channel=channel, client=client, key=key)
        raw_dir.mkdir(parents=True, exist_ok=True)
        #: ① 원본 그대로. 정규화는 따로 한다.
        raw_path.write_text(
            json.dumps(
                {
                    "received_at": now.isoformat(timespec="seconds"),
                    "client_ref": client,
                    "idempotency_key": key,
                    "submission_id": submission_id,
                    "provenance": {
                        "channel": channel,
                        "authenticated_client_id": (
                            authenticated_client_id
                            if channel == _REGISTERED_CHANNEL
                            else None
                        ),
                    },
                    #: ★손대지 않은 원본.
                    "payload": payload,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

        #: ② 정규화 이벤트 — append-only.
        _append_event(
            _normalized_event(
                payload,
                client=client,
                key=key,
                submission_id=submission_id,
                channel=channel,
                at=now,
            ),
            day=day,
        )
    except OSError as e:
        raise InfraError(f"보고를 저장하지 못했습니다: {e}") from e

    return StoreResult(
        stored=True,
        idempotency_key=key,
        submission_id=submission_id,
        channel=channel,
        raw_path=_rel(raw_path),
    )


def count_events() -> int:
    """쌓인 이벤트 수. 운영 화면·테스트에서 쓴다."""
    if not _EVENTS.exists():
        return 0
    return sum(
        1
        for p in _EVENTS.glob("*.jsonl")
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


# ── 관리자 교차검증(승격) ─────────────────────────────────────────────────
#
# ★★**`verified` 라고 부르지 않는다.**
#
#   계획서 §3 은 `verified` 를 "발행처 확인·원본성 검증 또는 관리자 교차검증"으로
#   정의했다. 그런데 부트캠프 범위에서 우리가 실제로 할 수 있는 것은 뒤쪽뿐이고,
#   그것을 `verified` 라 부르면 **확인되지 않은 것이 확인된 것처럼 보인다.**
#   그래서 등급 이름을 `admin_attested` 로 따로 둔다.
#   코호트 응답은 이 등급의 건수를 **따로 세어** 보여준다.

#: 관리자 교차검증 등급. `file_cohort_stats._COUNTED` 에 포함된다(집계에 들어간다).
ATTESTED_VERIFICATION = "admin_attested"
METHOD_ADMIN_REVIEW = "admin_review"

#: 검수 근거 최소 길이. "ok" 같은 형식적 입력을 막는다.
_MIN_BASIS_LEN = 5

#: ★실제 트랙의 코호트 이벤트. **합성 경로는 이 모듈이 알지 못한다.**
_COHORT_EVENTS = _ROOT / "data" / "cohort" / "verified_real" / "events.jsonl"


def _iter_submission_files():
    if not _SUBMISSIONS.exists():
        return
    yield from sorted(_SUBMISSIONS.rglob("*.json"))


def _attested_ids() -> set[str]:
    """이미 승격된 제출 id — 두 번 세지 않는다."""
    if not _COHORT_EVENTS.exists():
        return set()
    out: set[str] = set()
    for line in _COHORT_EVENTS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.add(json.loads(line).get("submission_id", ""))
        except json.JSONDecodeError as e:
            raise InfraError(f"실제 코호트 이벤트가 깨졌습니다: {e}") from e
    return out


def _submission_id(rec: dict, path: Path) -> str:
    """저장 레코드의 멱등키. 없으면 파일명 꼬리가 그 값이다."""
    return rec.get("idempotency_key") or path.stem.split("_")[-1]


def _payload(rec: dict) -> dict:
    """★원본은 `{received_at, client_ref, idempotency_key, payload:{...}}` 구조다.

    처음에 `rec.get("insurer")` 로 읽었더니 **전부 None** 이었고, 승격 이벤트가
    보험사·결과가 빈 채로 쌓일 뻔했다(2026-08-04, 실제 파일을 열어 보고 발견).
    "받은 것은 손대지 않고 남긴다"는 설계의 대가다 — 읽을 때 한 겹 벗겨야 한다.
    """
    p = rec.get("payload")
    return p if isinstance(p, dict) else rec


def pending(limit: int = 100) -> list[dict]:
    """검수 대기 목록 — 아직 승격되지 않은 실제 제보."""
    done = _attested_ids()
    out: list[dict] = []
    for p in _iter_submission_files():
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise InfraError(f"제보 파일이 깨졌습니다: {p.name} — {e}") from e
        sid = _submission_id(rec, p)
        if sid in done:
            continue
        pl = _payload(rec)
        out.append({
            "submission_id": sid,
            "received_at": rec.get("received_at", ""),
            "ingest_channel": _record_channel(rec),
            "client_ref": pl.get("client_ref") or rec.get("client_ref"),
            "insurer": pl.get("insurer", ""),
            "kcd_codes": pl.get("kcd_codes") or [],
            "outcome": pl.get("outcome", ""),
            "outcome_reason": pl.get("outcome_reason", ""),
            "precheck_trace_id": pl.get("precheck_trace_id"),
            #: ★클라이언트가 뭐라 주장했든 저장된 등급은 미검증이다.
            "verification": _FIXED_VERIFICATION,
        })
        if len(out) >= limit:
            break
    return out


def counts() -> dict:
    total = sum(1 for _ in _iter_submission_files())
    attested = len(_attested_ids())
    return {"submitted": total, "attested": attested, "pending": total - attested}


def attest(submission_id: str, *, basis: str, actor: str,
           at: datetime | None = None) -> dict:
    """관리자 교차검증으로 승격한다 → 실제 코호트 집계에 한 줄 쌓인다.

    ★`basis`(무엇을 보고 납득했는지)를 **강제로 받는다.** 근거 없는 승격은
      나중에 설명할 수 없고, 설명 못 하는 숫자는 통계가 아니다.
    """
    from app.core.errors import ValidationErr

    sid = (submission_id or "").strip()
    if not sid:
        raise ValidationErr("submission_id 가 비어 있습니다.")
    #: ★길이 규칙을 **여기** 둔다. 라우터의 pydantic 에만 두면 CLI·스크립트가
    #:   우회한다 — 규칙이 두 곳이면 느슨한 쪽이 실질 규칙이 된다.
    if len((basis or "").strip()) < _MIN_BASIS_LEN:
        raise ValidationErr(
            f"검수 근거(basis)는 {_MIN_BASIS_LEN}자 이상이어야 합니다. "
            "무엇을 보고 납득했는지 적으세요(예: 지급통지서 사본 대조)."
        )
    if sid in _attested_ids():
        raise ValidationErr(f"이미 승격된 제보입니다: {sid}")

    rec = None
    for p in _iter_submission_files():
        cand = json.loads(p.read_text(encoding="utf-8"))
        if _submission_id(cand, p) == sid:
            rec = cand
            break
    if rec is None:
        raise ValidationErr(f"제보를 찾을 수 없습니다: {sid}")

    now = at or datetime.now(timezone.utc)
    pl = _payload(rec)
    event = {
        "submission_id": sid,
        "ingest_channel": _record_channel(rec),
        "client_ref": pl.get("client_ref") or rec.get("client_ref"),
        "insurer": pl.get("insurer", ""),
        "kcd_codes": pl.get("kcd_codes") or [],
        "product_id": pl.get("product_id", ""),
        "age_band": pl.get("age_band"),
        "outcome": pl.get("outcome", ""),
        #: ★`verified` 가 아니다. 관리자가 납득했다는 뜻이다.
        "verification": ATTESTED_VERIFICATION,
        "verification_method": METHOD_ADMIN_REVIEW,
        "verified_by": actor,
        "verification_basis": basis.strip(),
        "verified_at": now.isoformat(),
        "data_source": "verified_real",
    }
    try:
        _COHORT_EVENTS.parent.mkdir(parents=True, exist_ok=True)
        with _COHORT_EVENTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        raise InfraError(f"실제 코호트 이벤트를 쓰지 못했습니다: {e}") from e
    return event


__all__ = [
    "ATTESTED_VERIFICATION", "METHOD_ADMIN_REVIEW", "StoreResult",
    "attest", "count_events", "counts", "pending", "store",
]
