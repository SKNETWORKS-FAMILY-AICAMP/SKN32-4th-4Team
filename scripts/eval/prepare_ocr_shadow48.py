"""Build a deterministic 48-document, three-page MinerU shadow packet.

The selector starts from the independent self-pay keyword scan and deliberately
includes pages where the current table extractor found no table.  Source PDFs
stay local; only rendered PNG files and a non-sensitive manifest are produced.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCAN = ROOT / "scan_selfpay_pages.json"
DEFAULT_OUT = ROOT / "data" / "eval" / "ocr_shadow48"
REGRESSION_SHAS = {
    "21b40ddc9987",
    "99cedca23be0",
    "16061a35637e",
    "586d721a612a",
}
CATEGORIES = ("missed", "withheld", "accepted")


def load_generation_ranges() -> tuple[tuple[int, str | None, str | None], ...]:
    profile = json.loads((ROOT / "config" / "generation_profiles.json").read_text(encoding="utf-8"))
    return tuple(
        (
            int(item["generation"]),
            str(item["effective_from"]).replace("-", "") if item.get("effective_from") else None,
            str(item["effective_to"]).replace("-", "") if item.get("effective_to") else None,
        )
        for item in profile["generations"]
    )


GENERATION_RANGES = load_generation_ranges()


@dataclass(frozen=True)
class Anchor:
    category: str
    insurer: str
    sha12: str
    extracted_path: str
    page_1based: int
    sale_start: str
    generation_candidate: int | None
    product_type_candidate: str
    methods: tuple[str, ...]
    cols: tuple[int, ...]
    rows: tuple[int, ...]
    coord_count: int
    keyword_score: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generation_candidate(sale_start: str) -> int | None:
    if not re.fullmatch(r"\d{8}", sale_start or ""):
        return None
    for generation, effective_from, effective_to in GENERATION_RANGES:
        if effective_from and sale_start < effective_from:
            continue
        if effective_to and sale_start > effective_to:
            continue
        return generation
    return None


def product_type_candidate(product_name: str) -> str:
    # These markers are descriptive strata, not adjudicated business labels.
    if any(token in product_name for token in ("노후", "노후실손")):
        return "senior"
    if any(token in product_name for token in ("유병력자", "간편")):
        return "simplified_issue"
    if any(token in product_name for token in ("여행", "글로벌케어")):
        return "travel"
    if any(token in product_name for token in ("실손", "실손의료")):
        return "standard"
    return "unknown"


def keyword_score(text: str) -> int:
    weights = {
        "자기부담금": 8,
        "공제기준금액": 7,
        "공제금액": 6,
        "본인부담": 5,
        "급여": 2,
        "비급여": 2,
        "통원": 1,
        "외래": 1,
        "처방조제": 1,
    }
    return sum(weight for token, weight in weights.items() if token in text)


def page_category(page: dict[str, Any]) -> str:
    coords = page.get("tables_coords") or []
    # "accepted" means the current pipeline emitted a table, either via the
    # legacy page table path or an explicitly accepted coordinate candidate.
    if page.get("tables") or any(table.get("is_table") is True for table in coords):
        return "accepted"
    if not coords:
        return "missed"
    return "withheld"


def _page_by_number(document: dict[str, Any], page_1based: int) -> dict[str, Any]:
    try:
        return next(page for page in document["pages"] if int(page["page"]) == page_1based)
    except StopIteration as exc:
        raise RuntimeError(f"page {page_1based} missing from extracted document") from exc


def collect_anchors(scan_path: Path) -> list[Anchor]:
    scan = json.loads(scan_path.read_text(encoding="utf-8"))
    cache: dict[str, dict[str, Any]] = {}
    best: dict[tuple[str, str, str], Anchor] = {}

    for hit in scan:
        extracted_path = str(hit["file"])
        if extracted_path not in cache:
            cache[extracted_path] = json.loads(
                (ROOT / extracted_path).read_text(encoding="utf-8")
            )
        document = cache[extracted_path]
        source = document["source"]
        sha12 = str(source["sha256"])[:12]
        if sha12 in REGRESSION_SHAS:
            continue
        page_number = int(hit["page"])
        page = _page_by_number(document, page_number)
        category = page_category(page)
        coords = page.get("tables_coords") or []
        anchor = Anchor(
            category=category,
            insurer=str(hit["ins"]),
            sha12=sha12,
            extracted_path=extracted_path,
            page_1based=page_number,
            sale_start=str(source.get("sale_start") or ""),
            generation_candidate=generation_candidate(str(source.get("sale_start") or "")),
            product_type_candidate=product_type_candidate(str(source.get("product_name") or "")),
            methods=tuple(sorted({str(table.get("method") or "") for table in coords})),
            cols=tuple(sorted({int(table.get("cols") or 0) for table in coords})),
            rows=tuple(sorted({int(table.get("rows") or 0) for table in coords})),
            coord_count=len(coords),
            keyword_score=keyword_score(str(page.get("text") or "")),
        )
        key = (anchor.category, anchor.insurer, anchor.sha12)
        previous = best.get(key)
        rank = (anchor.keyword_score, anchor.coord_count, -anchor.page_1based)
        previous_rank = (
            (previous.keyword_score, previous.coord_count, -previous.page_1based)
            if previous
            else None
        )
        if previous is None or rank > previous_rank:
            best[key] = anchor
    return list(best.values())


def select_balanced(anchors: list[Anchor], per_category: int = 16) -> list[Anchor]:
    selected: list[Anchor] = []
    used_documents: set[tuple[str, str]] = set()
    global_insurer = Counter()

    # Accepted is the smallest pool, so reserve it first.
    for category in ("accepted", "missed", "withheld"):
        pool = [item for item in anchors if item.category == category]
        category_insurer = Counter()
        category_generation = Counter()
        for _ in range(per_category):
            available = [
                item
                for item in pool
                if (item.insurer, item.sha12) not in used_documents
            ]
            if not available:
                raise RuntimeError(f"not enough unique documents for {category}")
            choice = min(
                available,
                key=lambda item: (
                    category_insurer[item.insurer],
                    category_generation[item.generation_candidate],
                    global_insurer[item.insurer],
                    -item.keyword_score,
                    item.insurer,
                    item.sale_start,
                    item.sha12,
                    item.page_1based,
                ),
            )
            selected.append(choice)
            used_documents.add((choice.insurer, choice.sha12))
            category_insurer[choice.insurer] += 1
            category_generation[choice.generation_candidate] += 1
            global_insurer[choice.insurer] += 1

    # Stable presentation order does not affect selection.
    return sorted(selected, key=lambda item: (CATEGORIES.index(item.category), item.insurer, item.sha12))


def source_pdf(insurer: str, sha12: str) -> Path:
    folder = ROOT / "data" / "raw" / "insurance_terms" / insurer
    matches = sorted(folder.glob(f"{sha12}_*.pdf"))
    if not matches:
        raise FileNotFoundError(f"source PDF not found: {insurer}/{sha12}_*.pdf")
    digests = {sha256_file(path) for path in matches}
    if len(digests) != 1 or not next(iter(digests)).startswith(sha12):
        raise RuntimeError(f"ambiguous source PDF contents: {insurer}/{sha12}")
    return matches[0]


def build_packet(selected: list[Anchor], out_dir: Path, dpi: int) -> tuple[dict[str, Any], dict[str, Any]]:
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    documents: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []

    for anchor in selected:
        pdf = source_pdf(anchor.insurer, anchor.sha12)
        pdf_sha = sha256_file(pdf)
        document_samples = []
        with fitz.open(pdf) as opened:
            for page_number in range(max(1, anchor.page_1based - 1), min(len(opened), anchor.page_1based + 1) + 1):
                offset = page_number - anchor.page_1based
                sample_id = f"{anchor.sha12}_p{page_number:04d}"
                image_path = images_dir / f"{sample_id}.png"
                pix = opened[page_number - 1].get_pixmap(dpi=dpi, alpha=False)
                pix.save(image_path)
                sample = {
                    "id": sample_id,
                    "insurer": anchor.insurer,
                    "sha12": anchor.sha12,
                    "page_1based": page_number,
                    "anchor_page_1based": anchor.page_1based,
                    "window_offset": offset,
                    "category": anchor.category,
                    "kind": "selfpay_shadow48",
                    "image": image_path.relative_to(ROOT).as_posix(),
                    "image_sha256": sha256_file(image_path),
                    "source_pdf_sha256": pdf_sha,
                    "render_dpi": dpi,
                    "width": pix.width,
                    "height": pix.height,
                }
                samples.append(sample)
                document_samples.append(sample_id)
        documents.append(
            {
                "insurer": anchor.insurer,
                "sha12": anchor.sha12,
                "anchor_page_1based": anchor.page_1based,
                "category": anchor.category,
                "sale_start": anchor.sale_start,
                "generation_candidate": anchor.generation_candidate,
                "product_type_candidate": anchor.product_type_candidate,
                "methods": list(anchor.methods),
                "cols": list(anchor.cols),
                "rows": list(anchor.rows),
                "coord_count": anchor.coord_count,
                "keyword_score": anchor.keyword_score,
                "sample_ids": document_samples,
            }
        )

    manifest = {
        "schema_version": "1",
        "notice": "Shadow evaluation only. No output is serving or citation eligible.",
        "selector": "prepare_ocr_shadow48.py:v1",
        "render_dpi": dpi,
        "documents": documents,
        "samples": samples,
    }
    manifest["input_set_sha256"] = canonical_sha256(
        [{"id": item["id"], "image_sha256": item["image_sha256"]} for item in samples]
    )
    config = {
        "created_for": "ocr_shadow48",
        "models": [
            {
                "slug": "mineru_2_5_pro_2605",
                "model_id": "opendatalab/MinerU2.5-Pro-2605-1.2B",
                "adapter": "mineru",
                "model_class": "qwen2_vl",
                "prompt": "",
                "max_new_tokens": 8192,
                "decode": {"do_sample": False, "temperature": 0.0},
            }
        ],
        "samples": [
            {
                key: sample[key]
                for key in (
                    "id",
                    "insurer",
                    "sha12",
                    "page_1based",
                    "anchor_page_1based",
                    "window_offset",
                    "category",
                    "kind",
                    "image_sha256",
                )
            }
            for sample in samples
        ],
    }
    return manifest, config


def validate_packet(manifest: dict[str, Any]) -> None:
    documents = manifest["documents"]
    samples = manifest["samples"]
    category_counts = Counter(item["category"] for item in documents)
    if len(documents) != 48:
        raise RuntimeError(f"expected 48 documents, got {len(documents)}")
    if category_counts != Counter({category: 16 for category in CATEGORIES}):
        raise RuntimeError(f"unexpected category distribution: {category_counts}")
    doc_keys = {(item["insurer"], item["sha12"]) for item in documents}
    if len(doc_keys) != 48:
        raise RuntimeError("document SHA selection is not unique")
    if not 96 <= len(samples) <= 144:
        raise RuntimeError(f"unexpected three-page window size: {len(samples)}")
    sample_ids = [item["id"] for item in samples]
    if len(sample_ids) != len(set(sample_ids)):
        raise RuntimeError("duplicate rendered page IDs")


def write_transfer_packet(
    *, manifest_path: Path, config_path: Path, manifest: dict[str, Any], output_path: Path
) -> str:
    entries = [
        (manifest_path, "manifest.json"),
        (config_path, "ocr_shadow48_bench.json"),
        *((ROOT / sample["image"], f"input/{Path(sample['image']).name}") for sample in manifest["samples"]),
    ]
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source, archive_name in entries:
            info = zipfile.ZipInfo(archive_name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, source.read_bytes())
    with zipfile.ZipFile(output_path) as archive:
        names = archive.namelist()
    expected_names = [archive_name for _, archive_name in entries]
    if names != expected_names or any(name.lower().endswith(".pdf") for name in names):
        raise RuntimeError("transfer packet entries do not match the frozen manifest")
    return sha256_file(output_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", type=Path, default=DEFAULT_SCAN)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dpi", type=int, default=200)
    args = parser.parse_args()

    selected = select_balanced(collect_anchors(args.scan))
    manifest, config = build_packet(selected, args.out_dir, args.dpi)
    validate_packet(manifest)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out_dir / "manifest.json"
    config_path = ROOT / "config" / "ocr_shadow48_bench.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    packet_path = args.out_dir / "transfer_packet.zip"
    packet_sha256 = write_transfer_packet(
        manifest_path=manifest_path,
        config_path=config_path,
        manifest=manifest,
        output_path=packet_path,
    )

    print(f"documents={len(manifest['documents'])} pages={len(manifest['samples'])}")
    print(f"categories={dict(Counter(item['category'] for item in manifest['documents']))}")
    print(f"insurers={dict(Counter(item['insurer'] for item in manifest['documents']))}")
    print(f"generations={dict(Counter(str(item['generation_candidate']) for item in manifest['documents']))}")
    print(f"input_set_sha256={manifest['input_set_sha256']}")
    print(f"transfer_packet_sha256={packet_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
