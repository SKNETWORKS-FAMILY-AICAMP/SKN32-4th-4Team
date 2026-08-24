import json

from app.core import candidate_fact_registry as registry


def test_real_candidate_sources_are_connected_and_shadow_only():
    status = registry.check_candidate_fact_sources()

    assert status["configured"] is True
    assert status["ready"] is True
    assert status["sources"] == 2
    assert status["rows"] == 1306
    assert status["facts"] == 8622
    assert all(source["shadow_only"] is True for source in status["details"])


def test_eligible_candidate_row_fails_closed(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate.jsonl"
    candidate.write_text(
        json.dumps({
            "approval_status": "approved",
            "serving_eligible": True,
            "citation_eligible": True,
            "facts": [{"fact_type": "bad"}],
        }) + "\n",
        encoding="utf-8",
    )
    import hashlib

    config = tmp_path / "registry.json"
    config.write_text(json.dumps({
        "schema_version": "candidate-fact-sources-v1",
        "sources": [{
            "source_id": "bad",
            "fact_type": "bad",
            "path": str(candidate.relative_to(tmp_path)),
            "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
            "rows": 1,
            "facts": 1,
            "serving_eligible": False,
            "citation_eligible": False,
        }],
    }), encoding="utf-8")
    accepted = tmp_path / "accepted.json"
    accepted.write_text(json.dumps({"candidate_fact_registry": "registry.json"}), encoding="utf-8")
    monkeypatch.setattr(registry, "ROOT", tmp_path)
    monkeypatch.setattr(registry, "ACCEPTED_CONFIG", accepted)

    status = registry.check_candidate_fact_sources()
    assert status["ready"] is False
    assert "eligible/approved row" in status["reason"]
