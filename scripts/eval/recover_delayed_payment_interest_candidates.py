"""Specialized recovery for the nine rejected delayed-payment interest tables.

The generic grid is unsafe because it split ``보험계약대출이율`` and the
``+ 가산이율(N%)`` suffix into different cells.  The page's reading-order text
preserves the four period/rate rows, so this extractor requires the complete
four-row signature and emits candidate-only facts.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INPUT = ROOT / "data" / "extracted"
OUTPUT = ROOT / "data" / "candidates" / "s7_delayed_payment_interest"

SIGNATURES = [
    ("지급기일의 다음날부터 30일 이내 기간", "보험계약대출이율", 0),
    ("지급기일의 31일 이후부터 60일 이내 기간", "보험계약대출이율 + 가산이율(4%)", 4),
    ("지급기일의 61일 이후부터 90일 이내 기간", "보험계약대출이율 + 가산이율(6%)", 6),
    ("지급기일의 91일 이후 기간", "보험계약대출이율 + 가산이율(8%)", 8),
]


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    docs = 0
    pages = 0
    for path in sorted(INPUT.rglob("*.json")):
        if not path.parent.name.startswith("s5_"):
            continue
        docs += 1
        doc = json.loads(path.read_text(encoding="utf-8"))
        source = doc.get("source") or {}
        sha256 = str(source.get("sha256") or path.stem)
        for page in doc.get("pages") or []:
            pages += 1
            compact = norm(page.get("text") or "")
            required = [norm(period) for period, _, _ in SIGNATURES]
            required += [norm("보험계약대출이율+가산이율(4%)"), norm("보험계약대출이율+가산이율(6%)"), norm("보험계약대출이율+가산이율(8%)")]
            if not all(token in compact for token in required):
                continue
            coords = page.get("tables_coords") or []
            rejected_multi = [
                table for table in coords
                if table.get("is_table") is False and int(table.get("cols") or 0) >= 3
            ]
            if not rejected_multi:
                continue
            rows.append({
                "schema_version": "s7-delayed-payment-interest-candidate-v1",
                "source_sha256": sha256,
                "source_sha12": sha256[:12],
                "insurer": source.get("insurer"),
                "product_name": source.get("product_name"),
                "page": page.get("page"),
                "rejected_table_ids": [table.get("table_id") for table in rejected_multi],
                "recovery_basis": "complete four-row reading-order signature; split-cell grid remains blocked",
                "serving_eligible": False,
                "citation_eligible": False,
                "approval_status": "candidate",
                "facts": [
                    {
                        "fact_type": "delayed_payment_interest",
                        "period": period,
                        "base_rate": "보험계약대출이율",
                        "additional_rate_percent": additional,
                        "display_rate": display,
                    }
                    for period, display, additional in SIGNATURES
                ],
            })

    jsonl = OUTPUT / "candidates.jsonl"
    with jsonl.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        "schema_version": "s7-delayed-payment-interest-summary-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "documents_scanned": docs,
        "pages_scanned": pages,
        "candidate_pages": len(rows),
        "candidate_facts": sum(len(row["facts"]) for row in rows),
        "release_policy": "candidate_only; serving/citation blocked until human approval",
        "candidate_jsonl": str(jsonl),
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
