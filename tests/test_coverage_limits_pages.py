from __future__ import annotations

import json
from pathlib import Path

from scripts.extract.coverage_limits import _boundary_amount, parse_pages


ROOT = Path(__file__).resolve().parents[1]


def test_boundary_amount_requires_descending_units() -> None:
    assert _boundary_amount("1만", "5천원과 20% 중 큰 금액") == (
        15000,
        "1만5천원과 20% 중 큰 금액",
    )
    assert _boundary_amount("5천", "1만원") is None
    assert _boundary_amount("본문", "5천원") is None


def test_known_kb_split_row_recovers_15000_with_two_page_locator() -> None:
    path = ROOT / "data/extracted/kbinsure/s5_pymupdf-1.28.0/224bd44994c3.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    pages = [page for page in doc["pages"] if page["page"] in {5, 6}]
    parsed = parse_pages(pages)
    recovered = [record for record in parsed[1] if record.get("쪽경계_복구")]
    assert len(recovered) == 1
    assert recovered[0]["공제액"] == 15000
    assert recovered[0]["기관종별"] == "병원급"
    assert recovered[0]["근거_locator"]["page_from"] == 5
    assert recovered[0]["근거_locator"]["page_to"] == 6
    assert recovered[0]["쪽경계_복구"]["value_invention"] is False
