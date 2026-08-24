"""약관 문서 매니페스트 — 한 행에 계보 전체를 남긴다.

한 행이 답해야 하는 것:

    보험사 → 상품명 → 상품라인(일반/노후/유병력자) → 문서변형(계약전환용/재개용/자녀전환용)
          → 판매 시작·종료일 → 약관 버전(세대·개정) → 약관 PDF URL → 파일 SHA-256

★상품라인과 문서변형은 **다른 축**이다. 한 칸에 합치면 "유병력자 계약전환용"을
표현할 수 없다. 그래서 열을 나눠 둔다.

★확정하지 않는다. 세대는 `generation_candidates` 로만 남기고, 규칙셋이 미검토
(`review_status != "reviewed"`)인 동안에는 무엇도 CONFIRMED 가 되지 않는다.

실행: python -m scripts.identify.build_document_manifest
"""

from __future__ import annotations

import collections
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from app.core.domain.document_identification import Artifact, SourceOccurrence
from app.core.domain.generation import ProductType, load_ruleset
from app.core.errors import InfraError
from app.core.usecases.identify_document import IdentifyDocument
from app.outer.outbound.pdf.extractor import PdfEvidenceExtractor

_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = _ROOT / "data" / "raw" / "fetch_manifest.jsonl"
_RULES = _ROOT / "config" / "generation_profiles.json"
_OUT = _ROOT / "docs" / "reports"
_CATALOG_OUT = _ROOT / "data" / "catalog"

#: 표지의 판/개정 표기. 삼성화재는 `(2501.5)` 형태를 쓴다 — 25년 01월 판으로 읽힌다.
_EDITION = re.compile(r"\((\d{2})(\d{2})\.(\d+)\)")
#: 명시적 날짜 표기.
_YMD = re.compile(r"(20\d{2})[.\-년/\s]+(\d{1,2})[.\-월/\s]+(\d{1,2})")
_YM = re.compile(r"(20\d{2})[.\-년/\s]+(\d{1,2})\s*월")


def _revision_from_cover(cover: str) -> tuple[date | None, str]:
    """표지에서 개정일 후보를 뽑는다. **확정하지 않고 근거 문자열을 함께** 돌려준다."""
    m = _YMD.search(cover)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])), m.group(0)
        except ValueError:
            pass
    m = _YM.search(cover)
    if m:
        try:
            return date(int(m[1]), int(m[2]), 1), m.group(0)
        except ValueError:
            pass
    m = _EDITION.search(cover)
    if m:
        try:
            return date(2000 + int(m[1]), int(m[2]), 1), m.group(0)
        except ValueError:
            pass
    return None, ""


def _as_date(yyyymmdd: str) -> date | None:
    if not yyyymmdd or len(yyyymmdd) != 8 or yyyymmdd == "00000000":
        return None
    try:
        return date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:]))
    except ValueError:
        return None


def main() -> None:
    rules = load_ruleset(_RULES)
    if not _MANIFEST.exists():
        raise InfraError(f"수집 매니페스트가 없습니다: {_MANIFEST}")

    grouped: dict[str, list[SourceOccurrence]] = collections.defaultdict(list)
    saved_of: dict[str, tuple[Path, int, str]] = {}
    for line in _MANIFEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        sha = r["sha256"]
        grouped[sha].append(
            SourceOccurrence(
                artifact_sha256=sha,
                url=r["url"],
                fetched_at=r["fetched_at"],
                product_code=r.get("product_code", ""),
                product_name=r.get("product_name", ""),
                sale_start=r.get("sale_start", ""),
                sale_end=r.get("sale_end", ""),
            )
        )
        saved_of.setdefault(sha, (_ROOT / r["saved_as"], r["bytes"], r["insurer"]))

    extractor = PdfEvidenceExtractor()
    identify = IdentifyDocument()
    rows: list[dict[str, object]] = []

    for sha, occs in grouped.items():
        path, size, insurer = saved_of[sha]
        try:
            ev = extractor.extract(path=path, sha256=sha)
        except InfraError as e:
            rows.append({"sha256": sha, "error": str(e)})
            continue

        artifact = Artifact(sha256=sha, bytes=size, page_count=ev.page_count, quality=ev.quality)
        ident = identify.run(artifact=artifact, occurrences=tuple(occs), evidence=ev)
        cand = ident.candidates[0]

        primary = occs[0]
        ptype, ptype_reasons = rules.classify_product(primary.product_name)
        revision, revision_excerpt = _revision_from_cover(ev.cover_text)
        gens, gen_reasons = rules.generation_candidates(
            product_type=ptype,
            revision_date=revision,
            sale_start=_as_date(primary.sale_start),
            sale_end=_as_date(primary.sale_end),
        )

        rows.append(
            {
                "insurer": insurer,
                "product_name": primary.product_name,
                "product_code": primary.product_code,
                "product_type": ptype.value,
                "variant": cand.variant.value,
                "sale_start": primary.sale_start,
                "sale_end": primary.sale_end,
                "is_discontinued": bool(primary.sale_end and primary.sale_end != "99991231"),
                "document_kind_candidate": cand.document_kind.value,
                "revision_date": revision.isoformat() if revision else None,
                "revision_excerpt": revision_excerpt,
                "generation_candidates": [g.generation for g in gens],
                "generation_confirmed": None,
                "pdf_url": primary.url,
                "all_urls": sorted({o.url for o in occs}),
                "sha256": sha,
                "bytes": size,
                "pages": ev.page_count,
                "identification_status": ident.status.value,
                "quarantine_reasons": list(ident.quarantine_reasons) + ptype_reasons + gen_reasons,
            }
        )

    stamp = date.today().isoformat()
    _CATALOG_OUT.mkdir(parents=True, exist_ok=True)
    out_jsonl = _CATALOG_OUT / f"{stamp}_document_manifest.jsonl"
    with out_jsonl.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    ok_rows = [r for r in rows if "error" not in r]
    settled = [r for r in ok_rows if len(r["generation_candidates"]) == 1]  # type: ignore[arg-type]
    types = collections.Counter(r["product_type"] for r in ok_rows)
    variants = collections.Counter(r["variant"] for r in ok_rows)

    lines = [
        f"# 약관 문서 매니페스트 — {stamp}",
        "",
        f"- 규칙셋: `config/generation_profiles.json` (schema {rules.schema_version}, "
        f"검토상태 **{rules.review_status}**)",
        f"- 대상: 고유 파일 **{len(ok_rows)}건**",
        "- ★**세대 확정 0건.** 규칙셋이 미검토 상태이고, 확정은 사람 검수로만 한다.",
        "",
        "## 한 행에 남기는 것",
        "",
        "보험사 → 상품명 → **상품라인** → **문서변형** → 판매 시작·종료 → 개정일 → "
        "세대후보 → PDF URL → SHA-256",
        "",
        "★상품라인(일반/노후/유병력자)과 문서변형(계약전환용/재개용/자녀전환용)은 **다른 축**이다. "
        "합치면 '유병력자 계약전환용'을 표현할 수 없다.",
        "",
        "## 집계",
        "",
        f"- 상품라인: {' · '.join(f'{k}={v}' for k, v in types.items())}",
        f"- 문서변형: {' · '.join(f'{k}={v}' for k, v in variants.items())}",
        f"- 세대후보가 1개로 좁혀진 건: **{len(settled)} / {len(ok_rows)}**",
        "",
        "## 문서별",
        "",
        "| 보험사 | 상품명 | 라인 | 변형 | 판매시작 | 판매종료 | 개정일 | 세대후보 | SHA-256 | 상태 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(ok_rows, key=lambda x: (str(x["product_name"]), str(x["sha256"]))):
        gens = ",".join(str(g) for g in r["generation_candidates"]) or "-"  # type: ignore[union-attr]
        end = "판매중" if not r["is_discontinued"] else str(r["sale_end"])
        lines.append(
            f"| {r['insurer']} | {str(r['product_name'])[:26]} | {r['product_type']} "
            f"| {r['variant']} | {r['sale_start']} | {end} | {r['revision_date'] or '-'} "
            f"| {gens} | `{str(r['sha256'])[:12]}` | {r['identification_status']} |"
        )
    lines += [
        "",
        "## 세대 규칙셋 출처",
        "",
        "| 세대 | 적용 구간 | 출처 |",
        "|---|---|---|",
    ]
    for p in rules.profiles:
        lines.append(
            f"| {p.label} | {p.effective_from or '~'} ~ {p.effective_to or '현재'} "
            f"| {len(p.sources)}건 |"
        )
    lines += [
        "",
        "★**제도 시행일이며 개별 상품 판매개시일과 다르다.** 판매구간은 세대 후보를 "
        "제거하는 보조 제약으로만 쓰고, 세대를 부여하는 규칙으로 쓰지 않는다.",
        "",
        f"- 매니페스트: `data/catalog/{out_jsonl.name}`",
    ]
    (_OUT / f"{stamp}_문서_매니페스트.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"문서 {len(ok_rows)}건 → {out_jsonl.relative_to(_ROOT)}")
    print(f"  상품라인: {dict(types)}")
    print(f"  문서변형: {dict(variants)}")
    print(f"  세대후보 1개로 좁혀짐: {len(settled)}/{len(ok_rows)}")
    print(f"  규칙셋 검토상태: {rules.review_status} (미검토면 확정 불가)")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_ROOT))
    main()
