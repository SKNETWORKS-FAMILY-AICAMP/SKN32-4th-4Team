"""Compare clause-only KCD decisions with annex-reference shadow decisions.

This is an offline release gate.  It never mutates clause artifacts and every
row it writes is explicitly blocked from serving.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from app.core.domain import eligibility
from app.core.domain.kcd_ranges import CodeMention, CodeRef, KcdRange, judge, parse_ranges, scan_clause


ROOT = Path(__file__).resolve().parents[2]
STRUCTURED = ROOT / "data" / "structured"
SHADOW = ROOT / "data" / "eval" / "annex_shadow_s6"
OUTPUT = ROOT / "data" / "eval" / "annex_precheck_diff_s6"
_BASE_CODES = tuple(CodeRef(chr(letter), number) for letter in range(65, 91) for number in range(100))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            if row.get("release_state") != "shadow" or row.get("serving_eligible") is not False:
                raise AssertionError("annex precheck diff escaped the serving block")
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_shadow_mention(raw: dict[str, Any], row: dict[str, Any]) -> CodeMention:
    ranges = parse_ranges(raw.get("range") or "")
    if len(ranges) != 1:
        raise ValueError(
            f"expected one KCD range in {row.get('sha12')} annex {row.get('annex_ordinal')}: {raw!r}"
        )
    return CodeMention(
        range=ranges[0],
        kind=raw.get("kind") or "mention",
        context=(raw.get("context") or "")[:500],
    )


def safe_ref_reasons(
    row: dict[str, Any],
    *,
    owner: dict[str, Any] | None,
    usable_clause_ordinals: set[int],
) -> list[str]:
    """Return every reason that prevents a resolved annex ref entering safe mode."""
    reasons: list[str] = []
    if row.get("quarantined"):
        reasons.append("quarantined_document")
    if (row.get("ref") or {}).get("conditional"):
        reasons.append("conditional_reference")
    if owner is None:
        reasons.append("missing_owner")
    elif owner.get("owner_status") != "unique":
        reasons.append("ambiguous_owner")
    elif owner.get("owner_clause_ordinal") != row.get("clause_ordinal"):
        reasons.append("owner_clause_mismatch")
    if row.get("clause_ordinal") not in usable_clause_ordinals:
        reasons.append("origin_clause_not_usable")
    return reasons


def _candidate_codes(mentions: Iterable[CodeMention]) -> list[str]:
    ranges = [mention.range for mention in mentions]
    if not ranges:
        return []
    codes = {str(code) for code in _BASE_CODES if any(rng.contains(code) for rng in ranges)}
    # Base-code enumeration cannot represent exact subcodes such as N39.3.
    for rng in ranges:
        if rng.lo.sub is not None:
            codes.add(str(rng.lo))
        if rng.hi.sub is not None:
            codes.add(str(rng.hi))
        if (
            rng.lo.letter == rng.hi.letter
            and rng.lo.number == rng.hi.number
            and rng.lo.sub is not None
            and rng.hi.sub is not None
        ):
            for sub in range(rng.lo.sub, rng.hi.sub + 1):
                codes.add(str(CodeRef(rng.lo.letter, rng.lo.number, sub)))
    return sorted(codes)


def _ranges_overlap(left: KcdRange, right: KcdRange) -> bool:
    return (
        left.contains(right.lo)
        or left.contains(right.hi)
        or right.contains(left.lo)
        or right.contains(left.hi)
    )


def _annex_hits(code: str, items: list[tuple[CodeMention, dict[str, Any]]]) -> list[dict[str, Any]]:
    parsed = CodeRef.parse(code)
    if parsed is None:
        return []
    hits: list[dict[str, Any]] = []
    for mention, row in items:
        if mention.range.contains(parsed) and mention.kind in {"exclude", "exception"}:
            hits.append({
                "annex_ordinal": row.get("annex_ordinal"),
                "annex_label": row.get("annex_label"),
                "clause_ordinal": row.get("clause_ordinal"),
                "clause_qualified_no": row.get("clause_qualified_no"),
                "range": str(mention.range),
                "kind": mention.kind,
            })
    return hits


def compare_document(
    *,
    sha12: str,
    insurer_dir: str,
    doc: dict[str, Any],
    resolved_rows: list[dict[str, Any]],
    owner_by_annex: dict[int, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any], collections.Counter[str]]:
    parse_status = doc.get("parse_status")
    usable_clauses = [
        clause for clause in doc.get("clauses") or []
        if eligibility.check(clause, parse_status=parse_status).usable
    ]
    usable_ordinals = {clause.get("ordinal") for clause in usable_clauses}
    baseline = [mention for clause in usable_clauses for mention in scan_clause(clause.get("text") or "")]

    all_items: list[tuple[CodeMention, dict[str, Any]]] = []
    nonquarantine_items: list[tuple[CodeMention, dict[str, Any]]] = []
    structural_safe_items: list[tuple[CodeMention, dict[str, Any]]] = []
    safe_items: list[tuple[CodeMention, dict[str, Any]]] = []
    excluded = collections.Counter()
    structural_safe_refs = 0
    safe_refs = 0
    for row in resolved_rows:
        owner = owner_by_annex.get(row.get("annex_ordinal"))
        reasons = safe_ref_reasons(row, owner=owner, usable_clause_ordinals=usable_ordinals)
        if reasons:
            excluded.update(reasons)
        else:
            structural_safe_refs += 1
        parsed_items: list[tuple[CodeMention, dict[str, Any]]] = []
        for raw in row.get("mentions") or []:
            item = (_parse_shadow_mention(raw, row), row)
            parsed_items.append(item)
            all_items.append(item)
            if not row.get("quarantined"):
                nonquarantine_items.append(item)
            if not reasons:
                structural_safe_items.append(item)
        if reasons or not parsed_items:
            continue
        # A resolved reference points at an annex, but the annex can contain
        # several independent disease/injury subsections.  Owner uniqueness
        # alone therefore cannot authorize every KCD range in the annex.  Keep
        # only ranges bounded by an explicit KCD range in the local reference
        # context until subsection-level annex spans exist.
        context_ranges = parse_ranges(((row.get("ref") or {}).get("context") or ""))
        scoped = [
            item for item in parsed_items
            if any(_ranges_overlap(item[0].range, context_range) for context_range in context_ranges)
        ]
        if not context_ranges:
            excluded["unbounded_reference_code_scope"] += 1
        elif len(scoped) != len(parsed_items):
            excluded["annex_mentions_outside_reference_scope"] += len(parsed_items) - len(scoped)
        if scoped:
            safe_refs += 1
            safe_items.extend(scoped)

    all_mentions = baseline + [item[0] for item in all_items]
    nonquarantine_mentions = baseline + [item[0] for item in nonquarantine_items]
    structural_safe_mentions = baseline + [item[0] for item in structural_safe_items]
    safe_mentions = baseline + [item[0] for item in safe_items]
    codes = _candidate_codes(all_mentions)
    diffs: list[dict[str, Any]] = []
    transitions = collections.Counter()
    for code in codes:
        baseline_status = judge(code, baseline)["status"]
        all_status = judge(code, all_mentions)["status"]
        nonquarantine_status = judge(code, nonquarantine_mentions)["status"]
        structural_safe_status = judge(code, structural_safe_mentions)["status"]
        safe_status = judge(code, safe_mentions)["status"]
        if len({baseline_status, all_status, nonquarantine_status, structural_safe_status, safe_status}) == 1:
            continue
        transitions[f"all:{baseline_status}->{all_status}"] += 1
        transitions[f"nonquarantine:{baseline_status}->{nonquarantine_status}"] += 1
        transitions[f"structural_safe:{baseline_status}->{structural_safe_status}"] += 1
        transitions[f"safe:{baseline_status}->{safe_status}"] += 1
        diffs.append({
            "schema_version": "annex-precheck-diff-v1",
            "release_state": "shadow",
            "serving_eligible": False,
            "sha12": sha12,
            "insurer_dir": insurer_dir,
            "source_sha256": (doc.get("source") or {}).get("sha256"),
            "code": code,
            "baseline_status": baseline_status,
            "all_resolved_status": all_status,
            "nonquarantine_status": nonquarantine_status,
            "structural_safe_status": structural_safe_status,
            "safe_status": safe_status,
            "all_annex_hits": _annex_hits(code, all_items),
            "safe_annex_hits": _annex_hits(code, safe_items),
        })

    document_summary = {
        "schema_version": "annex-precheck-document-summary-v1",
        "release_state": "shadow",
        "serving_eligible": False,
        "sha12": sha12,
        "insurer_dir": insurer_dir,
        "source_sha256": (doc.get("source") or {}).get("sha256"),
        "usable_clauses": len(usable_clauses),
        "baseline_mentions": len(baseline),
        "resolved_refs": len(resolved_rows),
        "structural_safe_refs": structural_safe_refs,
        "safe_refs": safe_refs,
        "all_annex_mentions": len(all_items),
        "nonquarantine_annex_mentions": len(nonquarantine_items),
        "structural_safe_annex_mentions": len(structural_safe_items),
        "safe_annex_mentions": len(safe_items),
        "changed_codes_any_mode": len(diffs),
        "changed_codes_safe": sum(row["baseline_status"] != row["safe_status"] for row in diffs),
        "safe_exclusion_reasons": dict(sorted(excluded.items())),
        "transitions": dict(sorted(transitions.items())),
    }
    return diffs, document_summary, transitions


def build(*, clause_tag: str, shadow_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    resolved = _read_jsonl(shadow_dir / "resolved.jsonl")
    owners = _read_jsonl(shadow_dir / "owners.jsonl")
    resolved_by_sha: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    owners_by_sha: dict[str, dict[int, dict[str, Any]]] = collections.defaultdict(dict)
    for row in resolved:
        resolved_by_sha[row["sha12"]].append(row)
    for row in owners:
        owners_by_sha[row["sha12"]][row["annex_ordinal"]] = row

    diff_rows: list[dict[str, Any]] = []
    document_rows: list[dict[str, Any]] = []
    counts = collections.Counter()
    transitions = collections.Counter()
    insurer_safe_changes = collections.Counter()
    files = sorted(STRUCTURED.glob(f"*/{clause_tag}/*.clauses.json"))
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("parse_status") != "ok":
            counts[f"skipped:{doc.get('parse_status') or 'unknown'}"] += 1
            continue
        sha12 = path.name.removesuffix(".clauses.json")
        insurer = path.parents[1].name
        rows, document, doc_transitions = compare_document(
            sha12=sha12,
            insurer_dir=insurer,
            doc=doc,
            resolved_rows=resolved_by_sha.get(sha12, []),
            owner_by_annex=owners_by_sha.get(sha12, {}),
        )
        diff_rows.extend(rows)
        document_rows.append(document)
        transitions.update(doc_transitions)
        counts["ok_documents"] += 1
        counts["documents_with_resolved"] += bool(resolved_by_sha.get(sha12))
        counts["documents_changed_any_mode"] += bool(rows)
        safe_changes = sum(row["baseline_status"] != row["safe_status"] for row in rows)
        counts["changed_codes_any_mode"] += len(rows)
        counts["changed_codes_safe"] += safe_changes
        counts["documents_changed_safe"] += bool(safe_changes)
        insurer_safe_changes[insurer] += safe_changes
        counts["baseline_mentions"] += document["baseline_mentions"]
        counts["resolved_refs"] += document["resolved_refs"]
        counts["structural_safe_refs"] += document["structural_safe_refs"]
        counts["safe_refs"] += document["safe_refs"]
        counts["all_annex_mentions"] += document["all_annex_mentions"]
        counts["structural_safe_annex_mentions"] += document["structural_safe_annex_mentions"]
        counts["safe_annex_mentions"] += document["safe_annex_mentions"]
        for reason, value in document["safe_exclusion_reasons"].items():
            counts[f"safe_excluded:{reason}"] += value

    summary = {
        "schema_version": "annex-precheck-diff-summary-v1",
        "clause_tag": clause_tag,
        "release_state": "shadow",
        "serving_eligible": False,
        "comparison_modes": {
            "baseline": "eligible clauses only",
            "all_resolved": "all resolved annex refs, including quarantine (diagnostic only)",
            "nonquarantine": "resolved annex refs except quarantined documents",
            "structural_safe": "nonquarantine, nonconditional, unique matching owner, usable origin clause",
            "safe": "structural-safe plus annex KCD range bounded by an explicit local reference-context KCD range",
        },
        "counts": dict(sorted(counts.items())),
        "transitions": dict(sorted(transitions.items())),
        "safe_changes_by_insurer": dict(sorted(insurer_safe_changes.items())),
    }
    return diff_rows, document_rows, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clause-tag", default="s6_pymupdf-1.28.0")
    parser.add_argument("--shadow-dir", type=Path, default=SHADOW)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT)
    args = parser.parse_args()
    diffs, documents, summary = build(clause_tag=args.clause_tag, shadow_dir=args.shadow_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {"diffs": diffs, "documents": documents}
    summary["files"] = {}
    for name, rows in outputs.items():
        path = args.output_dir / f"{name}.jsonl"
        count = _write_jsonl(path, rows)
        summary["files"][name] = {"rows": count, "sha256": _sha256(path)}
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
