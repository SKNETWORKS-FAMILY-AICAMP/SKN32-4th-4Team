from scripts.extract.to_clauses import _split_paragraphs


def test_number_inside_sentence_is_not_split_into_an_item():
    body = "제1조(보험금)\n① 보험금은 사고일로부터 1. 지급하는 것이 아니라 심사 후 지급합니다."
    paragraphs, unresolved = _split_paragraphs(body, "제1조(보험금)")

    assert unresolved == 0
    assert len(paragraphs) == 2
    assert paragraphs[1]["paragraph_no"] == 1
    assert paragraphs[1]["items"] == []
    assert "1. 지급하는 것이 아니라" in paragraphs[1]["text"]


def test_line_head_item_is_kept_without_splitting_its_sentence_body():
    body = (
        "제1조(보험금)\n"
        "① 회사는 다음 각 호를 따릅니다.\n"
        "1. 첫째 기준이며 문장 안의 2. 표기는 새 호가 아닙니다.\n"
        "2. 둘째 기준입니다."
    )
    paragraphs, unresolved = _split_paragraphs(body, "제1조(보험금)")

    assert unresolved == 0
    items = paragraphs[1]["items"]
    assert [item["item_no"] for item in items] == ["1", "2"]
    first = paragraphs[1]["text"][items[0]["offset"] : items[0]["offset"] + items[0]["length"]]
    assert "문장 안의 2. 표기는 새 호가 아닙니다." in first
