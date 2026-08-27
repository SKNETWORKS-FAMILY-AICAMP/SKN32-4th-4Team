"""Audit whether external table labels still identify this corpus and pages."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT.parent / "_3rd_project_4/ai-1/table_true_false.json"
IMPORTED = ROOT / "data/eval/table_labels.jsonl"
OUTPUT = ROOT / "data/eval/b7_external_table_label_provenance.json"
LINE = re.compile(r'"(true|check|false)"\s*:\s*\[(.*?)\]?\s*$')
OBJECT = re.compile(r"\{.*?\}")


def _source_rows() -> list[tuple[str, str, int]]:
    result = []
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        match = LINE.match(line.strip())
        if not match:
            continue
        for object_match in OBJECT.finditer(match.group(2).rstrip("]")):
            row = json.loads(object_match.group(0))
            result.append((match.group(1), row.get("sha12") or "", int(row.get("page") or 0)))
    return result


def main() -> int:
    imported_payload = [json.loads(line) for line in IMPORTED.read_text(encoding="utf-8").splitlines() if line.strip()]
    meta = imported_payload[0]
    imported = [(row["label"], row["sha12"], int(row["page"])) for row in imported_payload[1:]]
    current = _source_rows()
    extracted = {p.name.split(".")[0]: p for p in (ROOT / "data/extracted").rglob("s5_pymupdf-1.28.0/*.json")}
    manifest = json.loads((ROOT / "data/manifests/preprocess/manifest_s6.json").read_text(encoding="utf-8"))
    manifest_sha12 = {
        row["input_sha256"][:12]
        for row in manifest.get("documents") or []
        if len(row.get("input_sha256") or "") == 64
    }
    missing_documents = []
    missing_pages = []
    missing_manifest_hashes = []
    for _, sha12, page in sorted(set(imported)):
        path = extracted.get(sha12)
        if not path:
            missing_documents.append([sha12, page])
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if not any(int(row.get("page") or 0) == page for row in document.get("pages") or []):
            missing_pages.append([sha12, page])
        if sha12 not in manifest_sha12:
            missing_manifest_hashes.append(sha12)
    current_sha = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    result = {
        "schema_version": "b7-external-table-label-provenance-v1",
        "source": str(SOURCE),
        "imported_source_sha256": meta.get("source_sha256"),
        "current_source_sha256": current_sha,
        "source_byte_identical": current_sha == meta.get("source_sha256"),
        "semantic_label_set_identical": set(current) == set(imported),
        "current_raw_rows": len(current),
        "current_unique_rows": len(set(current)),
        "imported_unique_rows": len(set(imported)),
        "current_s5_documents": len(extracted),
        "manifest_documents": len(manifest_sha12),
        "missing_documents": missing_documents,
        "missing_pages": missing_pages,
        "missing_manifest_hashes": sorted(set(missing_manifest_hashes)),
        "verdict": "same_document_revision_and_semantic_labels"
        if set(current) == set(imported) and not missing_documents and not missing_pages and not missing_manifest_hashes
        else "mismatch",
        "note": "외부 라벨 파일의 서식/중복이 바뀌어 바이트 SHA는 다르지만 고유 라벨·문서 SHA·페이지는 동일하다.",
    }
    if result["verdict"] == "mismatch":
        raise SystemExit(json.dumps(result, ensure_ascii=False))
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
