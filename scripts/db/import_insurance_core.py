# -*- coding: utf-8 -*-
"""보험 원천 산출물을 ``insurance_real.core``에 넣기 위한 안전한 importer.

기본 동작은 dry-run이다. 현재 저장소의 원천 데이터는 식별·승인 전 상태이므로
``--apply``를 주더라도 사람 검수 상태를 우회하지 않는다. 승인된 산출물만
``--identified-by``를 지정해 멱등 이관할 수 있다.

사용:
    python -m scripts.db.import_insurance_core
    python -m scripts.db.import_insurance_core --dsn "$env:PG_DSN" --apply \
        --identified-by <ops.admin_user UUID>
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STRUCTURED = ROOT / "data" / "structured" / "dbins" / "s7_hybrid-table-v1"
DEFAULT_MANIFESTS = ROOT / "data" / "raw" / "manifests"
ALLOWED_LINES = {"standard", "senior", "simplified_issue", "travel", "unknown"}
ALLOWED_DATE_CONFIDENCE = {"exact", "month", "unknown"}


@dataclass(frozen=True)
class Manifest:
    path: Path
    row: dict[str, Any]


@dataclass(frozen=True)
class Document:
    path: Path
    row: dict[str, Any]
    manifest: Manifest


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest_index(manifest_dir: Path) -> dict[str, list[Manifest]]:
    index: dict[str, list[Manifest]] = {}
    for path in sorted(manifest_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                sha256 = str(row.get("sha256") or "")
                if len(sha256) != 64:
                    continue
                index.setdefault(sha256, []).append(Manifest(path, row))
    return index


def load_documents(structured_dir: Path, manifest_dir: Path) -> list[Document]:
    manifests = load_manifest_index(manifest_dir)
    documents: list[Document] = []
    for path in sorted(structured_dir.glob("*.clauses.json")):
        row = _json(path)
        source = row.get("source") or {}
        sha256 = str(source.get("sha256") or "")
        candidates = manifests.get(sha256) or []
        if not candidates:
            raise ValueError(f"구조화 문서의 원천 manifest가 없다: {path} ({sha256})")
        manifest = max(
            candidates,
            key=lambda candidate: (
                int(candidate.row.get("product_code") == source.get("product_code")),
                int(candidate.row.get("product_name") == source.get("product_name")),
                int(candidate.row.get("url") == source.get("url")),
                candidate.path.name,
            ),
        )
        documents.append(Document(path, row, manifest))
    return documents


def _release_blockers(document: Document) -> list[str]:
    row = document.row
    source = row.get("source") or {}
    manifest = document.manifest.row
    release = row.get("release_state") or {}
    blockers: list[str] = []
    if source.get("sha256") != manifest.get("sha256"):
        blockers.append("sha256_mismatch")
    if source.get("insurer") != manifest.get("insurer"):
        blockers.append("insurer_mismatch")
    if _source_file(document) is None:
        blockers.append("source_file_missing")
    if row.get("parse_status") != "ok":
        blockers.append(f"parse_status:{row.get('parse_status')}")
    if row.get("identification") != "confirmed" or manifest.get("identification") != "confirmed":
        blockers.append("identification_not_confirmed")
    if release.get("approval") != "accepted":
        blockers.append(f"release_approval:{release.get('approval', '<missing>')}")
    return blockers


def summarize(documents: list[Document]) -> dict[str, Any]:
    blockers: dict[str, int] = {}
    clauses = 0
    annexes = 0
    contents: set[str] = set()
    for document in documents:
        for blocker in _release_blockers(document):
            blockers[blocker] = blockers.get(blocker, 0) + 1
        clauses += len(document.row.get("clauses") or [])
        annexes += len(document.row.get("annexes") or [])
        for item in (document.row.get("clauses") or []) + (document.row.get("annexes") or []):
            content_hash = str(item.get("content_hash") or "")
            if content_hash:
                contents.add(content_hash)
    return {
        "structured_documents": len(documents),
        "clauses": clauses,
        "annexes": annexes,
        "unique_contents": len(contents),
        "ready": not blockers,
        "blockers": dict(sorted(blockers.items())),
    }


def _date_confidence(value: Any) -> str:
    return value if value in ALLOWED_DATE_CONFIDENCE else "unknown"


def _date(value: Any):
    if not value:
        return None
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    if len(text) == 6 and text.isdigit():
        return datetime.strptime(text, "%Y%m").date().replace(day=1)
    return None


def _product_line(value: Any) -> str:
    return value if value in ALLOWED_LINES else "unknown"


def _source_file(document: Document) -> Path | None:
    saved_as = str(document.manifest.row.get("saved_as") or "")
    if not saved_as:
        return None
    path = ROOT / saved_as
    return path if path.is_file() else None


def _slug(document: Document) -> str:
    return document.manifest.path.stem


def _kind(slug: str, legal_name: str) -> str:
    return "life" if slug.endswith("life") or "생명" in legal_name else "general"


def _version_label(source: dict[str, Any]) -> str:
    code = str(source.get("product_code") or "unknown")
    start = str(source.get("sale_start") or "unknown")
    sha = str(source.get("sha256") or "")[:12]
    return f"{code}@{start}:{sha}"


def _jsonb(value: Any):
    from psycopg.types.json import Jsonb

    return Jsonb(value)


def _insert_document(conn, document: Document, identified_by: str) -> tuple[str, str, str]:
    """문서·추출·상품·버전을 만들고 해당 ID를 반환한다."""
    source = document.row["source"]
    manifest = document.manifest.row
    slug = _slug(document)
    insurer = conn.execute(
        "INSERT INTO core.insurer(slug,legal_name,display_name,kind) "
        "VALUES (%s,%s,%s,%s) ON CONFLICT (slug) DO UPDATE SET "
        "legal_name=EXCLUDED.legal_name,display_name=EXCLUDED.display_name "
        "RETURNING id",
        (slug, source["insurer"], source["insurer"], _kind(slug, source["insurer"])),
    ).fetchone()[0]
    fetched_at = manifest.get("fetched_at") or document.row.get("built_at")
    if not fetched_at:
        fetched_at = datetime.now(timezone.utc)
    document_id = conn.execute(
        "INSERT INTO core.confirmed_policy_document("
        "sha256,source_url,fetched_at,http_status,bytes,pages,insurer_id,"
        "identified_by,identified_at,identification_note,redistributable) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now(),%s,false) "
        "ON CONFLICT (sha256) DO UPDATE SET source_url=EXCLUDED.source_url,"
        "fetched_at=EXCLUDED.fetched_at,http_status=EXCLUDED.http_status,"
        "bytes=EXCLUDED.bytes,pages=EXCLUDED.pages,insurer_id=EXCLUDED.insurer_id "
        "RETURNING id",
        (
            source["sha256"], source["url"], fetched_at, manifest.get("http_status"),
            manifest.get("bytes"), (document.row.get("stats") or {}).get("pages"),
            insurer, identified_by, "imported from accepted S7 structured artifact",
        ),
    ).fetchone()[0]
    extraction = conn.execute(
        "INSERT INTO core.document_extraction("
        "confirmed_document_id,extractor,schema_version,parse_status,approval,"
        "numbering,parse_warnings,toc_pages,toc_page_count,extracted_at) "
        "VALUES (%s,%s,%s,%s,'accepted',%s,%s,%s,%s,%s) "
        "ON CONFLICT (confirmed_document_id,schema_version,extractor) DO UPDATE "
        "SET parse_status=EXCLUDED.parse_status,approval='accepted',"
        "parse_warnings=EXCLUDED.parse_warnings,toc_pages=EXCLUDED.toc_pages,"
        "toc_page_count=EXCLUDED.toc_page_count,extracted_at=EXCLUDED.extracted_at "
        "RETURNING id",
        (
            document_id, document.row.get("extractor", "hybrid-table/v1"),
            int(document.row.get("schema_version") or 0), document.row.get("parse_status"),
            document.row.get("numbering"), _jsonb(document.row.get("parse_warnings") or []),
            document.row.get("toc_pages") or [], len(document.row.get("toc_pages") or []),
            document.row.get("built_at") or datetime.now(timezone.utc),
        ),
    ).fetchone()[0]
    product_code = str(source.get("product_code") or "") or None
    product = conn.execute(
        "INSERT INTO core.product(insurer_id,product_code,name,line) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (product_code) DO UPDATE SET name=EXCLUDED.name,line=EXCLUDED.line "
        "RETURNING id",
        (insurer, product_code, source.get("product_name") or "unknown", _product_line(manifest.get("product_line"))),
    ).fetchone()[0]
    version = conn.execute(
        "INSERT INTO core.policy_version(confirmed_document_id,product_id,version_label,"
        "variant,valid_from,valid_to,sales_from,sales_to,date_confidence,generation,"
        "generation_source,generation_confidence) VALUES (%s,%s,%s,NULL,NULL,NULL,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (product_id,version_label) DO UPDATE SET confirmed_document_id=EXCLUDED.confirmed_document_id "
        "RETURNING id",
        (
            document_id, product, _version_label(source), _date(source.get("sale_start")),
            _date(source.get("sale_end")), _date(source.get("sale_start")),
            _date(source.get("sale_end")), _date_confidence(manifest.get("date_confidence")),
            manifest.get("generation") if str(manifest.get("generation", "")).isdigit() else None,
            manifest.get("generation_basis"),
            manifest.get("generation_confidence") if manifest.get("generation_confidence") in {"exact", "month", "unknown"} else "unknown",
        ),
    ).fetchone()[0]
    return str(document_id), str(extraction), str(version)


def _insert_clauses(conn, document: Document, extraction_id: str, version_id: str, document_id: str) -> tuple[int, int]:
    inserted_content = 0
    inserted_clause = 0
    release = document.row.get("release_state") or {}
    citeable = bool(release.get("citation_eligible") and document.row.get("citation_eligible"))
    items = [("clause", x) for x in document.row.get("clauses") or []]
    items += [("annex", x) for x in document.row.get("annexes") or []]
    for source_kind, item in items:
        paragraphs = item.get("paragraphs") or []
        content_hash = item["content_hash"]
        conn.execute(
            "INSERT INTO core.clause_content(content_hash,hash_version,title,body,char_length,"
            "paragraph_count,paragraphs) VALUES (%s,'s7-hybrid-table-v1',%s,%s,%s,%s,%s) "
            "ON CONFLICT (content_hash) DO UPDATE SET title=EXCLUDED.title,body=EXCLUDED.body,"
            "char_length=EXCLUDED.char_length,paragraph_count=EXCLUDED.paragraph_count,paragraphs=EXCLUDED.paragraphs",
            (content_hash, item.get("title") or item.get("label") or "", item.get("text") or "",
             int(item.get("char_length") or len(item.get("text") or "")), len(paragraphs), _jsonb(paragraphs)),
        )
        inserted_content += 1
        conn.execute(
            "INSERT INTO core.policy_clause(confirmed_document_id,document_extraction_id,"
            "policy_version_id,source_kind,ordinal,content_hash,qualified_no,section,clause_no,"
            "citation,kind,citeable,statute,paragraph_no_ambiguous,locator,table_count,tables_on_pages) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'unclassified',%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (document_extraction_id,source_kind,ordinal) DO UPDATE SET "
            "content_hash=EXCLUDED.content_hash,qualified_no=EXCLUDED.qualified_no,section=EXCLUDED.section,"
            "clause_no=EXCLUDED.clause_no,citation=EXCLUDED.citation,citeable=EXCLUDED.citeable,"
            "statute=EXCLUDED.statute,paragraph_no_ambiguous=EXCLUDED.paragraph_no_ambiguous,"
            "locator=EXCLUDED.locator,table_count=EXCLUDED.table_count,tables_on_pages=EXCLUDED.tables_on_pages",
            (
                document_id, extraction_id, version_id, source_kind, int(item.get("ordinal") or 0), content_hash,
                item.get("qualified_no") or "", item.get("section") or "", item.get("clause_no") or "",
                item.get("citation") or item.get("label") or "", citeable,
                bool(item.get("statute")), bool(item.get("paragraph_no_ambiguous")),
                _jsonb(item.get("locator") or {}), len(item.get("tables") or []), _jsonb(item.get("tables_on_pages") or {}),
            ),
        )
        inserted_clause += 1
    return inserted_content, inserted_clause


def apply(documents: list[Document], dsn: str, identified_by: str) -> dict[str, int]:
    import psycopg

    summary = {"documents": 0, "contents": 0, "clauses": 0}
    with psycopg.connect(dsn) as conn:
        for document in documents:
            document_id, extraction_id, version_id = _insert_document(conn, document, identified_by)
            contents, clauses = _insert_clauses(conn, document, extraction_id, version_id, document_id)
            summary["documents"] += 1
            summary["contents"] += contents
            summary["clauses"] += clauses
        conn.commit()
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--structured-dir", type=Path, default=DEFAULT_STRUCTURED)
    parser.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFESTS)
    parser.add_argument("--dsn", default=os.environ.get("PG_DSN", ""))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--identified-by", help="ops.admin_user.id; --apply 시 필수")
    args = parser.parse_args(argv)

    documents = load_documents(args.structured_dir, args.manifest_dir)
    report = summarize(documents)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if not report["ready"]:
        print("blocked: 사람 검수·승인 전 자료는 core.confirmed_policy_document에 적재하지 않습니다.")
        return 3 if args.apply else 0
    if not args.apply:
        print("dry-run: 변경 없음")
        return 0
    if not args.dsn:
        print("--apply에는 --dsn 또는 PG_DSN이 필요합니다.")
        return 2
    if not args.identified_by:
        print("--apply에는 --identified-by가 필요합니다.")
        return 2
    result = apply(documents, args.dsn, args.identified_by)
    print(json.dumps({"applied": result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
