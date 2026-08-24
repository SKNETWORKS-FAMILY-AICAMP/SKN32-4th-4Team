"""Load only human-approved S7.1 OCR facts into the active pgvector index."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
MODEL = "dragonkue/snowflake-arctic-embed-l-v2.0-ko"
REVISION = "55ec6e9358a56d56af759bc8372e970caf8c305f"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def _full_sha_by12() -> dict[str, str]:
    manifest = ROOT / "data/manifests/preprocess/manifest_s6.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    result = {}
    for row in payload.get("documents") or []:
        full = row.get("input_sha256") or ""
        if len(full) == 64:
            result.setdefault(full[:12], full)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", type=Path, default=ROOT / "data/work/s7_1_approved_facts")
    parser.add_argument(
        "--release",
        type=Path,
        default=ROOT / "data/eval/rerank_results/s7_1_qwen3_reranker_4b_release.json",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    manifest = json.loads((args.src / "manifest.json").read_text(encoding="utf-8"))
    release = json.loads(args.release.read_text(encoding="utf-8"))
    vector_meta = json.loads((args.src / "vectors.meta.json").read_text(encoding="utf-8"))
    facts = _rows(args.src / "approved_facts.jsonl")
    chunks = _rows(args.src / "chunks.jsonl")
    occurrences = _rows(args.src / "occurrences.jsonl")
    if manifest.get("schema_version") != "s7.1-approved-ocr-facts-v1":
        raise SystemExit("unexpected fact manifest")
    if release.get("schema_version") != "s7.1-reranker-release-v1" or release.get("candidate_facts_included") is not True:
        raise SystemExit("S7.1 release is missing or did not include approved facts")
    if release.get("input", {}).get("fact_manifest_sha256") != _sha(args.src / "manifest.json"):
        raise SystemExit("release/fact manifest SHA mismatch")
    expected = manifest.get("counts") or {}
    if expected.get("approved_facts") != 850 or expected.get("approved_contents") != 75 or expected.get("quarantined_patterns") != 5:
        raise SystemExit(f"unexpected approval counts: {expected}")
    if len(facts) != 850 or len(occurrences) != 850 or len(chunks) != 75:
        raise SystemExit("artifact cardinality mismatch")
    if any(row.get("approval") != "human_pattern_approved" for row in facts):
        raise SystemExit("non-approved fact reached loader")
    if any(not row.get("serving_eligible") or not row.get("citation_eligible") for row in facts):
        raise SystemExit("approved fact eligibility mismatch")
    if vector_meta.get("model") != MODEL or vector_meta.get("revision") != REVISION:
        raise SystemExit("embedding model provenance mismatch")
    vectors_path = args.src / "vectors.npz"
    if vector_meta.get("output_sha256") != _sha(vectors_path):
        raise SystemExit("embedding file SHA mismatch")

    with np.load(vectors_path, allow_pickle=False) as packed:
        vectors = packed["vectors"].astype(np.float32)
        hashes = packed["content_hash"].astype(str)
        seqs = packed["seq"].astype(np.int32)
        n_chunks = packed["n_chunks"].astype(np.int32)
    if vectors.shape != (75, 1024):
        raise SystemExit(f"unexpected vector shape: {vectors.shape}")
    chunk_hashes = [row["content_hash"] for row in chunks]
    if chunk_hashes != hashes.tolist() or [int(row["seq"]) for row in chunks] != seqs.tolist():
        raise SystemExit("chunk/vector row order mismatch")
    if not np.isfinite(vectors).all():
        raise SystemExit("non-finite vectors")

    from app.adapters import pgvector_clause_index as ix
    from app.adapters.pgvector_index import get_conn
    from app.core import release as accepted_release

    profile = accepted_release.current().embed_profile
    if profile.model != MODEL or profile.dim != 1024 or profile.chunk_budget != 448 or profile.overlap != 80:
        raise SystemExit("active embedding profile does not match S7.1 vectors")
    model_key = profile.key
    generation = ix.current_generation()
    full_sha = _full_sha_by12()
    fact_by_id = {row["candidate_id"]: row for row in facts}
    occurrence_rows = []
    for row in occurrences:
        fact = fact_by_id.get(row["candidate_id"])
        sha = full_sha.get(row["sha12"])
        if not fact or not sha or len(sha) != 64:
            raise SystemExit(f"occurrence provenance missing: {row.get('candidate_id')}")
        if not row.get("table_bbox") or not row.get("image_sha256"):
            raise SystemExit(f"citation locator missing: {row['candidate_id']}")
        service = ", ".join(fact.get("service") or [])
        title = f"자기부담금 표 · {fact.get('plan')} · {service}"
        occurrence_rows.append(
            (
                row["content_hash"], sha, row["insurer"],
                f"OCR표/{row['candidate_id'][7:19]}", "자기부담금 표", title,
                int(row["page_from"]), int(row["page_to"]),
                "approved_ocr_table_fact",
                {"citation_eligible": True, "chunk_type": "approved_ocr_fact",
                 "is_statute": False, "parse_status": "ok"},
            )
        )

    summary = {
        "generation": generation,
        "model_key": model_key,
        "contents": len(chunks),
        "occurrences": len(occurrence_rows),
        "documents": len({row[1] for row in occurrence_rows}),
        "release_payload_sha256": release.get("payload_sha256"),
    }
    if args.dry_run:
        print(json.dumps({**summary, "dry_run": True}, ensure_ascii=False))
        return 0

    with get_conn() as conn:
        ix.ensure_schema(conn)
        ix.upsert_content(conn, [(row["content_hash"], row["text"], 1) for row in chunks])
        ix.upsert_chunks(
            conn,
            [(h, int(seq), int(nc), row["text"], vec)
             for h, seq, nc, row, vec in zip(hashes, seqs, n_chunks, chunks, vectors, strict=True)],
            model=model_key,
        )
        ix.upsert_occurrences(conn, occurrence_rows, generation=generation)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM policy_clause_chunk WHERE content_hash = ANY(%s) AND embed_model = %s",
                (hashes.tolist(), model_key),
            )
            loaded_chunks = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*), count(DISTINCT sha256) FROM policy_clause_occurrence "
                "WHERE content_hash = ANY(%s) AND index_generation = %s",
                (hashes.tolist(), generation),
            )
            loaded_occurrences, loaded_documents = cur.fetchone()
    if loaded_chunks != 75 or loaded_occurrences != 850:
        raise SystemExit(
            f"post-load count mismatch: chunks={loaded_chunks}, occurrences={loaded_occurrences}"
        )
    print(json.dumps({**summary, "loaded_chunks": loaded_chunks,
                      "loaded_occurrences": loaded_occurrences,
                      "loaded_documents": loaded_documents, "dry_run": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
