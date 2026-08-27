from scripts.eval.a1_gap_causes import cause_proxy, content_reachability


def _row(**kwargs):
    base = {
        "risk_class": "business_signal",
        "gap_context": "between_covered",
        "text_preview": "회사는 통원의료비를 보상합니다.",
    }
    base.update(kwargs)
    return base


def test_reachability_finds_material_already_in_corpus():
    text = "가나다라마바사아자차카타파하" * 8
    got = content_reachability(text, "앞" + text + "뒤", width=24)
    assert got["ratio"] == 1.0


def test_between_gap_with_unreached_text_is_content_loss_candidate():
    cause, _ = cause_proxy(_row(), {"ratio": 0.0})
    assert cause == "content_loss_candidate"


def test_statute_reference_is_not_attached_as_policy_clause():
    cause, _ = cause_proxy(_row(text_preview="참 고 【법규12】 의료법 제3조"), {"ratio": 0.0})
    assert cause == "statute_reference_proxy"


def test_front_matter_is_separate_from_between_gap():
    cause, _ = cause_proxy(
        _row(gap_context="before_first_covered", text_preview="가입자 유의사항"),
        {"ratio": 0.0},
    )
    assert cause == "front_matter_proxy"


def test_annex_material_precedes_generic_content_loss():
    cause, _ = cause_proxy(_row(text_preview="별표 1 장해의 분류 지급률"), {"ratio": 0.0})
    assert cause == "annex_boundary_candidate"
