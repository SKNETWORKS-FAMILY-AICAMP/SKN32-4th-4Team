"""기존 합성 JSON/JSONL을 별도 insurance_demo PostgreSQL로 멱등 이관한다.

기본은 dry-run이다. 실제 반영은 ``--apply``를 명시해야 한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from app.adapters import pg_demo_submission_store as pg

ROOT = Path(__file__).resolve().parents[1]
SUBMISSIONS = ROOT / "data" / "demo" / "submissions"
ACCEPTED = ROOT / "data" / "cohort" / "synthetic" / "events.jsonl"


def _load() -> tuple[list[dict], dict[str, dict]]:
    records = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(SUBMISSIONS.rglob("*.json"))
    ] if SUBMISSIONS.exists() else []
    accepted: dict[str, dict] = {}
    if ACCEPTED.exists():
        for line in ACCEPTED.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            sid = str(ev.get("submission_id") or "")
            if sid in accepted:
                raise RuntimeError(f"승격 이벤트가 중복됐습니다: {sid}")
            accepted[sid] = ev
    ids = {str(r.get("submission_id") or "") for r in records}
    dangling = sorted(set(accepted) - ids)
    if dangling:
        raise RuntimeError(f"제출 없는 승격 이벤트가 있습니다: {dangling[:5]}")
    return records, accepted


def _source_snapshot(records: list[dict], accepted: dict[str, dict]) -> dict:
    rows = sorted(
        [[str(r["submission_id"]), pg.canonical_hash(r), str(r["submission_id"]) in accepted]
         for r in records],
        key=lambda x: x[0],
    )
    digest = hashlib.sha256(
        json.dumps(rows, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "submitted": len(rows),
        "accepted": sum(1 for r in rows if r[2]),
        "snapshot_sha256": digest,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="명시해야 실제 DB에 반영")
    args = ap.parse_args()
    records, accepted = _load()
    source = _source_snapshot(records, accepted)
    print("source", json.dumps(source, ensure_ascii=False))
    print("target_before", json.dumps(pg.legacy_snapshot(), ensure_ascii=False))
    if not args.apply:
        print("dry-run: 변경 없음. 반영하려면 --apply")
        return 0

    result = pg.import_legacy_batch(records, accepted)
    target = pg.legacy_snapshot()
    print("import", json.dumps(result, ensure_ascii=False))
    print("target_after", json.dumps(target, ensure_ascii=False))
    if source != target:
        raise RuntimeError(f"count/hash reconcile 실패: source={source}, target={target}")
    print("reconcile OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
