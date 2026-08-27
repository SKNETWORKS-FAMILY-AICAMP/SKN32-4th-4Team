"""Independent release check for the S7-backed chatbot path.

This verifier does not start the API or mutate data. It checks the accepted
release, the three S7 serving artifacts, quarantine filtering, and the actual
glossary adapter output.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main() -> int:
    accepted = json.loads((ROOT / "config" / "accepted_extraction.json").read_text(encoding="utf-8"))
    supplemental = json.loads((ROOT / accepted["supplemental_facts"]).read_text(encoding="utf-8"))
    configured_root = (os.getenv("S7_FACT_ROOT") or "").strip()
    facts_dir = Path(configured_root) if configured_root else ROOT / "data" / "work" / "s7_1_approved_facts"
    facts_path = facts_dir / "approved_facts.jsonl"
    chunks_path = facts_dir / "chunks.jsonl"
    occurrences_path = facts_dir / "occurrences.jsonl"
    required = [facts_path, chunks_path, occurrences_path]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        print("FAIL missing S7 artifacts:")
        print("\n".join(missing))
        return 1

    facts = [json.loads(line) for line in facts_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    approved = [f for f in facts if f.get("serving_eligible") and f.get("citation_eligible")]
    quarantined = [f for f in facts if not (f.get("serving_eligible") and f.get("citation_eligible"))]
    chunks = {json.loads(line).get("content_hash") for line in chunks_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    occurrences = [json.loads(line) for line in occurrences_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    approved_hashes = {f.get("content_hash") for f in approved}
    served_occurrences = [o for o in occurrences if o.get("content_hash") in approved_hashes and o.get("content_hash") in chunks]

    os.environ.setdefault("S7_FACT_ROOT", str(facts_dir))
    from app.adapters import file_glossary_source

    file_glossary_source._reset_for_tests()
    rows = file_glossary_source._load()
    s7_rows = [r for r in rows if r.kind == "s7_approved_fact"]
    from app.core.usecases import glossary

    fact_answer = glossary.explain("자기부담금", source=file_glossary_source)
    definition_answer = glossary.explain("통원", source=file_glossary_source)
    checks = {
        "accepted_release": accepted.get("release_id"),
        "accepted_clause_generation": accepted.get("clause_tag"),
        "approved_facts": len(approved),
        "quarantined_facts": len(quarantined),
        "served_occurrences": len(served_occurrences),
        "chatbot_s7_passages": len(s7_rows),
        "approved_fact_selected_for_fact_question": any(
            quote.kind == "s7_approved_fact" for quote in fact_answer.quotes
        ),
        "term_definition_found": definition_answer.found,
        "amount_fact_not_used_as_term_definition": all(
            quote.kind != "s7_approved_fact" for quote in definition_answer.quotes
        ),
        "quarantine_not_served": not any(r.content_hash not in approved_hashes for r in s7_rows),
        "meta_s7_serving": file_glossary_source.meta().get("s7_serving") is True,
        "release_is_accepted": supplemental.get("release_state") == "accepted",
        "release_is_serving_eligible": supplemental.get("serving_eligible") is True,
        "approved_count_matches_release": len(approved) == supplemental["approval"]["approved_facts"],
        "content_count_matches_release": len(chunks) == supplemental["materialized"]["contents"],
        "occurrence_count_matches_release": len(served_occurrences) == supplemental["materialized"]["occurrences"],
    }
    for key, value in checks.items():
        print(f"{key}: {value}")
    failed = [key for key, value in checks.items() if value is False]
    failed.extend(key for key in ("approved_facts", "served_occurrences", "chatbot_s7_passages") if not checks[key])
    if failed:
        print("FAIL: " + ", ".join(failed))
        return 1
    print("PASS S7 chatbot release path")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
