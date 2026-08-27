"""Integrity-checked registry for shadow candidate facts.

Configured candidate files are project inputs, but never serving or citation
inputs until a separate human-approved release materializes them.  Readiness
checks their location, digest, counts, and isolation contract so artifacts
cannot silently disappear or become eligible through a malformed row.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ACCEPTED_CONFIG = ROOT / "config" / "accepted_extraction.json"


def _inside_root(relative: str) -> Path:
    path = (ROOT / relative).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"candidate path escapes project root: {relative}") from exc
    return path


def _blocked(record: dict) -> bool:
    if record.get("serving_eligible") is True or record.get("citation_eligible") is True:
        return False
    if str(record.get("approval_status") or record.get("approval") or "").lower() in {
        "accepted", "approve", "approved", "human_pattern_approved"
    }:
        return False
    for fact in record.get("facts") or []:
        if fact.get("serving_eligible") is True or fact.get("citation_eligible") is True:
            return False
        if str(fact.get("approval_status") or fact.get("approval") or "").lower() in {
            "accepted", "approve", "approved", "human_pattern_approved"
        }:
            return False
    return True


def check_candidate_fact_sources() -> dict[str, object]:
    """Return public-safe readiness details for configured shadow sources."""
    try:
        accepted = json.loads(ACCEPTED_CONFIG.read_text(encoding="utf-8"))
        registry_ref = accepted.get("candidate_fact_registry")
        if not registry_ref:
            return {"configured": False, "ready": True, "sources": 0, "rows": 0, "facts": 0}
        registry_path = _inside_root(str(registry_ref))
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        if registry.get("schema_version") != "candidate-fact-sources-v1":
            raise ValueError("unsupported candidate registry schema")

        source_results: list[dict[str, object]] = []
        total_rows = 0
        total_facts = 0
        for source in registry.get("sources") or []:
            path = _inside_root(str(source["path"]))
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            if digest != source.get("sha256"):
                raise ValueError(f"sha256 mismatch: {source.get('source_id')}")
            rows = [json.loads(line) for line in payload.decode("utf-8").splitlines() if line.strip()]
            facts = sum(len(row.get("facts") or []) for row in rows)
            if len(rows) != int(source.get("rows") or -1):
                raise ValueError(f"row count mismatch: {source.get('source_id')}")
            if facts != int(source.get("facts") or -1):
                raise ValueError(f"fact count mismatch: {source.get('source_id')}")
            if source.get("serving_eligible") is not False or source.get("citation_eligible") is not False:
                raise ValueError(f"registry source is not shadow-only: {source.get('source_id')}")
            if not all(_blocked(row) for row in rows):
                raise ValueError(f"eligible/approved row found in candidate source: {source.get('source_id')}")
            total_rows += len(rows)
            total_facts += facts
            source_results.append({
                "source_id": source.get("source_id"),
                "fact_type": source.get("fact_type"),
                "rows": len(rows),
                "facts": facts,
                "shadow_only": True,
            })
        return {
            "configured": True,
            "ready": True,
            "sources": len(source_results),
            "rows": total_rows,
            "facts": total_facts,
            "details": source_results,
        }
    except Exception as exc:  # noqa: BLE001 - readiness must report, not crash
        return {
            "configured": True,
            "ready": False,
            "sources": 0,
            "rows": 0,
            "facts": 0,
            "reason": f"{type(exc).__name__}: {exc}"[:200],
        }


__all__ = ["check_candidate_fact_sources"]
