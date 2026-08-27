# -*- coding: utf-8 -*-
"""reconcile_manifest 회귀 — 격리 파일(saved_as 가 insurance_terms/ 밖을 가리키는
행)을 "파일 없음"으로 오판해 지우면 안 된다.

실측 2026-08-25: 이 결함으로 격리 336건이 실제로 한 번 삭제됐다(git으로 복원).
`insurance_terms/{보험사}/` 안에서만 파일을 찾던 옛 코드가, `classify_documents.py`가
파일을 `excluded/{사유}/{보험사}/` 로 옮기고 `saved_as` 를 정확히 갱신해도
그 새 위치를 볼 줄 몰라서 벌어졌다.
"""

from __future__ import annotations

import json

from scripts.crawl import reconcile_manifest as rm


def _write_manifest(path, rows):
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_row_pointing_outside_insurance_terms_is_kept_if_file_exists(tmp_path, monkeypatch):
    root = tmp_path
    raw = root / "data" / "raw" / "insurance_terms" / "testco"
    excluded = root / "data" / "raw" / "excluded" / "여행실손" / "testco"
    manifests = root / "data" / "raw" / "manifests"
    raw.mkdir(parents=True)
    excluded.mkdir(parents=True)
    manifests.mkdir(parents=True)

    #: 격리돼 excluded/ 로 옮겨진 파일. insurance_terms/testco/ 안에는 없다.
    moved = excluded / "abc123_여행자보험.pdf"
    moved.write_bytes(b"%PDF-1.4 fake")

    m = manifests / "testco.jsonl"
    _write_manifest(
        m,
        [
            {
                "insurer": "testco",
                "saved_as": "data/raw/excluded/여행실손/testco/abc123_여행자보험.pdf",
                "sha256": "abc123",
                "excluded_reason": "여행실손",
            }
        ],
    )

    monkeypatch.setattr(rm, "_ROOT", root)
    monkeypatch.setattr(rm, "_MANIFESTS", manifests)
    monkeypatch.setattr(rm, "_RAW", root / "data" / "raw" / "insurance_terms")

    import sys

    old_argv = sys.argv
    try:
        sys.argv = ["reconcile_manifest.py"]  # not --dry-run: 실제로 쓴다
        rm.main()
    finally:
        sys.argv = old_argv

    rows = [json.loads(line) for line in m.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1, "excluded/ 로 옮겨진 격리 행을 파일없음으로 오판해 지웠다"
    assert rows[0]["sha256"] == "abc123"


def test_row_whose_file_is_truly_gone_is_still_dropped(tmp_path, monkeypatch):
    root = tmp_path
    raw = root / "data" / "raw" / "insurance_terms" / "testco"
    manifests = root / "data" / "raw" / "manifests"
    raw.mkdir(parents=True)
    manifests.mkdir(parents=True)

    m = manifests / "testco.jsonl"
    _write_manifest(
        m,
        [
            {
                "insurer": "testco",
                "saved_as": "data/raw/insurance_terms/testco/gone.pdf",
                "sha256": "deadbeef",
            }
        ],
    )

    monkeypatch.setattr(rm, "_ROOT", root)
    monkeypatch.setattr(rm, "_MANIFESTS", manifests)
    monkeypatch.setattr(rm, "_RAW", root / "data" / "raw" / "insurance_terms")

    import sys

    old_argv = sys.argv
    try:
        sys.argv = ["reconcile_manifest.py"]
        rm.main()
    finally:
        sys.argv = old_argv

    rows = [json.loads(line) for line in m.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows == [], "실제로 사라진 파일의 행은 여전히 지워져야 한다(원래 §1 사례)"
