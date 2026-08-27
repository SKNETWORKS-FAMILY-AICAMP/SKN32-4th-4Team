"""Materialize annex-reference resolution as a serving-blocked shadow artifact."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Any

from app.core.domain.annex_refs import AnnexResolution, resolve


ROOT = Path(__file__).resolve().parents[2]
STRUCTURED = ROOT / "data" / "structured"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ref(ref: Any) -> dict[str, Any]:
    return {
        "raw": ref.raw,
        "kind": ref.kind,
        "number": ref.number,
        "title": ref.title,
        "at": ref.at,
        "scope": ref.scope,
        "context": ref.context,
        "looks_statute": ref.looks_statute,
        "conditional": ref.conditional,
        "bracketed": ref.bracketed,
    }


def _mention(mention: Any) -> dict[str, str]:
    return {"range": str(mention.range), "kind": mention.kind, "context": mention.context}


def materialize_document(
    *,
    insurer_dir: str,
    sha12: str,
    doc: dict[str, Any],
    quarantined: bool,
    quarantine_reason: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    result: AnnexResolution = resolve(doc.get("clauses") or [], doc.get("annexes") or [])
    common = {
        "schema_version": "annex-ref-shadow-v1",
        "release_state": "shadow",
        "serving_eligible": False,
        "insurer_dir": insurer_dir,
        "sha12": sha12,
        "source_sha256": (doc.get("source") or {}).get("sha256"),
        "quarantined": quarantined,
        "quarantine_reason": quarantine_reason,
    }
    resolved_rows: list[dict[str, Any]] = []
    unresolved_rows: list[dict[str, Any]] = []
    owner_map: dict[int, dict[int, list[int]]] = collections.defaultdict(lambda: collections.defaultdict(list))

    for item in result.resolved:
        mentions = [_mention(mention) for mention in item.mentions]
        if item.ref.conditional and any(mention["kind"] != "mention" for mention in mentions):
            raise ValueError(f"conditional ref promoted in {sha12} clause {item.clause_ordinal}")
        resolved_rows.append({
            **common,
            "clause_ordinal": item.clause_ordinal,
            "clause_qualified_no": item.clause_qualified_no,
            "annex_ordinal": item.annex_ordinal,
            "annex_label": item.annex_label,
            "match_rule": item.match_rule,
            "ref": _ref(item.ref),
            "mentions": mentions,
        })
        owner_map[item.annex_ordinal][item.clause_ordinal].append(item.ref.at)

    for item in result.unresolved:
        unresolved_rows.append({
            **common,
            "clause_ordinal": item.clause_ordinal,
            "clause_qualified_no": item.clause_qualified_no,
            "reason": item.reason,
            "candidates": list(item.candidates),
            "ref": _ref(item.ref),
        })

    owner_rows: list[dict[str, Any]] = []
    annex_by_ordinal = {annex.get("ordinal", index): annex for index, annex in enumerate(doc.get("annexes") or [])}
    for annex_ordinal, owners in sorted(owner_map.items()):
        annex = annex_by_ordinal.get(annex_ordinal) or {}
        ordinals = sorted(owners)
        owner_rows.append({
            **common,
            "annex_ordinal": annex_ordinal,
            "annex_label": annex.get("label", ""),
            "owner_status": "unique" if len(ordinals) == 1 else "ambiguous",
            "owner_clause_ordinal": ordinals[0] if len(ordinals) == 1 else None,
            "owner_clause_candidates": ordinals,
            "reference_offsets_by_clause": {str(key): value for key, value in sorted(owners.items())},
        })
    return resolved_rows, unresolved_rows, owner_rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build(*, clause_tag: str, quarantine: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    all_resolved: list[dict[str, Any]] = []
    all_unresolved: list[dict[str, Any]] = []
    all_owners: list[dict[str, Any]] = []
    counts: collections.Counter[str] = collections.Counter()
    docs_with: dict[str, set[str]] = collections.defaultdict(set)

    files = sorted(STRUCTURED.glob(f"*/{clause_tag}/*.clauses.json"))
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("parse_status") != "ok":
            counts[f"skipped:{doc.get('parse_status') or 'unknown'}"] += 1
            continue
        insurer = path.parents[1].name
        sha12 = path.name.removesuffix(".clauses.json")
        q = quarantine.get(sha12) or {}
        resolved_rows, unresolved_rows, owner_rows = materialize_document(
            insurer_dir=insurer,
            sha12=sha12,
            doc=doc,
            quarantined=bool(q),
            quarantine_reason=q.get("reason", ""),
        )
        all_resolved.extend(resolved_rows)
        all_unresolved.extend(unresolved_rows)
        all_owners.extend(owner_rows)
        counts["ok_documents"] += 1
        counts["resolved_refs"] += len(resolved_rows)
        counts["unresolved_refs"] += len(unresolved_rows)
        counts["owner_rows"] += len(owner_rows)
        if resolved_rows:
            docs_with["resolved"].add(sha12)
        if q and (resolved_rows or unresolved_rows):
            docs_with["quarantined"].add(sha12)
        for row in resolved_rows:
            if row["ref"]["conditional"]:
                counts["conditional_refs"] += 1
            for mention in row["mentions"]:
                counts["code_mentions"] += 1
                counts[f"mention_kind:{mention['kind']}"] += 1
        for row in unresolved_rows:
            counts[f"unresolved_reason:{row['reason']}"] += 1
        for row in owner_rows:
            counts[f"owner_status:{row['owner_status']}"] += 1

    for row in all_resolved + all_unresolved + all_owners:
        if row["serving_eligible"] is not False or row["release_state"] != "shadow":
            raise AssertionError("annex shadow row escaped serving block")
    summary = {
        "schema_version": "annex-ref-shadow-summary-v1",
        "clause_tag": clause_tag,
        "release_state": "shadow",
        "serving_eligible": False,
        "counts": dict(sorted(counts.items())),
        "documents_with_resolved": len(docs_with["resolved"]),
        "documents_quarantined": len(docs_with["quarantined"]),
        "quarantine_sha12": sorted(docs_with["quarantined"]),
    }
    return {"resolved": all_resolved, "unresolved": all_unresolved, "owners": all_owners}, summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clause-tag", default="s6_pymupdf-1.28.0")
    parser.add_argument("--quarantine", type=Path, default=ROOT / "config/annex_ref_quarantine.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/eval/annex_shadow_s6")
    args = parser.parse_args()
    quarantine = json.loads(args.quarantine.read_text(encoding="utf-8"))
    rows, summary = build(clause_tag=args.clause_tag, quarantine=quarantine)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, values in rows.items():
        _write_jsonl(args.output_dir / f"{name}.jsonl", values)
    summary["files"] = {
        name: {
            "rows": len(values),
            "sha256": _sha256(args.output_dir / f"{name}.jsonl"),
        }
        for name, values in rows.items()
    }
    summary_path = args.output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
