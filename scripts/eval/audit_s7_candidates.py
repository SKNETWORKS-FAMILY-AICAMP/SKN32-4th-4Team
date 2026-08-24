"""Audit S7 OCR candidates against frozen native text and S6 accepted evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
EXTRACTED = ROOT / "data" / "extracted"
STRUCTURED = ROOT / "data" / "structured"


class AuditError(RuntimeError):
    pass


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def unique(parent: Path, pattern: str) -> Path:
    paths = sorted(parent.glob(pattern))
    if len(paths) != 1:
        raise AuditError(f"expected one path for {parent / pattern}, got {len(paths)}")
    return paths[0]


def token_key(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"[\s,]", "", value)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def tree_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.resolve().relative_to(ROOT.resolve()).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    body = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def audit(candidate_root: Path) -> dict[str, Any]:
    candidate_files = sorted(path for path in candidate_root.glob("*.json") if path.name != "index.json")
    candidate_ids: set[str] = set()
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    counts = Counter()
    validation_status = Counter()
    validation_reasons = Counter()
    revisions = Counter()
    insurers = Counter()
    categories = Counter()

    for path in candidate_files:
        document = load(path)
        for candidate in document.get("candidates") or []:
            candidate_id = str(candidate.get("candidate_id") or "")
            if candidate_id in candidate_ids:
                raise AuditError(f"duplicate candidate id: {candidate_id}")
            candidate_ids.add(candidate_id)
            candidates.append((document, candidate))
            insurers[str(candidate.get("insurer") or "unknown")] += 1
            categories[str(candidate.get("category") or "unknown")] += 1
            validation = candidate.get("validation") or {}
            validation_status[str(validation.get("status") or "missing")] += 1
            validation_reasons.update(str(reason) for reason in validation.get("reasons") or [])
            ocr = ((candidate.get("source") or {}).get("ocr") or {})
            revisions[str(ocr.get("model_revision") or "missing")] += 1
            alias = ocr.get("exact_image_alias") or {}
            counts["exact_alias_expanded_candidates"] += bool(alias.get("expanded"))
            counts["invalid_table_bbox"] += not (
                isinstance((candidate.get("source") or {}).get("table_bbox"), list)
                and len((candidate.get("source") or {}).get("table_bbox")) == 4
            )
            counts["serving_eligible"] += bool(candidate.get("serving_eligible"))
            counts["citation_eligible"] += bool(candidate.get("citation_eligible"))

    native_cache: dict[tuple[str, str], dict[int, str]] = {}
    for document, candidate in candidates:
        key = (str(document["insurer"]), str(document["sha12"]))
        if key not in native_cache:
            page_path = unique(EXTRACTED / key[0], f"s5_pymupdf-*/{key[1]}.json")
            page_doc = load(page_path)
            native_cache[key] = {
                int(page["page"]): token_key(
                    str(page.get("text") or "")
                    + " "
                    + json.dumps(page.get("tables") or [], ensure_ascii=False)
                )
                for page in page_doc.get("pages") or []
            }
        native = native_cache[key].get(int(candidate["page_1based"]), "")
        amount_tokens = [str(token) for token in candidate.get("amount_tokens") or []]
        rate_tokens = [str(token) for token in candidate.get("rate_tokens") or []]
        tokens = [("amount", token) for token in amount_tokens] + [
            ("rate", token) for token in rate_tokens
        ]
        supported = [(kind, token) for kind, token in tokens if token_key(token) in native]
        counts["facts_with_tokens"] += bool(tokens)
        counts["tokens_total"] += len(tokens)
        counts["tokens_supported_native_exact"] += len(supported)
        counts["amount_tokens_total"] += len(amount_tokens)
        counts["rate_tokens_total"] += len(rate_tokens)
        counts["amount_tokens_supported_native_exact"] += sum(
            kind == "amount" for kind, _ in supported
        )
        counts["rate_tokens_supported_native_exact"] += sum(kind == "rate" for kind, _ in supported)
        if tokens and len(supported) == len(tokens):
            counts["facts_all_tokens_supported_native_exact"] += 1
        elif supported:
            counts["facts_partial_tokens_supported_native_exact"] += 1
        else:
            counts["facts_no_tokens_supported_native_exact"] += 1

    s7_paths = sorted(STRUCTURED.glob("*/s7_hybrid-table-v1/*.clauses.json"))
    if len(s7_paths) != 1367:
        raise AuditError(f"expected 1367 S7 clause files, got {len(s7_paths)}")
    s7_candidate_ids: set[str] = set()
    accepted_clause_mismatch = 0
    s7_problems = Counter()
    for s7_path in s7_paths:
        s7 = load(s7_path)
        insurer = s7_path.parent.parent.name
        sha12 = s7_path.name.removesuffix(".clauses.json")
        s6_path = unique(STRUCTURED / insurer, f"s6_pymupdf-*/{sha12}.clauses.json")
        s6 = load(s6_path)
        accepted_clause_mismatch += s7.get("clauses") != s6.get("clauses")
        release = s7.get("release_state") or {}
        s7_problems["release_not_candidate"] += release.get("approval") != "candidate"
        s7_problems["release_serving_leak"] += release.get("serving_eligible") is not False
        s7_problems["release_citation_leak"] += release.get("citation_eligible") is not False
        source_sha = str((s7.get("source") or {}).get("sha256") or "")
        for fact in s7.get("candidate_facts") or []:
            fact_id = str(fact.get("candidate_id") or "")
            if fact_id in s7_candidate_ids:
                s7_problems["duplicate_candidate_id"] += 1
            s7_candidate_ids.add(fact_id)
            s7_problems["fact_document_sha"] += fact.get("document_sha256") != source_sha
            s7_problems["fact_approval"] += fact.get("approval") != "candidate"
            s7_problems["fact_serving_leak"] += fact.get("serving_eligible") is not False
            s7_problems["fact_citation_leak"] += fact.get("citation_eligible") is not False

    critical = {
        "candidate_ids_match_s7": candidate_ids == s7_candidate_ids,
        "accepted_clauses_unchanged": accepted_clause_mismatch == 0,
        "candidate_serving_eligible_zero": counts["serving_eligible"] == 0,
        "candidate_citation_eligible_zero": counts["citation_eligible"] == 0,
        "table_bbox_invalid_zero": counts["invalid_table_bbox"] == 0,
        "s7_problems_zero": not any(s7_problems.values()),
    }
    s7_page_paths = sorted(EXTRACTED.glob("*/s6_hybrid-table-v1/*.json"))
    if len(s7_page_paths) != 1367:
        raise AuditError(f"expected 1367 S7 page files, got {len(s7_page_paths)}")
    result = {
        "schema_version": "s7-candidate-audit-v1",
        "candidate_documents": len(candidate_files),
        "candidate_count": len(candidates),
        "candidate_ids_unique": len(candidate_ids),
        "native_documents_checked": len(native_cache),
        "s7_documents_checked": len(s7_paths),
        "accepted_clause_mismatch": accepted_clause_mismatch,
        "critical_checks": critical,
        "counts": dict(sorted(counts.items())),
        "validation_status": dict(sorted(validation_status.items())),
        "validation_reasons": dict(sorted(validation_reasons.items())),
        "model_revisions": dict(sorted(revisions.items())),
        "candidates_by_insurer": dict(sorted(insurers.items())),
        "candidates_by_category": dict(sorted(categories.items())),
        "s7_problems": {key: value for key, value in sorted(s7_problems.items()) if value},
        "artifact_digests": {
            "candidate_payload_tree_sha256": tree_digest(candidate_files),
            "s7_page_tree_sha256": tree_digest(s7_page_paths),
            "s7_clause_tree_sha256": tree_digest(s7_paths),
            "accepted_release_config_sha256": sha256_file(ROOT / "config" / "accepted_extraction.json"),
        },
    }
    if not all(critical.values()):
        raise AuditError(f"critical S7 audit failed: {critical}; problems={dict(s7_problems)}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-root", type=Path, default=ROOT / "data" / "candidates" / "s7_selfpay")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "eval" / "s7_candidate_quality_summary.json")
    args = parser.parse_args()
    result = audit(args.candidate_root)
    atomic_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
