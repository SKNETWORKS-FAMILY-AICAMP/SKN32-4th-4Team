"""Merge six S7 OCR shards and restore exact-image aliases safely.

Only byte-identical rendered images (the frozen PNG SHA-256 alias map) may
share an OCR result.  The merged occurrence files remain OCR evidence; this
script does not approve facts or change serving/citation eligibility.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


DEVICE_RESULT_DIRS = {
    "x600": "dedup6_x600",
    "runpod1": "dedup6_rp1",
    "runpod2": "dedup6_rp2",
    "runpod3": "dedup6_rp3",
    "runpod4": "dedup6_rp4",
    "runpod5": "dedup6_rp5",
}


class MergeError(RuntimeError):
    pass


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any, *, force: bool) -> None:
    body = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    if path.is_file():
        if path.read_bytes() == body:
            return
        if not force:
            raise MergeError(f"refusing to overwrite different output without --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _full_manifest(paths: list[Path]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    samples: list[dict[str, Any]] = []
    base: dict[str, Any] = {}
    for path in paths:
        manifest = _load(path)
        if not base:
            base = {k: v for k, v in manifest.items() if k not in {"samples", "documents"}}
        samples.extend(manifest.get("samples") or [])
    by_id = {str(item["id"]): item for item in samples}
    if len(by_id) != len(samples):
        raise MergeError("hard manifests contain duplicate sample IDs")
    # Omit source document groups: pages from hard0/hard1 can belong to the same
    # document, so the binder must regroup the combined occurrence set itself.
    return {**base, "samples": sorted(samples, key=lambda item: item["id"])}, by_id


def _representative_results(
    allocation_root: Path, results_root: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    results: dict[str, dict[str, Any]] = {}
    provenance: dict[str, dict[str, Any]] = {}
    for device, dirname in DEVICE_RESULT_DIRS.items():
        manifest_path = allocation_root / device / "manifest.json"
        result_dir = results_root / dirname
        if not manifest_path.is_file():
            raise MergeError(f"missing allocation manifest: {manifest_path}")
        if not result_dir.is_dir():
            raise MergeError(f"missing result directory: {result_dir}")
        manifest = _load(manifest_path)
        expected = {str(item["id"]): item for item in manifest.get("samples") or []}
        found: set[str] = set()
        for sample_id, sample in expected.items():
            path = result_dir / f"{sample_id}.json"
            if not path.is_file():
                raise MergeError(f"missing OCR result: {path}")
            result = _load(path)
            if result.get("sample_id") != sample_id:
                raise MergeError(f"result sample ID mismatch: {path}")
            if result.get("status") != "success":
                raise MergeError(f"non-success OCR result: {path}")
            image_sha = str(sample.get("image_sha256") or "")
            if result.get("image_sha256") != image_sha:
                raise MergeError(f"result image SHA mismatch: {path}")
            if sample_id in results:
                raise MergeError(f"representative occurs in multiple shards: {sample_id}")
            results[sample_id] = result
            provenance[sample_id] = {
                "device": device,
                "source_path": path.as_posix(),
                "source_result_sha256": _sha256(path),
            }
            found.add(sample_id)
        extra = {
            path.stem
            for path in result_dir.glob("*_p*.json")
            if path.stem not in expected
        }
        if extra:
            raise MergeError(f"unexpected page results in {result_dir}: {sorted(extra)[:5]}")
        if found != set(expected):
            raise MergeError(f"result set mismatch for {device}")
    return results, provenance


def merge(
    *,
    allocation_root: Path,
    results_root: Path,
    hard_manifests: list[Path],
    output_root: Path,
    model_revision: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    if model_revision is not None and (
        len(model_revision) != 40
        or any(character not in "0123456789abcdef" for character in model_revision.lower())
    ):
        raise MergeError(f"invalid model revision: {model_revision!r}")
    combined_manifest, all_samples = _full_manifest(hard_manifests)
    aliases = _load(allocation_root / "alias_map.json")
    representatives, representative_provenance = _representative_results(
        allocation_root, results_root
    )

    groups = aliases.get("groups") or []
    if len(groups) != int(aliases.get("unique_images") or -1):
        raise MergeError("alias unique-image count does not match groups")
    if set(representatives) != {str(group["representative_id"]) for group in groups}:
        raise MergeError("representative result IDs do not match alias map")

    member_ids: set[str] = set()
    expanded_count = 0
    for group in groups:
        image_sha = str(group["image_sha256"])
        representative_id = str(group["representative_id"])
        raw_representative = representatives[representative_id]
        raw_revision = raw_representative.get("model_revision")
        if model_revision and raw_revision and raw_revision != model_revision:
            raise MergeError(
                f"OCR model revision mismatch for {representative_id}: "
                f"result={raw_revision} cache_ref={model_revision}"
            )
        representative = dict(raw_representative)
        if model_revision:
            representative["model_revision"] = model_revision
            representative["model_revision_provenance"] = {
                "source": "huggingface_cache_ref",
                "raw_result_value": raw_revision,
            }
        if representative.get("image_sha256") != image_sha:
            raise MergeError(f"representative alias SHA mismatch: {representative_id}")
        source = representative_provenance[representative_id]
        _write_json(
            output_root / "unique" / f"{representative_id}.json",
            representative,
            force=force,
        )
        for member_id_raw in group.get("member_ids") or []:
            member_id = str(member_id_raw)
            if member_id in member_ids:
                raise MergeError(f"alias member repeated: {member_id}")
            member_ids.add(member_id)
            sample = all_samples.get(member_id)
            if sample is None:
                raise MergeError(f"alias member absent from hard manifests: {member_id}")
            if sample.get("image_sha256") != image_sha:
                raise MergeError(f"alias member image SHA mismatch: {member_id}")
            restored = {
                **representative,
                "sample_id": member_id,
                "image": f"{member_id}.png",
                "expected_image_sha256": image_sha,
                "image_sha256": image_sha,
                "exact_image_alias": {
                    "rule": "identical rendered PNG SHA-256 only",
                    "representative_id": representative_id,
                    "representative_device": source["device"],
                    "representative_result_sha256": source["source_result_sha256"],
                    "expanded": member_id != representative_id,
                },
            }
            _write_json(output_root / "expanded" / f"{member_id}.json", restored, force=force)
            expanded_count += member_id != representative_id

    if member_ids != set(all_samples):
        missing = sorted(set(all_samples) - member_ids)
        extra = sorted(member_ids - set(all_samples))
        raise MergeError(f"expanded occurrence IDs differ: missing={missing[:5]} extra={extra[:5]}")
    if len(representatives) != int(aliases.get("unique_images") or -1):
        raise MergeError("unique result count mismatch")
    if len(member_ids) != int(aliases.get("occurrences") or -1):
        raise MergeError("expanded occurrence count mismatch")
    if expanded_count != int(aliases.get("saved_inferences") or -1):
        raise MergeError("saved inference count mismatch")

    _write_json(output_root / "manifest.json", combined_manifest, force=force)
    summary = {
        "schema_version": "s7-ocr-dedup6-merged-v1",
        "unique_results": len(representatives),
        "occurrence_results": len(member_ids),
        "expanded_aliases": expanded_count,
        "success_results": sum(row.get("status") == "success" for row in representatives.values()),
        "serving_eligible": False,
        "citation_eligible": False,
        "model_revision": model_revision,
        "devices": {
            device: len((_load(allocation_root / device / "manifest.json")).get("samples") or [])
            for device in DEVICE_RESULT_DIRS
        },
    }
    _write_json(output_root / "summary.json", summary, force=force)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allocation-root", required=True, type=Path)
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--hard-manifest", required=True, action="append", type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--model-revision")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    summary = merge(
        allocation_root=args.allocation_root,
        results_root=args.results_root,
        hard_manifests=args.hard_manifest,
        output_root=args.output_root,
        model_revision=args.model_revision,
        force=args.force,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
