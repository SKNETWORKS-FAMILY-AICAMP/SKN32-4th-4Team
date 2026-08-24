from scripts.eval.build_annex_shadow import materialize_document


def _doc(clause_text: str, annex_text: str):
    return {
        "source": {"sha256": "a" * 64},
        "clauses": [{
            "ordinal": 7,
            "qualified_no": "보통약관/제7조",
            "text": clause_text,
            "section": "보통약관",
        }],
        "annexes": [{
            "ordinal": 3,
            "label": "[별표1] 질병분류표",
            "text": annex_text,
            "section": "보통약관",
        }],
    }


def test_shadow_rows_are_serving_blocked_and_have_owner():
    resolved, unresolved, owners = materialize_document(
        insurer_dir="test",
        sha12="aaaaaaaaaaaa",
        doc=_doc("보상하지 않습니다. [별표1]", "질병 F04~F09"),
        quarantined=False,
    )
    assert not unresolved
    assert resolved[0]["serving_eligible"] is False
    assert resolved[0]["mentions"][0]["kind"] == "exclude"
    assert owners[0]["owner_status"] == "unique"
    assert owners[0]["owner_clause_ordinal"] == 7


def test_conditional_reference_never_promotes_to_exclude():
    resolved, _, _ = materialize_document(
        insurer_dir="test",
        sha12="aaaaaaaaaaaa",
        doc=_doc("보상하지 않습니다. [별표1] 중에서 회사가 지정한 질병", "질병 F04~F09"),
        quarantined=False,
    )
    assert {item["kind"] for item in resolved[0]["mentions"]} == {"mention"}


def test_quarantine_is_explicit_on_every_row():
    resolved, _, owners = materialize_document(
        insurer_dir="test",
        sha12="aaaaaaaaaaaa",
        doc=_doc("[별표1]", "질병 F04"),
        quarantined=True,
        quarantine_reason="known false link",
    )
    assert resolved[0]["quarantined"] is True
    assert resolved[0]["quarantine_reason"] == "known false link"
    assert owners[0]["quarantine_reason"] == "known false link"
