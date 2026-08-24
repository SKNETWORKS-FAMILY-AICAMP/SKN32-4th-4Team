"""Merge and verify disjoint distributed coverage-boundary audit shards."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--expected-documents", type=int, default=1367)
    args = ap.parse_args()

    paths = sorted(Path(args.input_dir).glob("*.json"))
    if not paths:
        raise SystemExit("no audit shards found")
    totals = collections.Counter()
    insurers: dict[str, dict] = {}
    recovered: list[dict] = []
    unresolved: list[dict] = []
    shard_provenance: list[dict] = []
    for path in paths:
        shard = json.loads(path.read_text(encoding="utf-8"))
        claimed = shard.pop("payload_sha256", None)
        actual = hashlib.sha256(_canonical(shard)).hexdigest()
        if not claimed or claimed != actual:
            raise SystemExit(f"shard hash mismatch: {path}")
        overlap = set(insurers).intersection(shard.get("per_insurer") or {})
        if overlap:
            raise SystemExit(f"duplicate insurers across shards: {sorted(overlap)}")
        insurers.update(shard.get("per_insurer") or {})
        totals.update(shard.get("totals") or {})
        recovered.extend(shard.get("recovered_rows") or [])
        unresolved.extend(shard.get("unresolved_rows") or [])
        shard_provenance.append({
            "file": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "payload_sha256": actual,
            "host": (shard.get("provenance") or {}).get("host"),
            "workers": (shard.get("provenance") or {}).get("workers"),
            "seconds": (shard.get("provenance") or {}).get("seconds"),
            "insurers": sorted((shard.get("per_insurer") or {}).keys()),
        })
    if totals["documents"] != args.expected_documents:
        raise SystemExit(
            f"document coverage mismatch: {totals['documents']} != {args.expected_documents}"
        )
    if totals["recovered"] != len(recovered) or totals["unresolved"] != len(unresolved):
        raise SystemExit("row evidence count mismatch")

    result = {
        "schema_version": "coverage-boundary-audit-merged-v1",
        "validation": {
            "shard_hashes_valid": True,
            "insurer_shards_disjoint": True,
            "documents_complete": True,
            "expected_documents": args.expected_documents,
            "shards": len(paths),
        },
        "totals": dict(sorted(totals.items())),
        "per_insurer": dict(sorted(insurers.items())),
        "recovered_rows": recovered,
        "unresolved_rows": unresolved,
        "shard_provenance": shard_provenance,
    }
    result["payload_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "totals": result["totals"],
                      "payload_sha256": result["payload_sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
