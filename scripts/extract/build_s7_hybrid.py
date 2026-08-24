# -*- coding: utf-8 -*-
"""Build the full S7 hybrid-table candidate release from frozen s5/s6 artifacts.

The builder deliberately keeps accepted evidence and OCR-derived candidates in
different fields.  Creating S7 never makes an OCR fact serving/citation eligible
and never moves the mutable accepted release pointer.

    python -m scripts.extract.build_s7_hybrid
    python -m scripts.extract.build_s7_hybrid --verify
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
EXTRACTED = ROOT / "data" / "extracted"
STRUCTURED = ROOT / "data" / "structured"
DEFAULT_CANDIDATE_ROOT = ROOT / "data" / "candidates" / "s7_selfpay"

PAGE_INPUT_GLOB = "s5_pymupdf-*"
CLAUSE_INPUT_GLOB = "s6_pymupdf-*"
PAGE_TAG = "s6_hybrid-table-v1"
CLAUSE_TAG = "s7_hybrid-table-v1"
PAGE_SCHEMA_VERSION = "6"
CLAUSE_SCHEMA_VERSION = "7"
EXTRACTOR = "hybrid-table/v1"
SELECTION_RULE_VERSION = "s7-page-mode/1"
BUILDER_VERSION = "build_s7_hybrid/1"


class S7BuildError(RuntimeError):
    """Input lineage or output isolation is invalid."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def write_atomic(path: Path, value: Any, *, force: bool) -> str:
    body = canonical_bytes(value)
    if path.is_file():
        existing = path.read_bytes()
        if existing == body:
            return "unchanged"
        if not force:
            raise S7BuildError(
                f"refusing to overwrite different S7 artifact without --force: {path}"
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return "written"


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def unique_input(parent: Path, tag_glob: str, filename: str) -> Path:
    hits = sorted(parent.glob(f"{tag_glob}/{filename}"))
    if len(hits) != 1:
        raise S7BuildError(
            f"expected exactly one input for {parent.name}/{tag_glob}/{filename}, got {len(hits)}"
        )
    return hits[0]


def discover_pairs() -> list[tuple[str, str, Path, Path]]:
    pairs: list[tuple[str, str, Path, Path]] = []
    for page_path in sorted(EXTRACTED.glob(f"*/{PAGE_INPUT_GLOB}/*.json")):
        insurer = page_path.parent.parent.name
        sha12 = page_path.stem
        canonical_page = unique_input(EXTRACTED / insurer, PAGE_INPUT_GLOB, page_path.name)
        if canonical_page != page_path:
            continue
        clause_name = f"{sha12}.clauses.json"
        clause_path = unique_input(STRUCTURED / insurer, CLAUSE_INPUT_GLOB, clause_name)
        pairs.append((insurer, sha12, page_path, clause_path))
    return pairs


def load_candidates(root: Path) -> dict[str, list[dict[str, Any]]]:
    if not root.exists():
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(root.glob("*.json")):
        if path.name == "index.json":
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        sha12 = str(payload.get("sha12") or path.stem)
        candidates = payload.get("candidates") or []
        if not isinstance(candidates, list):
            raise S7BuildError(f"candidate list is not an array: {path}")
        result.setdefault(sha12, []).extend(candidates)
    return result


def normalize_candidate(raw: dict[str, Any], *, source_sha: str) -> dict[str, Any]:
    fact = dict(raw)
    candidate_id = str(fact.get("candidate_id") or fact.get("fact_id") or "")
    if not candidate_id.startswith("sha256:") or len(candidate_id) != 71:
        raise S7BuildError(f"invalid candidate id for {source_sha[:12]}: {candidate_id!r}")
    fact["candidate_id"] = candidate_id
    fact["fact_id"] = candidate_id
    fact["approval"] = "candidate"
    fact["serving_eligible"] = False
    fact["citation_eligible"] = False
    fact["document_sha256"] = source_sha
    fact["document_sha12"] = source_sha[:12]
    fact.setdefault("fact_type", "self_pay_rule")
    fact.setdefault("inferred", False)
    source = fact.get("source")
    if not isinstance(source, dict):
        raise S7BuildError(f"candidate source is missing for {candidate_id}")
    page = fact.get("page_1based")
    if not isinstance(page, int) or page < 1:
        raise S7BuildError(f"candidate page is invalid for {candidate_id}")
    return fact


def page_candidates(candidates: Iterable[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for candidate in candidates:
        grouped.setdefault(int(candidate["page_1based"]), []).append(candidate)
    for page in grouped:
        grouped[page].sort(key=lambda item: item["candidate_id"])
    return grouped


def select_page_mode(page: dict[str, Any], ocr_facts: list[dict[str, Any]]) -> dict[str, Any]:
    verified_line = [
        table
        for table in (page.get("tables_coords") or [])
        if table.get("method") == "선" and table.get("is_table") is not False
    ]
    native = page.get("tables") or []
    text = str(page.get("text") or "")
    available: list[str] = []
    if verified_line:
        available.append("verified_line_grid")
    if native:
        available.append("native_layout")
    if ocr_facts:
        available.append("ocr_candidate")
    if text.strip():
        available.append("text_only")
    extraction_failed = bool(page.get("table_extraction_failed"))
    if extraction_failed:
        available.append("failed")

    if verified_line:
        selected = "verified_line_grid"
        reason = "verified vector-line grid exists; preserves accepted table evidence"
        state = "accepted_evidence"
    elif native:
        selected = "native_layout"
        reason = "native layout table exists and no verified line grid exists"
        state = "accepted_evidence"
    elif ocr_facts:
        selected = "ocr_candidate"
        reason = "business candidate facts exist but require human approval"
        state = "candidate_only"
    elif extraction_failed:
        selected = "failed"
        reason = "native table extraction failed and no usable structure exists"
        state = "blocked"
    else:
        selected = "text_only"
        reason = "no table structure exists; page text is retained"
        state = "accepted_evidence"

    return {
        "rule_version": SELECTION_RULE_VERSION,
        "selected_mode": selected,
        "selection_reason": reason,
        "selected_output_state": state,
        "available_modes": available,
        "verified_line_table_ids": [table.get("table_id") for table in verified_line],
        "native_table_count": len(native),
        "ocr_candidate_fact_ids": [fact["candidate_id"] for fact in ocr_facts],
    }


def promote_page_doc(
    page_doc: dict[str, Any],
    *,
    page_path: Path,
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], Counter]:
    source_sha = str((page_doc.get("source") or {}).get("sha256") or "")
    if len(source_sha) != 64 or not page_path.stem == source_sha[:12]:
        raise S7BuildError(f"page source SHA mismatch: {page_path}")
    if str(page_doc.get("schema_version")) != "5":
        raise S7BuildError(f"page input is not schema 5: {page_path}")

    by_page = page_candidates(candidates)
    mode_counts: Counter = Counter()
    pages: list[dict[str, Any]] = []
    valid_pages = {int(item.get("page")) for item in page_doc.get("pages") or []}
    unknown_pages = sorted(set(by_page) - valid_pages)
    if unknown_pages:
        raise S7BuildError(f"candidate page outside document {source_sha[:12]}: {unknown_pages}")
    for original in page_doc.get("pages") or []:
        page = dict(original)
        decision = select_page_mode(page, by_page.get(int(page["page"]), []))
        page["hybrid_extraction"] = decision
        mode_counts[decision["selected_mode"]] += 1
        pages.append(page)

    stats = dict(page_doc.get("stats") or {})
    stats["hybrid_mode_pages"] = dict(sorted(mode_counts.items()))
    stats["candidate_facts"] = len(candidates)
    return {
        **page_doc,
        "schema_version": PAGE_SCHEMA_VERSION,
        "extractor": EXTRACTOR,
        "upstream": {
            "schema_version": str(page_doc.get("schema_version")),
            "extractor": page_doc.get("extractor"),
            "path": relative(page_path),
            "sha256": sha256_file(page_path),
        },
        "hybrid_contract": {
            "builder": BUILDER_VERSION,
            "selection_rule_version": SELECTION_RULE_VERSION,
            "approval": "candidate",
            "candidate_serving_eligible": False,
            "candidate_citation_eligible": False,
        },
        "stats": stats,
        "pages": pages,
    }, mode_counts


def promote_clause_doc(
    clause_doc: dict[str, Any],
    *,
    clause_path: Path,
    page_output_path: Path,
    page_output: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    source_sha = str((clause_doc.get("source") or {}).get("sha256") or "")
    if len(source_sha) != 64 or clause_path.name != f"{source_sha[:12]}.clauses.json":
        raise S7BuildError(f"clause source SHA mismatch: {clause_path}")
    if str(clause_doc.get("schema_version")) != "6":
        raise S7BuildError(f"clause input is not schema 6: {clause_path}")
    if source_sha != str((page_output.get("source") or {}).get("sha256") or ""):
        raise S7BuildError(f"page/clause source mismatch: {source_sha[:12]}")

    stats = dict(clause_doc.get("stats") or {})
    stats["candidate_facts"] = len(candidates)
    return {
        **clause_doc,
        "schema_version": CLAUSE_SCHEMA_VERSION,
        "extractor": EXTRACTOR,
        "release_state": {
            "approval": "candidate",
            "serving_eligible": False,
            "citation_eligible": False,
            "reason": "S7 extraction requires validation and explicit human release approval",
        },
        "upstream": {
            "page": {
                "schema_version": PAGE_SCHEMA_VERSION,
                "path": relative(page_output_path),
                "sha256": hashlib.sha256(canonical_bytes(page_output)).hexdigest(),
            },
            "clause": {
                "schema_version": str(clause_doc.get("schema_version")),
                "extractor": clause_doc.get("extractor"),
                "path": relative(clause_path),
                "sha256": sha256_file(clause_path),
            },
        },
        "hybrid_contract": {
            "builder": BUILDER_VERSION,
            "selection_rule_version": SELECTION_RULE_VERSION,
            "accepted_evidence_unchanged": True,
            "candidate_fact_count": len(candidates),
        },
        "stats": stats,
        "candidate_facts": candidates,
    }


def verify_output(page_path: Path, clause_path: Path) -> Counter:
    page_doc = json.loads(page_path.read_text(encoding="utf-8"))
    clause_doc = json.loads(clause_path.read_text(encoding="utf-8"))
    problems: Counter = Counter()
    if str(page_doc.get("schema_version")) != PAGE_SCHEMA_VERSION:
        problems["page_schema"] += 1
    if str(clause_doc.get("schema_version")) != CLAUSE_SCHEMA_VERSION:
        problems["clause_schema"] += 1
    page_sha = str((page_doc.get("source") or {}).get("sha256") or "")
    clause_sha = str((clause_doc.get("source") or {}).get("sha256") or "")
    if page_sha != clause_sha or page_path.stem != page_sha[:12]:
        problems["source_sha"] += 1
    if (clause_doc.get("release_state") or {}).get("approval") != "candidate":
        problems["release_state"] += 1
    for fact in clause_doc.get("candidate_facts") or []:
        if fact.get("approval") != "candidate":
            problems["fact_approval"] += 1
        if fact.get("serving_eligible") is not False:
            problems["fact_serving_leak"] += 1
        if fact.get("citation_eligible") is not False:
            problems["fact_citation_leak"] += 1
        if fact.get("document_sha256") != clause_sha:
            problems["fact_source_sha"] += 1
    return problems


def build(*, candidate_root: Path, limit: int, force: bool, verify_only: bool) -> dict[str, Any]:
    pairs = discover_pairs()
    if len(pairs) != 1367:
        raise S7BuildError(f"expected 1,367 frozen input pairs, got {len(pairs)}")
    selected = pairs[:limit] if limit else pairs
    candidate_map = load_candidates(candidate_root)
    known_shas = {sha12 for _, sha12, _, _ in pairs}
    unknown_candidate_docs = sorted(set(candidate_map) - known_shas)
    if unknown_candidate_docs:
        raise S7BuildError(f"candidate documents outside frozen corpus: {unknown_candidate_docs[:10]}")

    counts: Counter = Counter()
    modes: Counter = Counter()
    problems: Counter = Counter()
    for index, (insurer, sha12, page_in, clause_in) in enumerate(selected, 1):
        page_out = EXTRACTED / insurer / PAGE_TAG / f"{sha12}.json"
        clause_out = STRUCTURED / insurer / CLAUSE_TAG / f"{sha12}.clauses.json"
        if verify_only:
            if not page_out.is_file() or not clause_out.is_file():
                problems["missing_output"] += 1
            else:
                problems.update(verify_output(page_out, clause_out))
            continue

        page_doc = json.loads(page_in.read_text(encoding="utf-8"))
        clause_doc = json.loads(clause_in.read_text(encoding="utf-8"))
        source_sha = str((page_doc.get("source") or {}).get("sha256") or "")
        candidates = [
            normalize_candidate(item, source_sha=source_sha)
            for item in candidate_map.get(sha12, [])
        ]
        candidates.sort(key=lambda item: item["candidate_id"])
        promoted_page, doc_modes = promote_page_doc(
            page_doc, page_path=page_in, candidates=candidates
        )
        promoted_clause = promote_clause_doc(
            clause_doc,
            clause_path=clause_in,
            page_output_path=page_out,
            page_output=promoted_page,
            candidates=candidates,
        )
        counts[f"page_{write_atomic(page_out, promoted_page, force=force)}"] += 1
        counts[f"clause_{write_atomic(clause_out, promoted_clause, force=force)}"] += 1
        counts["candidate_facts"] += len(candidates)
        modes.update(doc_modes)
        if index % 50 == 0 or index == len(selected):
            print(f"S7 {index:,}/{len(selected):,}", flush=True)

    result = {
        "input_pairs": len(pairs),
        "selected_pairs": len(selected),
        "page_tag": PAGE_TAG,
        "clause_tag": CLAUSE_TAG,
        "candidate_root": relative(candidate_root) if candidate_root.is_relative_to(ROOT) else str(candidate_root),
        "counts": dict(sorted(counts.items())),
        "page_modes": dict(sorted(modes.items())),
        "problems": dict(sorted(problems.items())),
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if problems:
        raise S7BuildError(f"S7 verification failed: {dict(problems)}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the full S7 hybrid candidate release")
    parser.add_argument("--candidate-root", type=Path, default=DEFAULT_CANDIDATE_ROOT)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    build(
        candidate_root=args.candidate_root,
        limit=args.limit,
        force=args.force,
        verify_only=args.verify,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
