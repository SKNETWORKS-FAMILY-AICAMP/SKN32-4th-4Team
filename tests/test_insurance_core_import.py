from pathlib import Path

from scripts.db.import_insurance_core import (
    DEFAULT_MANIFESTS,
    DEFAULT_STRUCTURED,
    load_documents,
    main,
    summarize,
)


def test_current_insurance_release_is_reported_but_not_importable():
    documents = load_documents(DEFAULT_STRUCTURED, DEFAULT_MANIFESTS)
    report = summarize(documents)

    assert report["structured_documents"] == 236
    assert report["clauses"] == 22565
    assert report["annexes"] == 1184
    assert report["ready"] is False
    assert report["blockers"]["identification_not_confirmed"] == 236
    assert report["blockers"]["release_approval:candidate"] == 236


def test_apply_refuses_unapproved_source_before_connecting():
    assert main(["--apply", "--dsn", "postgresql://unused"]) == 3
