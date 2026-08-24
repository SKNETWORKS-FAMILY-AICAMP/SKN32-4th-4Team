"""식별 파이프라인 실행 — 검수 큐를 만든다.

`data/raw/fetch_manifest.jsonl` 의 수집 기록을 읽어
**Artifact(sha256 기준) + SourceOccurrence(수집 사건) 로 분리**하고,
파일마다 근거를 추출해 후보를 만든 뒤, 사람이 볼 검수 큐를 낸다.

★URL 기준으로 묶지 않는 이유: URL은 바뀔 수 있고, 다른 URL이 같은 파일을 줄 수 있으며,
같은 URL의 내용이 시점에 따라 교체될 수도 있다. URL dedup 은 **중복을 놓치면서
동시에 변경 이력도 덮는다.**

이 도구는 **아무것도 확정하지 않는다.** 산출물은 전부 `UNIDENTIFIED` 또는 `AMBIGUOUS` 다.

실행: python -m scripts.identify.build_review_queue
"""

from __future__ import annotations

import collections
import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path

from app.core.domain.document_identification import Artifact, SourceOccurrence
from app.core.domain.insurance import IdentificationStatus
from app.core.errors import InfraError
from app.core.usecases.identify_document import IdentifyDocument
from app.outer.outbound.pdf.extractor import PdfEvidenceExtractor

_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _ROOT / "data" / "raw" / "fetch_manifest.jsonl"
_OUT = _ROOT / "docs" / "reports"


def load_occurrences() -> dict[str, list[SourceOccurrence]]:
    if not _MANIFEST.exists():
        raise InfraError(f"수집 매니페스트가 없습니다: {_MANIFEST}")
    grouped: dict[str, list[SourceOccurrence]] = collections.defaultdict(list)
    for line in _MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        grouped[r["sha256"]].append(
            SourceOccurrence(
                artifact_sha256=r["sha256"],
                url=r["url"],
                fetched_at=r["fetched_at"],
                product_code=r.get("product_code", ""),
                product_name=r.get("product_name", ""),
                sale_start=r.get("sale_start", ""),
                sale_end=r.get("sale_end", ""),
            )
        )
    return grouped


def main() -> None:
    grouped = load_occurrences()
    extractor = PdfEvidenceExtractor()
    usecase = IdentifyDocument()

    rows: list[dict[str, object]] = []
    failures: list[tuple[str, str]] = []

    for sha, occs in grouped.items():
        saved = None
        for line in _MANIFEST.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r["sha256"] == sha:
                saved = _ROOT / r["saved_as"]
                size = r["bytes"]
                break
        if saved is None:
            failures.append((sha, "저장 경로를 찾지 못함"))
            continue

        try:
            ev = extractor.extract(path=saved, sha256=sha)
        except InfraError as e:
            failures.append((sha, str(e)))
            continue

        artifact = Artifact(sha256=sha, bytes=size, page_count=ev.page_count, quality=ev.quality)
        result = usecase.run(artifact=artifact, occurrences=tuple(occs), evidence=ev)
        cand = result.candidates[0]
        rows.append(
            {
                "sha256": sha,
                "file": saved.name,
                "bytes": size,
                "pages": ev.page_count,
                "text_length": ev.text_length,
                "quality": ev.quality.value,
                "status": result.status.value,
                "document_kind": cand.document_kind.value,
                "variant": cand.variant.value,
                "generation": cand.generation,
                "cross_verified": cand.cross_verified,
                "quarantine_reasons": list(result.quarantine_reasons),
                "occurrences": [asdict(o) for o in occs],
                "supporting": [asdict(e) for e in cand.supporting],
                "opposing": [asdict(e) for e in cand.opposing],
            }
        )

    stamp = date.today().isoformat()
    _OUT.mkdir(parents=True, exist_ok=True)
    (_OUT / f"{stamp}_식별_검수큐.json").write_text(
        json.dumps(
            {
                "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "artifacts": len(grouped),
                "identified_automatically": 0,  # ★자동 확정은 0건이어야 정상이다
                "rows": rows,
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    kinds = collections.Counter(r["document_kind"] for r in rows)
    stat = collections.Counter(r["status"] for r in rows)
    qual = collections.Counter(r["quality"] for r in rows)

    lines = [
        f"# 문서 식별 검수 큐 — {stamp}",
        "",
        "- 도구: `scripts/identify/build_review_queue.py`",
        f"- 고유 파일(Artifact) **{len(grouped)}건** / 수집 사건(Occurrence) "
        f"**{sum(len(v) for v in grouped.values())}건**",
        "- ★**자동 확정 0건.** 이 도구는 후보만 만들고 확정은 사람 검수로만 한다.",
        "",
        "## 집계",
        "",
        f"| 상태 | {' · '.join(f'{k}={v}' for k, v in stat.items())} |",
        "|---|---|",
        f"| 문서종류 후보 | {' · '.join(f'{k}={v}' for k, v in kinds.items())} |",
        f"| 추출 품질 | {' · '.join(f'{k}={v}' for k, v in qual.items())} |",
        "",
        "## 파일별",
        "",
        "| 파일 | 쪽 | 텍스트 | 품질 | 문서종류 후보 | 변형 | 교차검증 | 상태 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda x: str(x["file"])):
        lines.append(
            f"| `{r['file']}` | {r['pages']} | {r['text_length']:,} | {r['quality']} "
            f"| **{r['document_kind']}** | {r['variant']} "
            f"| {'예' if r['cross_verified'] else '아니오'} | {r['status']} |"
        )

    reasons = collections.Counter(
        reason for r in rows for reason in r["quarantine_reasons"]  # type: ignore[union-attr]
    )
    lines += ["", "## 격리 사유 분포", "", "| 사유 | 건수 |", "|---|---|"]
    for reason, n in reasons.most_common():
        lines.append(f"| {reason} | {n} |")
    if failures:
        lines += ["", "## 추출 실패", ""]
        lines += [f"- `{s[:12]}`: {why}" for s, why in failures]

    (_OUT / f"{stamp}_식별_검수큐.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Artifact {len(grouped)} / Occurrence {sum(len(v) for v in grouped.values())}")
    print(f"status: {dict(stat)}")
    print(f"kind:   {dict(kinds)}")
    print(f"quality:{dict(qual)}")
    print(f"OK saved: docs/reports/{stamp}_식별_검수큐.md")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
