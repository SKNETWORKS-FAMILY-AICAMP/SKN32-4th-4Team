"""Render the frozen OCR SOTA-5 benchmark pages without copying source PDFs.

Usage:
    python -m scripts.eval.prepare_ocr_sota5
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "ocr_sota5_bench.json"
OUT_DIR = ROOT / "data" / "eval" / "ocr_sota5"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def source_pdf(insurer: str, sha12: str) -> Path:
    folder = ROOT / "data" / "raw" / "insurance_terms" / insurer
    matches = sorted(folder.glob(f"{sha12}_*.pdf"))
    if not matches:
        raise FileNotFoundError(f"source PDF not found: {insurer}/{sha12}_*.pdf")
    digests = {sha256(path) for path in matches}
    if len(digests) != 1 or not next(iter(digests)).startswith(sha12):
        raise RuntimeError(f"ambiguous source PDF contents: {insurer}/{sha12} ({len(matches)} files)")
    return matches[0]


def main() -> int:
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    dpi = int(cfg["render_dpi"])
    images_dir = OUT_DIR / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    manifest = []

    for sample in cfg["samples"]:
        pdf = source_pdf(sample["insurer"], sample["sha12"])
        page_index = int(sample["page_1based"]) - 1
        with fitz.open(pdf) as doc:
            if not 0 <= page_index < len(doc):
                raise IndexError(f"page out of range: {sample['id']} -> {page_index + 1}/{len(doc)}")
            page = doc[page_index]
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            image_path = images_dir / f"{sample['id']}.png"
            pix.save(image_path)

        manifest.append(
            {
                **sample,
                "image": image_path.relative_to(ROOT).as_posix(),
                "image_sha256": sha256(image_path),
                "source_pdf_sha256": sha256(pdf),
                "render_dpi": dpi,
                "width": pix.width,
                "height": pix.height,
            }
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "manifest.json"
    out.write_text(json.dumps({"samples": manifest}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"rendered {len(manifest)} pages -> {images_dir.relative_to(ROOT)}")
    print(f"manifest -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
