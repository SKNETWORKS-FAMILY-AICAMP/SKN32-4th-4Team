from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHADOW_TOKEN = "ocr_shadow48"


def test_shadow_directory_is_not_referenced_by_serving_or_index_code():
    roots = [ROOT / "app", ROOT / "scripts" / "index", ROOT / "scripts" / "db"]
    offenders = []
    for root in roots:
        assert root.is_dir(), f"isolation scan root is missing: {root}"
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".py", ".json", ".sql", ".yaml", ".yml"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if SHADOW_TOKEN in text or ("ocr_shadow" in text and "data/eval" in text):
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_shadow_directory_is_not_an_accepted_extraction_source():
    accepted = (ROOT / "config" / "accepted_extraction.json").read_text(encoding="utf-8")
    assert SHADOW_TOKEN not in accepted
