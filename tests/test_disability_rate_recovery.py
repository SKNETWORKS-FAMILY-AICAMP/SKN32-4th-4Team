from scripts.eval.recover_disability_rate_candidates import _candidate_from_table


def _doc():
    return {
        "source": {
            "sha256": "a" * 64,
            "insurer": "테스트보험",
            "product_name": "테스트상품",
        }
    }


def test_equal_sequential_item_and_rate_vectors_emit_candidate_only_facts():
    table = {
        "table_id": "p1-1-2열짝짓기",
        "method": "2열짝짓기",
        "is_table": False,
        "reject_why": ["T6 본문 지문 2개"],
        "records": [
            {"cols": {"1": "장해의 분류", "2": "지급률(%)"}},
            {"cols": {"1": "1) 첫째 장해 2) 둘째 장해", "2": "100 60"}},
        ],
    }
    got = _candidate_from_table(_doc(), {"page": 1}, table)

    assert got["accepted_by_invariant"] is True
    assert [fact["payment_rate_percent"] for fact in got["facts"]] == [100, 60]
    assert all(fact["serving_eligible"] is False for fact in got["facts"])
    assert all(fact["citation_eligible"] is False for fact in got["facts"])


def test_page_reading_order_restores_complete_wrapped_descriptions():
    page = {
        "page": 1,
        "text": (
            "장해의 분류\n지급률(%)\n"
            "1) 두 팔의 손목 이상을 잃었을 때\n"
            "2) 한 팔의 관절 하나의 기능에 뚜렷한\n장해를 남긴 때\n"
            "100\n60\n나. 장해판정기준\n"
        ),
    }
    table = {
        "table_id": "p1-1-2열짝짓기",
        "method": "2열짝짓기",
        "is_table": False,
        "records": [
            {"cols": {"1": "장해의 분류", "2": "지급률(%)"}},
            {"cols": {"1": "1) 두 팔의 손목 이상을 2) 한 팔의 관절", "2": "100 60"}},
        ],
    }
    got = _candidate_from_table(_doc(), page, table)

    assert got["accepted_by_invariant"] is True
    assert got["recovery_basis"] == "page_reading_order_item_list_plus_vertical_rate_vector"
    assert got["facts"][1]["classification"] == "한 팔의 관절 하나의 기능에 뚜렷한 장해를 남긴 때"


def test_mismatched_vector_is_rejected():
    table = {
        "records": [
            {"cols": {"1": "장해의 분류", "2": "지급률(%)"}},
            {"cols": {"1": "1) 첫째 장해 2) 둘째 장해", "2": "100"}},
        ]
    }
    got = _candidate_from_table(_doc(), {"page": 1}, table)

    assert got["accepted_by_invariant"] is False
    assert got["checks"]["item_rate_count_equal"] is False
