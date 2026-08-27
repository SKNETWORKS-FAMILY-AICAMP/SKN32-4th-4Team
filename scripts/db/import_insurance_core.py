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
#: ★★**한 보험사만 보고 있었다** (2026-08-27 실측하고 고쳤다).
#:
#:   기본값이 `data/structured/dbins/s7_hybrid-table-v1` 이었다 — dbins **236건**뿐이다.
#:   그런데 현행 승인 `clause_tag` 는 `s6_pymupdf-1.28.0`(12개사 **1,355건**)이다.
#:   그대로 두면 「적재 준비 안 됨」의 분모가 236 이라 **11개사가 통째로 안 보인다.**
#:   ★경로를 박지 않는다 — 승인 릴리스(`config/accepted_extraction.json`)가 정한다.
DEFAULT_STRUCTURED = ROOT / "data" / "structured" / "dbins" / "s7_hybrid-table-v1"
DEFAULT_MANIFESTS = ROOT / "data" / "raw" / "manifests"
_LEDGER = ROOT / "config" / "confirmed_documents.jsonl"


def accepted_structured_dirs() -> list[Path]:
    """승인 릴리스의 `clause_tag` 로 **12개사 전부**를 훑는다."""
    from app.core.config import get_settings  # noqa: F401  (설정 로딩 부작용 방지용 지연 임포트)

    cfg = json.loads((ROOT / "config" / "accepted_extraction.json").read_text(encoding="utf-8"))
    tag = str(cfg.get("clause_tag") or "").strip()
    if not tag:
        #: ★기본값으로 때우지 않는다 — 어느 판을 넣는지가 안 정해진다.
        raise ValueError("accepted_extraction.json 에 clause_tag 가 없습니다.")
    dirs = sorted(p for p in (ROOT / "data" / "structured").glob(f"*/{tag}") if p.is_dir())
    if not dirs:
        raise ValueError(f"승인 clause_tag '{tag}' 산출물 디렉터리를 못 찾았습니다.")
    return dirs


def load_confirmation_ledger() -> dict[str, dict[str, Any]]:
    """확정 원장. **여기가 「확정」의 단일 진실원**이다.

    ★산출물 파일 안의 `identification`·`release_state` 는 **생성 시점 스냅샷**이라
      확정 결과가 반영돼 있지 않다(실측 2026-08-27: s6·s7 전량 `unidentified`).
      그렇다고 산출물을 고쳐 맞추면 **`manifest --verify` 가 통째로 깨진다** —
      같은 경로에 다른 내용을 쓰는 것이기 때문이다(2026-08-26 에 실제로 겪었다).
      그래서 **산출물은 그대로 두고, 확정은 원장에서 읽는다.**
    """
    if not _LEDGER.is_file():
        return {}
    out: dict[str, Any] = {}
    for line in _LEDGER.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[str(row.get("sha256") or "")] = row
    return out
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


def _release_blockers(document: Document, ledger: dict[str, Any] | None = None) -> list[str]:
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
    #: ★★**확정은 원장에서 읽는다** (2026-08-27 에 바꿨다).
    #:   앞서는 산출물 파일의 `identification` 을 봤는데, 그건 **생성 시점 스냅샷**이라
    #:   s6·s7 **전량이 `unidentified`** 였다 — 확정 1,355건이 하나도 안 보였다.
    #:   산출물을 고쳐 맞추는 길은 막혀 있다: 같은 경로에 다른 내용을 쓰면
    #:   `manifest --verify` 가 통째로 깨진다(2026-08-26 에 실제로 겪었다).
    entry = (ledger or {}).get(str(source.get("sha256") or ""))
    if entry is None:
        blockers.append("identification_not_confirmed")
    else:
        #: ★확정됐어도 **사람 승인 전이면 못 넣는다.** 게이트와 같은 판정을 쓴다 —
        #:   여기서 문자열을 따로 비교하면 두 곳이 갈린다.
        from app.core.domain import identification_mode as im

        if im.is_pending_signoff(entry):
            blockers.append("human_signoff_pending")
    #: ★릴리스 승인도 산출물이 아니라 **승인 릴리스 파일**이 정한다.
    #:   s6 산출물에는 `release_state` 필드 자체가 없다(실측).
    if not _release_accepted():
        blockers.append(f"release_approval:{release.get('approval', '<missing>')}")
    return blockers


def _hash_version() -> str:
    """어느 추출 판으로 만든 해시인가. 승인 릴리스의 `clause_tag` 에서 온다."""
    cfg = json.loads((ROOT / "config" / "accepted_extraction.json").read_text(encoding="utf-8"))
    tag = str(cfg.get("clause_tag") or "").strip()
    if not tag:
        raise ValueError("accepted_extraction.json 에 clause_tag 가 없습니다.")
    return tag


def _release_accepted() -> bool:
    """승인 릴리스가 확정 상태인가. `accepted_at` 이 있으면 확정으로 본다."""
    cfg = json.loads((ROOT / "config" / "accepted_extraction.json").read_text(encoding="utf-8"))
    return bool(str(cfg.get("accepted_at") or "").strip())


def summarize(documents: list[Document]) -> dict[str, Any]:
    blockers: dict[str, int] = {}
    clauses = 0
    annexes = 0
    contents: set[str] = set()
    ledger = load_confirmation_ledger()
    #: ★★**전부 아니면 전무**였다 (2026-08-27 에 고쳤다).
    #:
    #:   `ready = not blockers` 라서, 조항 머리를 못 찾은 문서 **19건**이
    #:   나머지 **1,336건 전부**를 막고 있었다. 그 19건은 고칠 수 있는 게 아니다 —
    #:   `parse_status` 가 `ok` 가 아니면 인용할 조항 자체가 없다.
    #:   ★색인(`build_clause_index._collect`)은 **같은 19건을 이미 세어서 건너뛴다.**
    #:     두 경로가 다른 규칙을 쓰면 core 와 색인의 분모가 갈린다.
    #:   그래서 여기서도 **문서 단위로** 가른다 — 다만 **조용히 넘기지 않는다**(§3):
    #:   못 넣는 문서 수와 사유를 `skipped`·`blockers` 로 세어 돌려준다.
    loadable: list[Document] = []
    for document in documents:
        marks = _release_blockers(document, ledger)
        if not marks:
            loadable.append(document)
        for blocker in marks:
            blockers[blocker] = blockers.get(blocker, 0) + 1
    #: ★세는 것은 **실제로 들어갈 것**만이다. 못 넣는 문서의 조항까지 세면
    #:   「몇 건 들어가나」를 물었을 때 실제보다 많게 답한다.
    for document in loadable:
        clauses += len(document.row.get("clauses") or [])
        annexes += len(document.row.get("annexes") or [])
        for item in (document.row.get("clauses") or []) + (document.row.get("annexes") or []):
            content_hash = str(item.get("content_hash") or "")
            if content_hash:
                contents.add(content_hash)
    #: ★**전역 차단과 문서별 차단을 가른다.** 릴리스가 승인 안 됐다면 아무것도 못 넣는다.
    #:   반면 조항 머리를 못 찾은 문서 하나는 그 문서만 못 넣는 것이다.
    fatal = {k: v for k, v in blockers.items() if k.startswith("release_approval")}
    return {
        "structured_documents": len(documents),
        "loadable_documents": len(loadable),
        "skipped_documents": len(documents) - len(loadable),
        "clauses": clauses,
        "annexes": annexes,
        "unique_contents": len(contents),
        "ready": bool(loadable) and not fatal,
        "fatal_blockers": dict(sorted(fatal.items())),
        "blockers": dict(sorted(blockers.items())),
    }


def loadable_documents(documents: list[Document]) -> list[Document]:
    """실제로 넣을 문서만. `summarize` 와 **같은 판정**을 쓴다."""
    ledger = load_confirmation_ledger()
    return [d for d in documents if not _release_blockers(d, ledger)]


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
        #: ★★자리표는 9개인데 **인자를 11개 넘기고 있었다** — `ProgrammingError` 로 죽는다.
        #:   core 가 0행이라 이 경로는 **한 번도 실행된 적이 없어** 아무도 몰랐다
        #:   (2026-08-27 에 처음 돌려 보고 발견).
        #:
        #: ★★★남는 두 인자는 `valid_from`/`valid_to` 자리였다. 그런데 SQL 은 그 둘을
        #:   `NULL` 로 박아 뒀다. **SQL 쪽이 맞다** —
        #:     `sales_from`/`sales_to` = 판매 기간
        #:     `valid_from`/`valid_to` = **적용 구간** (사고일을 여기 맞춘다)
        #:   핸드오프 16번이 이 둘을 섞으면 「사고일을 판매기간에 대조하는 버그」가
        #:   된다고 못 박아 뒀다. 판매일을 적용구간에 복사하는 것은 **지어내는 것**이고,
        #:   그 값으로 「이 약관이 적용된다」를 판정하면 틀린 근거가 나간다(§1).
        #:   적용구간은 **모르므로 비워 둔다.**
        (
            document_id, product, _version_label(source),
            _date(source.get("sale_start")),
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
    #: ★★**인용 가능 여부는 조항마다 다르다** (2026-08-27 실측하고 고쳤다).
    #:
    #:   앞서는 **문서 단위로 한 번** 계산했다 —
    #:       citeable = release_state.citation_eligible AND 문서.citation_eligible
    #:   그런데 s6 산출물에는 `release_state` 가 **아예 없다.** 그래서 `None and …` 이
    #:   되어 **196,039행 전부 `false`** 로 들어갔다. 산출물은 182,330개를 인용가능으로
    #:   표시하는데도 그렇다. 처음 적재해 보고서야 드러났다.
    #:
    #:   ★fail-closed 라 「보장됩니다」가 잘못 나가진 않았지만, 그 반대로
    #:     **근거를 댈 수 있는 조항을 못 댄다.** 그것도 서비스가 죽는 길이다.
    #:
    #:   ★★`citeable` 은 NOT NULL 이라 「모른다」를 담을 수 없다.
    #:     부록처럼 그 필드가 없는 것은 **인용 불가로 둔다** — 모르면 인용하지 않는다(§0).
    #:     색인(`policy_clause_occurrence.citation_eligible`)은 NULL 을 허용해
    #:     「모른다」를 구분하지만, 이 표는 못 한다. 그 차이를 알고 읽어야 한다.
    items = [("clause", x) for x in document.row.get("clauses") or []]
    items += [("annex", x) for x in document.row.get("annexes") or []]
    for source_kind, item in items:
        paragraphs = item.get("paragraphs") or []
        content_hash = item["content_hash"]
        conn.execute(
            #: ★`hash_version` 이 `'s7-hybrid-table-v1'` 로 **박혀 있었다.**
            #:   지금 넣는 것은 s6 다 — 어느 판으로 만든 해시인지를 틀리게 적으면
            #:   나중에 「이 해시는 어느 추출기 것이냐」를 답할 수 없다(§1 버전 박기).
            "INSERT INTO core.clause_content(content_hash,hash_version,title,body,char_length,"
            "paragraph_count,paragraphs) VALUES (%s,%s,%s,%s,%s,%s,%s) "
            #: ★`hash_version` 을 갱신 목록에 **넣는다.** 빠져 있어서 다시 넣어도 옛 값이
            #:   남았다(실측 2026-08-27: s6 를 넣었는데 `s7-hybrid-table-v1` 그대로였다).
            "ON CONFLICT (content_hash) DO UPDATE SET hash_version=EXCLUDED.hash_version,"
            "title=EXCLUDED.title,body=EXCLUDED.body,"
            "char_length=EXCLUDED.char_length,paragraph_count=EXCLUDED.paragraph_count,paragraphs=EXCLUDED.paragraphs",
            (content_hash, _hash_version(),
             item.get("title") or item.get("label") or "", item.get("text") or "",
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
                item.get("citation") or item.get("label") or "",
                item.get("citation_eligible") is True,
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
    #: ★기본을 **승인 릴리스의 clause_tag 전량**으로 바꿨다(2026-08-27).
    #:   `--structured-dir` 를 주면 그 한 곳만 본다(옛 동작, 디버그용).
    parser.add_argument("--structured-dir", type=Path, default=None,
                        help="한 곳만 볼 때. 기본은 승인 clause_tag 로 12개사 전량")
    parser.add_argument("--manifest-dir", type=Path, default=DEFAULT_MANIFESTS)
    parser.add_argument("--dsn", default=os.environ.get("PG_DSN", ""))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--identified-by", help="ops.admin_user.id; --apply 시 필수")
    args = parser.parse_args(argv)

    if args.structured_dir is not None:
        dirs = [args.structured_dir]
    else:
        dirs = accepted_structured_dirs()
    documents: list[Document] = []
    for d in dirs:
        documents.extend(load_documents(d, args.manifest_dir))
    #: ★몇 곳을 봤는지 **찍는다.** 앞서 한 보험사만 보고 있던 것을 아무도 몰랐다.
    print(f"[대상] 산출물 디렉터리 {len(dirs)}곳 · 문서 {len(documents):,}건", flush=True)
    report = summarize(documents)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if report["skipped_documents"]:
        #: ★건너뛴 것을 **크게 말한다.** 조용히 빼면 다음 사람이 분모를 모른다(§3).
        print(f"[건너뜀] {report['skipped_documents']:,}건은 넣지 않는다 — "
              f"{report['blockers']}", flush=True)
    if not report["ready"]:
        print("blocked: 사람 검수·승인 전 자료는 core.confirmed_policy_document에 적재하지 않습니다.")
        if report["fatal_blockers"]:
            print(f"  전역 차단: {report['fatal_blockers']}")
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
    #: ★차단된 문서는 **넣지 않는다.** `summarize` 와 같은 판정을 쓴다 —
    #:   여기서 따로 거르면 「보고한 수」와 「넣은 수」가 갈린다.
    result = apply(loadable_documents(documents), args.dsn, args.identified_by)
    print(json.dumps({"applied": result}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
