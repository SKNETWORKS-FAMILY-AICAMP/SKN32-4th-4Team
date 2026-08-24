from scripts.eval.outside_clause_pages import classify_page, review_sample


def test_blank_proxy_has_no_business_signal():
    got = classify_page({"text": "  \n", "tables_coords": []}, page_no=1, total_pages=10)
    assert got["risk_class"] == "blank_or_image_only_proxy"
    assert got["signal_count"] == 0


def test_selfpay_amount_is_business_signal():
    got = classify_page(
        {"text": "자기부담금은 15,000원입니다.", "tables_coords": []},
        page_no=7,
        total_pages=10,
    )
    assert got["risk_class"] == "business_signal"
    assert got["signals"]["business_term"] is True
    assert got["signals"]["money"] is True


def test_table_is_business_signal_even_with_no_text():
    got = classify_page(
        {"text": "", "tables_coords": [{"method": "선"}]},
        page_no=5,
        total_pages=10,
    )
    assert got["risk_class"] == "business_signal"
    assert got["trusted_table_count"] == 1


def test_review_sample_includes_low_risk_stratum():
    rows = [
        {"risk_class": "business_signal", "gap_context": "between_covered", "insurer_dir": "a", "sha12": "a", "page": 1},
        {"risk_class": "blank_or_image_only_proxy", "gap_context": "before_first_covered", "insurer_dir": "a", "sha12": "b", "page": 1},
    ]
    got = review_sample(rows, 2)
    assert {row["risk_class"] for row in got} == {"business_signal", "blank_or_image_only_proxy"}
    assert all(row["stratum_population"] == 1 for row in got)
