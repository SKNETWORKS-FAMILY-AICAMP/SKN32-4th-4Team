from scripts.eval.model_compare import evaluate


def _prediction(case_id, verdict, abstained, cited):
    return {
        "id": case_id,
        "model": "model-a",
        "verdict": verdict,
        "abstained": abstained,
        "cited_clauses": cited,
        "quotes": {},
        "reason": "설명",
    }


def test_다섯_안전지표를_각각_계산한다():
    golden = [
        {"id": "a", "expect": {"verdict": "unlikely", "abstained": False, "must_cite": ["E001"]}},
        {"id": "b", "expect": {"verdict": "needs_expert", "abstained": True, "must_cite": []}},
    ]
    rows = evaluate(golden, [
        _prediction("a", "unlikely", False, ["E001"]),
        _prediction("b", "unlikely", False, ["E999"]),
    ])["model-a"]
    assert rows["schema_compliance"] == 1.0
    assert rows["citation_alignment"] == 0.5
    assert rows["grounding_overreach_rate"] == 1.0
    assert rows["appropriate_abstention_rate"] == 0.0
    assert rows["over_abstention_rate"] == 0.0


def test_누락과_스키마위반을_통과로_세지_않는다():
    golden = [{"id": "a", "expect": {"verdict": "needs_expert", "abstained": True, "must_cite": []}}]
    rows = evaluate(golden, [{"id": "a", "model": "model-a"}])["model-a"]
    assert rows["schema_compliance"] == 0.0
    assert rows["missing_or_invalid_ids"] == ["a"]
