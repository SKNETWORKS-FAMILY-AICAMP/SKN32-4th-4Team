"""Bind MinerU HTML table rows into self-pay shadow fact candidates.

This module never approves facts.  MinerU currently supplies table-level bboxes,
not cell-level bboxes, so every candidate explicitly records that evidence
granularity and remains in the shadow/review lane.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


SELF_PAY_MARKERS = (
    "자기부담",
    "공제금액",
    "공제기준금액",
    "본인부담",
    "보상대상의료비",
)
PLAN_RE = re.compile(r"(표준형|기본형|선택형\s*(?:IV|I{1,3}|[ⅠⅡⅢⅣ]|[1-4])?|특약형)", re.I)
SERVICE_PATTERNS = (
    ("처방조제", re.compile(r"처방\s*[·ㆍ,]?\s*조제|처방조제|약제비")),
    ("외래", re.compile(r"외래")),
    ("통원", re.compile(r"통원")),
    ("입원", re.compile(r"입원")),
    ("도수치료", re.compile(r"도수치료")),
    ("주사료", re.compile(r"주사료|주사치료")),
    ("MRI/MRA", re.compile(r"MRI|MRA|자기공명영상", re.I)),
)
INSTITUTION_RE = re.compile(
    r"의원|병원|약국|조산원|보건소|보건의료원|보건지소|보건진료소|"
    r"의료기관|요양기관|상급종합|종합전문|의약품센터"
)
COVERAGE_RE = re.compile(r"3대\s*비급여|비급여|급여|본인부담")
AMOUNT_RE = re.compile(
    r"(?<!\d)\d+(?:[.,]\d+)?(?:\s*(?:억|천만|백만|십만|만|천)\s*\d*)*\s*원"
)
RATE_RE = re.compile(r"(?<!\d)\d+(?:\.\d+)?\s*%")
NON_KOREAN_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
KNOWN_DOMAIN_SUSPECTS = ("회귀의약품", "당 뇌 병", "뿐 금액")


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", " ", value).strip()


def canonical_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def valid_normalized_bbox(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(item, (int, float)) and 0 <= item <= 1 for item in value)
        and value[0] < value[2]
        and value[1] < value[3]
    )


@dataclass(frozen=True)
class Cell:
    text: str
    raw_row_index: int
    raw_cell_index: int
    rowspan: int = 1
    colspan: int = 1

    @property
    def origin(self) -> dict[str, int]:
        return {"raw_row_index": self.raw_row_index, "raw_cell_index": self.raw_cell_index}


class TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[Cell]]] = []
        self._table_depth = 0
        self._rows: list[list[Cell]] | None = None
        self._row: list[Cell] | None = None
        self._cell_attrs: dict[str, str] | None = None
        self._cell_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._rows = []
        elif self._table_depth == 1 and tag == "tr":
            self._row = []
        elif self._table_depth == 1 and tag in {"td", "th"} and self._row is not None:
            self._cell_attrs = {key.lower(): value or "" for key, value in attrs}
            self._cell_parts = []
        elif self._cell_attrs is not None and tag in {"br", "p", "div"}:
            self._cell_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell_attrs is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._table_depth == 1 and tag in {"td", "th"} and self._cell_attrs is not None:
            assert self._row is not None and self._rows is not None
            self._row.append(
                Cell(
                    text=normalize_text("".join(self._cell_parts)),
                    raw_row_index=len(self._rows),
                    raw_cell_index=len(self._row),
                    rowspan=max(1, int(self._cell_attrs.get("rowspan") or 1)),
                    colspan=max(1, int(self._cell_attrs.get("colspan") or 1)),
                )
            )
            self._cell_attrs = None
            self._cell_parts = []
        elif self._table_depth == 1 and tag == "tr" and self._row is not None:
            assert self._rows is not None
            self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._table_depth:
            if self._table_depth == 1 and self._rows is not None:
                self.tables.append(self._rows)
                self._rows = None
            self._table_depth -= 1


def parse_tables(html: str) -> list[list[list[Cell]]]:
    parser = TableHTMLParser()
    parser.feed(html or "")
    parser.close()
    return parser.tables


def expand_grid(rows: list[list[Cell]]) -> list[list[Cell | None]]:
    occupied: dict[tuple[int, int], Cell] = {}
    width = 0
    for row_index, cells in enumerate(rows):
        column = 0
        for cell in cells:
            while (row_index, column) in occupied:
                column += 1
            for row_offset in range(cell.rowspan):
                for column_offset in range(cell.colspan):
                    occupied[(row_index + row_offset, column + column_offset)] = cell
            column += cell.colspan
            width = max(width, column)
    height = max((row for row, _ in occupied), default=-1) + 1
    return [
        [occupied.get((row, column)) for column in range(width)]
        for row in range(height)
    ]


def unique_cells(row: list[Cell | None]) -> list[Cell]:
    seen: set[tuple[int, int]] = set()
    result = []
    for cell in row:
        if cell is None:
            continue
        key = (cell.raw_row_index, cell.raw_cell_index)
        if key not in seen:
            seen.add(key)
            result.append(cell)
    return result


def grid_integrity(rows: list[list[Cell]], grid: list[list[Cell | None]]) -> dict[str, int]:
    expected: dict[tuple[int, int], int] = {}
    for row in rows:
        for cell in row:
            expected[(cell.raw_row_index, cell.raw_cell_index)] = cell.rowspan * cell.colspan
    actual: dict[tuple[int, int], int] = {}
    for row in grid:
        for cell in row:
            if cell is None:
                continue
            key = (cell.raw_row_index, cell.raw_cell_index)
            actual[key] = actual.get(key, 0) + 1
    return {
        "raw_rows": len(rows),
        "expanded_rows": len(grid),
        "expanded_columns": max((len(row) for row in grid), default=0),
        "ragged_rows": sum(any(cell is None for cell in row) for row in grid),
        "span_mismatch_cells": sum(actual.get(key, 0) != count for key, count in expected.items()),
    }


def extract_amounts(value: str) -> tuple[list[str], list[str]]:
    return (
        [normalize_text(token) for token in AMOUNT_RE.findall(value)],
        [normalize_text(token) for token in RATE_RE.findall(value)],
    )


def service_values(texts: list[str]) -> list[str]:
    values = []
    joined = " | ".join(texts)
    for label, pattern in SERVICE_PATTERNS:
        if pattern.search(joined):
            values.append(label)
    return values


def fact_validation(fact: dict[str, Any], all_text: str) -> dict[str, Any]:
    reasons: list[str] = []
    if not fact["amount_tokens"] and not fact["rate_tokens"]:
        reasons.append("missing_amount_or_rate")
    if not fact["plan"]:
        reasons.append("missing_plan")
    if not fact["service"]:
        reasons.append("missing_service")
    if not valid_normalized_bbox(fact["source"]["table_bbox"]):
        reasons.append("missing_table_bbox")
    if fact["source"]["continuation_suspected"]:
        reasons.append("page_boundary_continuation")
    integrity = fact["source"].get("grid_integrity") or {}
    if integrity.get("ragged_rows"):
        reasons.append("ragged_expanded_grid")
    if integrity.get("span_mismatch_cells"):
        reasons.append("span_overlap_or_overwrite")
    if NON_KOREAN_CJK_RE.search(all_text):
        reasons.append("non_korean_cjk")
    if any(token in all_text for token in KNOWN_DOMAIN_SUSPECTS):
        reasons.append("known_domain_ocr_suspect")
    return {
        "status": "review_required" if reasons else "shadow_pass",
        "reasons": sorted(set(reasons)),
    }


def bind_table(
    *,
    html: str,
    bbox: list[float] | None,
    structured_index: int,
    sample: dict[str, Any],
    parsed_table_index: int,
    ocr_provenance: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    parsed_tables = parse_tables(html)
    if parsed_table_index >= len(parsed_tables):
        return []
    if not any(marker in html for marker in SELF_PAY_MARKERS):
        return []
    grid = expand_grid(parsed_tables[parsed_table_index])
    integrity = grid_integrity(parsed_tables[parsed_table_index], grid)
    bbox_valid = valid_normalized_bbox(bbox)
    continuation_suspected = bool(bbox_valid and (bbox[1] <= 0.13 or bbox[3] >= 0.88))
    candidates: list[dict[str, Any]] = []

    first_value_row = next(
        (
            index
            for index, row in enumerate(grid)
            if any(extract_amounts(cell.text) != ([], []) for cell in unique_cells(row))
        ),
        0,
    )

    for logical_row_index, logical_row in enumerate(grid):
        row_cells = unique_cells(logical_row)
        for amount_cell in row_cells:
            amount_tokens, rate_tokens = extract_amounts(amount_cell.text)
            if not amount_tokens and not rate_tokens:
                continue
            if not amount_tokens and not any(marker in amount_cell.text for marker in SELF_PAY_MARKERS):
                continue
            context_cells = [cell for cell in row_cells if cell is not amount_cell]
            amount_columns = [index for index, cell in enumerate(logical_row) if cell is amount_cell]
            for header_row in grid[:first_value_row]:
                for column in amount_columns:
                    header_cell = header_row[column] if column < len(header_row) else None
                    if header_cell and header_cell is not amount_cell and header_cell not in context_cells:
                        context_cells.append(header_cell)
            context_texts = [cell.text for cell in context_cells if cell.text]
            joined_context = " | ".join(context_texts)
            plan_match = PLAN_RE.search(joined_context)
            services = service_values(context_texts + [amount_cell.text])
            institution = next(
                (cell.text for cell in context_cells if INSTITUTION_RE.search(cell.text)),
                "",
            )
            coverage = []
            for value in context_texts + [amount_cell.text]:
                coverage.extend(normalize_text(token) for token in COVERAGE_RE.findall(value))
            coverage = list(dict.fromkeys(coverage))
            source = {
                "image_sha256": sample.get("image_sha256"),
                "ocr": ocr_provenance or {},
                "table_bbox": bbox,
                "evidence_granularity": "table",
                "structured_index": structured_index,
                "parsed_table_index": parsed_table_index,
                "logical_row_index": logical_row_index,
                "amount_cell_origin": amount_cell.origin,
                "amount_origin_group": (
                    f"{structured_index}:{parsed_table_index}:"
                    f"{amount_cell.raw_row_index}:{amount_cell.raw_cell_index}"
                ),
                "context_cell_origins": [cell.origin for cell in context_cells],
                "continuation_suspected": continuation_suspected,
                "grid_integrity": integrity,
                "axis_binding": {
                    "method": "expanded_html_grid_row_column_context",
                    "association_inferred": True,
                    "value_invention": False,
                },
            }
            semantic = {
                "document_sha12": sample["sha12"],
                "page_1based": sample["page_1based"],
                "plan": normalize_text(plan_match.group(0)) if plan_match else "",
                "service": services,
                "institution": institution,
                "coverage": coverage,
                "amount_formula": amount_cell.text,
                "amount_tokens": amount_tokens,
                "rate_tokens": rate_tokens,
                "source": source,
            }
            hash_source = {
                **source,
                "table_bbox": [round(float(value), 4) for value in bbox] if bbox_valid else bbox,
            }
            hash_semantic = {**semantic, "source": hash_source}
            fact = {
                "candidate_id": f"sha256:{canonical_hash(hash_semantic)}",
                "insurer": sample.get("insurer"),
                "category": sample.get("category"),
                **semantic,
                "inferred": False,
                "approval": "candidate",
                "serving_eligible": False,
                "citation_eligible": False,
            }
            fact["validation"] = fact_validation(fact, " | ".join(context_texts + [amount_cell.text]))
            candidates.append(fact)
    return candidates


def bind_result(result: dict[str, Any], sample: dict[str, Any]) -> list[dict[str, Any]]:
    if result.get("status") != "success":
        return []
    candidates: list[dict[str, Any]] = []
    provenance = {
        "model_slug": result.get("model_slug"),
        "model_id": result.get("model_id"),
        "model_revision": result.get("model_revision"),
        "runner_environment": result.get("runner_environment") or {},
    }
    if isinstance(result.get("exact_image_alias"), dict):
        provenance["exact_image_alias"] = result["exact_image_alias"]
    for structured_index, element in enumerate(result.get("structured") or []):
        if element.get("type") != "table" or not isinstance(element.get("content"), str):
            continue
        html = element["content"]
        for parsed_table_index in range(len(parse_tables(html))):
            candidates.extend(
                bind_table(
                    html=html,
                    bbox=element.get("bbox"),
                    structured_index=structured_index,
                    sample=sample,
                    parsed_table_index=parsed_table_index,
                    ocr_provenance=provenance,
                )
            )
    return candidates


def document_groups(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if manifest.get("documents"):
        return manifest["documents"]
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for sample in manifest["samples"]:
        key = (sample["insurer"], sample["sha12"])
        group = groups.setdefault(
            key,
            {
                "insurer": sample["insurer"],
                "sha12": sample["sha12"],
                "category": sample.get("category") or "unknown",
                "sample_ids": [],
            },
        )
        if group["category"] != (sample.get("category") or "unknown"):
            group["category"] = "mixed"
        group["sample_ids"].append(sample["id"])
    return list(groups.values())


def run_binder(manifest: dict[str, Any], results_root: Path, output_dir: Path) -> dict[str, Any]:
    samples = {item["id"]: item for item in manifest["samples"]}
    output_dir.mkdir(parents=True, exist_ok=True)
    document_summaries = []
    all_candidates: list[dict[str, Any]] = []

    for document in document_groups(manifest):
        candidates = []
        page_status = []
        for sample_id in document["sample_ids"]:
            sample = samples[sample_id]
            result_path = results_root / f"{sample_id}.json"
            if not result_path.is_file():
                page_status.append({"sample_id": sample_id, "status": "missing"})
                continue
            result = json.loads(result_path.read_text(encoding="utf-8"))
            page_status.append({"sample_id": sample_id, "status": result.get("status", "missing")})
            candidates.extend(bind_result(result, sample))
        payload = {
            "schema_version": "1",
            "notice": "Shadow candidates only. Human approval required.",
            "insurer": document["insurer"],
            "sha12": document["sha12"],
            "category": document.get("category"),
            "page_status": page_status,
            "candidates": candidates,
        }
        (output_dir / f"{document['sha12']}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        all_candidates.extend(candidates)
        document_summaries.append(
            {
                "insurer": document["insurer"],
                "sha12": document["sha12"],
                "category": document.get("category"),
                "pages": len(page_status),
                "successful_pages": sum(item["status"] == "success" for item in page_status),
                "candidate_count": len(candidates),
                "review_required": sum(
                    item["validation"]["status"] == "review_required" for item in candidates
                ),
            }
        )

    validation_status: dict[str, int] = {}
    validation_reasons: dict[str, int] = {}
    for candidate in all_candidates:
        validation = candidate.get("validation") or {}
        status = str(validation.get("status") or "missing")
        validation_status[status] = validation_status.get(status, 0) + 1
        for reason in validation.get("reasons") or []:
            validation_reasons[str(reason)] = validation_reasons.get(str(reason), 0) + 1
    summary = {
        "schema_version": "1",
        "notice": "No candidate is serving or citation eligible.",
        "documents": document_summaries,
        "candidate_count": len(all_candidates),
        "candidate_ids_unique": len({item["candidate_id"] for item in all_candidates}),
        "serving_eligible_count": sum(bool(item["serving_eligible"]) for item in all_candidates),
        "citation_eligible_count": sum(bool(item["citation_eligible"]) for item in all_candidates),
        "candidate_documents": sum(bool(item["candidate_count"]) for item in document_summaries),
        "zero_candidate_documents": sum(not item["candidate_count"] for item in document_summaries),
        "successful_pages": sum(item["successful_pages"] for item in document_summaries),
        "validation_status": dict(sorted(validation_status.items())),
        "validation_reasons": dict(sorted(validation_reasons.items())),
        "exact_alias_expanded_candidates": sum(
            bool((((item.get("source") or {}).get("ocr") or {}).get("exact_image_alias") or {}).get("expanded"))
            for item in all_candidates
        ),
    }
    (output_dir / "index.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    summary = run_binder(manifest, args.results_root, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
