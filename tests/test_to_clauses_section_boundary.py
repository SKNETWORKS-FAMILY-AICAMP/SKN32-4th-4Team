# -*- coding: utf-8 -*-
"""S1/S2 원인규명 리포트 §9-1 "부 경계 검출 개선" 의 회귀 테스트.

`docs/reports/2026-08-03_2330_구조결함_S1_S2_원인규명.md` §9 항목 1 — 검증 기준은
"A-B-A 가 줄어드는지"(정답셋 불필요). 실측(**전량 1,367문서**, 수정 전/후 재빌드
직접 비교 — 캐시된 산출물이 아니라 코드를 실행해서 잰 값):

    S1_aba_reentry       4,613 →   982  (-78.7%)
    S3_embedded_header     117 →    68  (-41.9%, 4·5번이 만든 부작용까지 해소)
    gated_clause_count   8,030 → 1,849  (-77.0%, 6,181개 조항이 새로 인용 가능해짐)

열 가지 결함을 고쳤고(사람 표본 검증 지시로 2라운드 진행), 이 파일은 그중 코드
한 줄로 표현 가능한 것들이 **개별적으로 load-bearing** 함을 증명한다(가짜로
통과하는 픽스처가 아님을 보이는 게 목적 — D6 뮤턴트 테스트와 같은 취지).

  8. 로마자·코드 접미사(`단체취급특별약관(Ⅰ)`) — 실측 74쪽, 캡처 그룹 안에
     넣어 `(Ⅰ)`·`(Ⅱ)` 를 서로 다른 section 으로 구분.
  9. 콜론 예시문(`사례 : A씨는 …특별약관`) — 실측 22쪽(현대해상), 콜론도
     문장부호로 판정.
  10. ★가지번호(`제N조의M`)가 본번호와 같은 수로 잡혀 A-B-A 오탐 — 2라운드
      최대 효과(aba -26.7%p 추가). `제5조`=5, `제5조의2`=5.002 로 분리.

  1. `_section_name_at` — 페이지 단위 `section_of_page` 는 그 쪽의 첫 매치에서
     멈춰 같은 쪽 안의 부 변경을 놓친다. 오프셋 단위로 고쳤다.
  2. `structure_faults` 의 `by_kind` — 번호 체계만 보고 부(部)를 안 봐서 새 특약의
     번호 재시작을 재진입으로 오판할 수 있었다. `(kind, section)` 로 가른다.
  3. `_REF_TAIL` — 제목이 있으면 무조건 진짜 머리로 봐서, "제3조(제목)의 규정에도
     불구하고" 처럼 **자기 조를 도로 인용**하며 제목을 되풀이하는 문장을 걸러내지
     못했다. 실측상 이게 A-B-A 오탐의 **다수 원인**이었다(원래 가설이던 "부 경계
     오검출" 이 아니라 — 진짜 재조사 표본 10문서가 전부 `same_section` 이었다).
     ★단, 제목에 중첩 괄호가 있으면(`납입최고(독촉)와 계약의 해지`) 제목 캡처가
     잘려 꼬리가 새므로 그 경우는 건너뛴다(회귀 방지 — 실측 15d0cf40e56c 등에서
     진짜 조 27개가 몽땅 걸릴 뻔했다).
  4. `struct_audit.py` 의 S3(파묻힌 머리) 검사 — 3번과 같은 판정을 안 써서, 3번이
     `heads` 에서 뺀 자기참조 문장이 부모 조 본문에 남으면 S3 가 다시 걸렸다
     (실측: 117 → 6,650 로 튄 뒤 정합시켜 되돌림). `_ARTICLE`/`_REF_TAIL` 을
     `to_clauses.py` 에서 그대로 import 해서 판정을 통일했다.
  5. `_REF_TAIL` 확장 — 쉼표 나열(`제10조, 제12조 및 …`)·항 기호 뒤 참조
     (`제12조(제목) ①에서 정한 …`) 를 원래 규칙이 못 잡았다. 앞의 `,`·`①-⑳`
     를 허용하도록 넓혔다.
  6. `_checklist_entry_count`(신규 목차 신호) — "약관의 핵심 체크항목 쉽게 찾기"
     류 안내 페이지(실측 650문서, 47.6%)가 쪽번호 없는 "번호 항목 → 조문 참조"
     나열이라 기존 목차 신호가 못 잡았다. 조 머리 뒤에 진짜 본문이 없는(20자
     미만) 항목이 몰려 있으면 목차로 본다 — 코덱스가 예전에 반례로 든 관계법령
     부록(흥국생명 `76e173e5747b` p131·NH손보 `ba396b382a73` p287)으로 재검증해
     안 걸리는 것까지 확인했다.
  7. `_TYPE_LABEL`(신규 부 경계) — `《질병통원형》` 처럼 특별약관·특약 접미사
     없는 보장유형 라벨(삼성생명 전용, 195/1,367문서)을 `_SECTION_TITLE` 이
     못 잡아 5개 보장유형의 「제1~4조」 보일러플레이트가 전부 `머리말` 하나로
     뭉개졌었다. 단독 줄일 때만 인정한다 — 본문 중간 인용이 더 흔하다(전체
     《》 출현의 49%).
"""
from scripts.eval.struct_audit import structure_faults
from scripts.extract.to_clauses import build


def _doc(*page_texts: str) -> dict:
    return {
        "pages": [{"page": i + 1, "text": t} for i, t in enumerate(page_texts)],
        "source": "test.pdf",
        "stats": {"pages": len(page_texts)},
    }


def _layout_line(text: str, y0: float, bold: bool = False, size: float = 10.0) -> dict:
    return {"text": text, "size": size, "bold": bold, "bbox": [92.2, y0, 92.2 + len(text) * 6, y0 + 10]}


def _doc_with_layout(*pages: tuple[str, list[dict]]) -> dict:
    """s5L 형(레이아웃 포함) 문서 픽스처. `pages` 원소는 `(text, layout줄목록)`."""
    return {
        "pages": [{"page": i + 1, "text": t, "layout": lay} for i, (t, lay) in enumerate(pages)],
        "source": "test.pdf",
        "stats": {"pages": len(pages)},
    }


# ────────────────────────────────────────────────────────────────
# 1) 오프셋 단위 부 이름 배정
# ────────────────────────────────────────────────────────────────
def test_mid_page_section_change_is_attributed_to_the_second_title_not_the_first():
    #: 실측(흥국화재 0d3d8bc38d21 p76) 재현 — **둘 다** 독립적으로 유효한 특약
    #: 제목인 줄이 같은 쪽에 연달아 온다("비급여 실손의료비 특별약관" 의 마지막
    #: 조 다음, 새 특약 "실손의료비보장 계약전환제도Ⅲ 특별약관" 이 같은 쪽에서
    #: 시작). 옛 `section_of_page` 는 그 쪽에서 **첫 매치를 찾자마자 멈춰서**
    #: (`break`) 뒤의 조들도 전부 첫 특약 이름을 뒤집어쓴다.
    page = (
        "비급여 실손의료비 특별약관\n"
        "제9조(보상하지 않는 사항)\n본문.\n"
        "실손의료비보장 계약전환제도Ⅲ 특별약관\n"
        "제1조(특별약관의 적용 및 방법)\n본문.\n"
        "제2조(전환후계약의 보장종목)\n본문.\n"
    )
    built = build(_doc(page))
    sections = {c["clause_no"]: c["section"] for c in built["clauses"]}
    assert sections == {
        "제9조": "비급여 실손의료비 특별약관",
        "제1조": "실손의료비보장 계약전환제도Ⅲ 특별약관",
        "제2조": "실손의료비보장 계약전환제도Ⅲ 특별약관",
    }, (
        "페이지 단위 section_of_page 로 되돌아가면 제1·2조도 첫 매치인 "
        "'비급여 실손의료비 특별약관'을 뒤집어쓴다"
    )


# ────────────────────────────────────────────────────────────────
# 2) structure_faults 의 (kind, section) 분리
# ────────────────────────────────────────────────────────────────
def test_structure_faults_does_not_flag_reentry_across_different_sections():
    #: 특약 X 의 제1~2조 다음 특약 Y 가 제1조부터 재시작 — kind 만 보면
    #: "1 → 2 → 1" 이 A-B-A 처럼 보인다. section 이 다르면 재진입이 아니다.
    blocks = [
        {"no": 1, "kind": "article", "text": "특약X 제1조", "section": "특약X"},
        {"no": 2, "kind": "article", "text": "특약X 제2조", "section": "특약X"},
        {"no": 1, "kind": "article", "text": "특약Y 제1조", "section": "특약Y"},
        {"no": 2, "kind": "article", "text": "특약Y 제2조", "section": "특약Y"},
    ]
    faults = structure_faults(blocks)
    assert faults["S1_aba_reentry"] == 0
    assert faults["gated_ordinals"] == []


def test_structure_faults_still_flags_reentry_within_the_same_section():
    #: 같은 부 안에서 번호가 되풀이되면 여전히 걸려야 한다 — 회귀 방지.
    blocks = [
        {"no": 1, "kind": "article", "text": "제1조", "section": "특약X"},
        {"no": 2, "kind": "article", "text": "제2조", "section": "특약X"},
        {"no": 1, "kind": "article", "text": "제1조 재진입", "section": "특약X"},
    ]
    faults = structure_faults(blocks)
    assert faults["S1_aba_reentry"] == 1


def test_structure_faults_without_section_key_behaves_like_before():
    #: `section` 을 안 넘기는 옛 호출부(`load_v5`)는 전부 `None` 으로 뭉쳐
    #: **예전과 똑같이** kind 만으로 갈라야 한다(하위호환).
    blocks = [
        {"no": 1, "kind": "article", "text": "제1조"},
        {"no": 2, "kind": "article", "text": "제2조"},
        {"no": 1, "kind": "article", "text": "제1조 재진입"},
    ]
    faults = structure_faults(blocks)
    assert faults["S1_aba_reentry"] == 1


# ────────────────────────────────────────────────────────────────
# 3) 제목이 있어도 참조 꼬리면 머리가 아니다 (+ 중첩 괄호 회귀 방지)
# ────────────────────────────────────────────────────────────────
def test_self_reference_with_repeated_title_is_not_a_new_head():
    #: 실측(0d3d8bc38d21 p76) 재현 — "제3조(제목)의 규정에도 불구하고" 는
    #: 자기 조를 도로 인용하는 문장이지 새 조가 아니다.
    page = (
        "제1조(적용범위)\n본문.\n"
        "제2조(용어의 정의)\n본문.\n"
        "제3조(전환후계약의 보장개시일)\n본문.\n"
        "제4조(계약 전 알릴 의무)\n본문.\n"
        "제3조(전환후계약의 보장개시일)의 규정에도 불구하고 다음 각 호에\n"
        "해당하면 그러하지 아니합니다.\n"
        "제5조(계약전환의 무효)\n본문.\n"
    )
    built = build(_doc(page))
    nos = [c["clause_no"] for c in built["clauses"]]
    assert nos.count("제3조") == 1, f"자기참조가 새 머리로 잡혔다: {nos}"
    assert built["structure_faults"]["S1_aba_reentry"] == 0


def test_titled_head_with_nested_parens_in_title_is_not_wrongly_rejected():
    #: 회귀 방지 — 제목 안에 중첩 괄호가 있으면(`납입최고(독촉)와 계약의 해지`)
    #: `_ARTICLE` 의 제목 캡처가 `독촉` 에서 잘리고 남은 `와 계약의 해지)` 가
    #: 꼬리로 새 나가 `_REF_TAIL` 의 `와\s` 에 걸린다. 이 조는 **진짜 머리**라
    #: 걸러지면 안 된다(실측 15d0cf40e56c: 이 유형 조가 21곳에서 통째로 빠질 뻔).
    page = (
        "제1조(적용범위)\n본문.\n"
        "제27조(보험료의 납입이 연체되는 경우 납입최고(독촉)와 계약의 해지)\n"
        "① 회사는 보험료가 연체된 경우 …\n"
    )
    built = build(_doc(page))
    nos = [c["clause_no"] for c in built["clauses"]]
    assert "제27조" in nos, f"중첩 괄호 제목의 진짜 머리가 참조로 오판돼 빠졌다: {nos}"


# ────────────────────────────────────────────────────────────────
# 4) S3(파묻힌 머리)가 3번과 같은 판정을 쓰는지
# ────────────────────────────────────────────────────────────────
def test_struct_audit_embedded_header_does_not_flag_self_reference():
    #: 실측(전량 재빌드) — 3번을 넣고 나서 struct_audit 의 S3 가 117 → 6,650 으로
    #: 튀었다. `heads` 에서 뺀 자기참조 문장이 부모 조 본문에 남는데, S3 가 옛
    #: 단순 정규식(제목·참조꼬리 판단 없음)을 그대로 써서 다시 걸렸기 때문이다.
    blocks = [{
        "no": 3, "kind": "article", "section": "특약X",
        "text": (
            "제3조(전환후계약의 보장개시일)\n① 회사는 …\n"
            "제3조(전환후계약의 보장개시일) 제2항에도 불구하고 …\n"
        ),
    }]
    faults = structure_faults(blocks)
    assert faults["S3_embedded_header"] == 0, (
        "자기참조 문장이 파묻힌 머리로 오탐됐다 — to_clauses._REF_TAIL 판정과 어긋났다"
    )


def test_struct_audit_embedded_header_still_flags_a_real_swallowed_clause():
    #: 회귀 방지 — 진짜 파묻힌 조항(경계를 놓쳐 삼켜진 것)은 여전히 걸려야 한다.
    blocks = [{
        "no": 3, "kind": "article", "section": "특약X",
        "text": (
            "제3조(전환후계약의 보장개시일)\n① 회사는 …\n"
            "제4조(전환전계약의 계약 전 알릴 의무 등)\n① 계약자 또는 피보험자는 …\n"
        ),
    }]
    faults = structure_faults(blocks)
    assert faults["S3_embedded_header"] == 1


# ────────────────────────────────────────────────────────────────
# 5) 체크리스트형 안내 목차
# ────────────────────────────────────────────────────────────────
def test_checklist_style_guide_page_is_recognized_as_toc():
    #: 실측(삼성생명 `015c910c03ae` p5) 재현 — 쪽번호 없이 "번호 항목 → 조문"만
    #: 나열하는 안내 페이지. 조 머리 뒤에 진짜 본문이 없다.
    page = "\n".join(
        f"{i}. \n항목{i} \n \n \n \n \n제{i + 2}조(제목{i}) \n \n \n \n \n"
        for i in range(1, 7)
    )
    built = build(_doc(page, "제1조(본문)\n본문 내용입니다.\n"))
    assert built["clauses"] == [] or all(
        c["locator"]["page_from"] != 1 for c in built["clauses"]
    ), "체크리스트 안내 쪽이 목차로 안 걸려 가짜 조항이 만들어졌다"


def test_real_statute_appendix_is_not_flagged_as_checklist_toc():
    #: 회귀 방지 — 코덱스가 예전에 폐기시킨 밀집도 신호의 반례였던 관계법령
    #: 부록 모양(조 머리 + 진짜 본문이 반복)은 체크리스트로 오판되면 안 된다.
    page = "\n".join(
        f"제{250 + i}조(죄명{i})\n① 사람을 해친 자는 사형, 무기 또는 5년 이상의 징역에 처한다.\n"
        for i in range(6)
    )
    built = build(_doc(page))
    assert len(built["clauses"]) == 6, "관계법령 부록의 진짜 조항이 체크리스트로 오판돼 빠졌다"


# ────────────────────────────────────────────────────────────────
# 6) 《유형명》 부 경계
# ────────────────────────────────────────────────────────────────
def test_type_label_bracket_is_recognized_as_section_boundary():
    #: 실측(삼성생명 `6f5e9d40f620`) 재현 — 특별약관·특약 접미사 없는 보장유형
    #: 라벨이 부 경계로 안 잡혀 여러 유형의 「제3·4조」가 전부 `머리말` 하나로
    #: 뭉개졌다.
    page = (
        "《질병입원형》\n"
        "제3조[보상내용]\n본문.\n"
        "제4조[보상하지 않는 사항]\n본문.\n"
        "《질병통원형》\n"
        "제3조[보상내용]\n본문.\n"
        "제4조[보상하지 않는 사항]\n본문.\n"
    )
    built = build(_doc(page))
    sections = {(c["clause_no"], c["ordinal"]): c["section"] for c in built["clauses"]}
    values = list(sections.values())
    assert values[0] == values[1] == "질병입원형"
    assert values[2] == values[3] == "질병통원형"
    assert built["structure_faults"]["S1_aba_reentry"] == 0, (
        "부가 갈렸는데도 제3·4조 재시작이 A-B-A 로 오탐됐다"
    )


def test_type_label_used_mid_sentence_is_not_treated_as_boundary():
    #: 회귀 방지 — 실측(전체 《》 출현의 49%)상 더 흔한 쓰임은 본문 중간 인용
    #: ("《질병급여형》 제3조(보상내용)에 대하여…")이다. 단독 줄이 아니면 안 잡는다.
    page = (
        "제1조(적용범위)\n"
        "① 이 계약의 연간 보험가입금액은 《질병급여형》 제3조(보상내용)에 대하여 적용합니다.\n"
    )
    built = build(_doc(page))
    sections = {c["clause_no"]: c["section"] for c in built["clauses"]}
    assert sections["제1조"] == "머리말", (
        "본문 중간의 《유형명》 인용이 부 경계로 잘못 인식됐다"
    )


# ────────────────────────────────────────────────────────────────
# 7) 로마자·코드 접미사가 붙은 부 제목 / 콜론 예시문
# ────────────────────────────────────────────────────────────────
def test_section_title_with_roman_numeral_suffix_is_recognized():
    #: 실측(kbinsure 등 74쪽) — 같은 특약의 개정판이 `단체취급특별약관(Ⅰ)`
    #: `단체취급특별약관(Ⅱ)` 처럼 로마자로 갈린다. 접미사 바로 뒤(`\s*$`)를
    #: 요구하던 원래 규칙은 이 괄호 때문에 부 경계를 통째로 놓쳤다.
    page = (
        "단체취급특별약관(Ⅰ)\n"
        "제1조(적용대상)\n본문.\n"
        "단체취급특별약관(Ⅱ)\n"
        "제1조(적용대상)\n본문.\n"
    )
    built = build(_doc(page))
    sections = [c["section"] for c in built["clauses"]]
    assert sections == ["단체취급특별약관(Ⅰ)", "단체취급특별약관(Ⅱ)"]
    assert built["structure_faults"]["S1_aba_reentry"] == 0


def test_section_title_with_unrelated_trailing_text_is_still_rejected():
    #: 회귀 방지 — 진짜 한글 예외 문구가 붙은 줄은 여전히 거부해야 한다
    #: (로마자·숫자만 든 좁은 문자 클래스라 안 걸린다).
    line = "이 특약(다만 사기 목적인 경우 제외)"
    import re

    from scripts.extract.to_clauses import _SECTION_TITLE
    assert _SECTION_TITLE.match(line) is None


def test_example_sentence_with_colon_is_not_a_section_title():
    #: 실측(현대해상 22쪽) — 안내책자의 예시문("사례 : A씨는 …특별약관")이 쉼표·
    #: 마침표가 없어서 부 제목 판정을 통과할 뻔했다. 콜론도 문장부호로 본다.
    page = (
        "사례 : A씨는 해외에서 병원치료를 받고 귀국하여 실손의료(갱신형)보장 특별약관\n"
        "제1조(보험금의 지급사유)\n본문.\n"
    )
    built = build(_doc(page))
    sections = {c["clause_no"]: c["section"] for c in built["clauses"]}
    assert sections["제1조"] == "머리말", (
        "콜론 예시문이 부 제목으로 오인식됐다"
    )


# ────────────────────────────────────────────────────────────────
# 8) 가지번호(`제N조의M`)를 본번호와 구분
# ────────────────────────────────────────────────────────────────
def test_clause_num_distinguishes_branch_number_from_base():
    from scripts.extract.to_clauses import _clause_num

    assert _clause_num("제5조") == 5
    assert _clause_num("제5조의2") == 5.002
    assert _clause_num("제5조") != _clause_num("제5조의2")
    assert _clause_num("4-1.") == 4.001
    assert _clause_num("4.") == 4


def test_article_and_its_branch_do_not_trigger_false_reentry():
    #: 실측(`e60978d0aad7`) 재현 — 제5조 → 제3조 → 제5조의2 순서가 옛 규칙으론
    #: `5→3→5` 로 보여 A-B-A 재진입 오탐이었다. 가지번호는 본번호와 다른
    #: 수여야 한다.
    blocks = [
        {"no": 5, "kind": "article", "text": "제5조", "section": "X"},
        {"no": 3, "kind": "article", "text": "제3조", "section": "X"},
        {"no": 5.002, "kind": "article", "text": "제5조의2", "section": "X"},
    ]
    faults = structure_faults(blocks)
    assert faults["S1_aba_reentry"] == 0


# ────────────────────────────────────────────────────────────────
# 9) 로마자·마침표 부 제목이 줄이 갈려도 잡힘 + 러닝헤더에 안 덮임
# ────────────────────────────────────────────────────────────────
def test_roman_dot_title_split_across_two_lines_is_recognized():
    #: 실측(현대해상 `2532b4e1d643` p39) 재현 — 로마자와 제목이 줄이 갈린다.
    page = (
        "Ⅰ.\n상해입원실손의료비(갱신형)보장\n"
        "제1조 (보험금의 지급사유)\n본문.\n"
        "Ⅱ.\n상해통원실손의료비(갱신형)보장\n"
        "제1조 (보험금의 지급사유)\n본문.\n"
    )
    built = build(_doc(page))
    sections = [c["section"] for c in built["clauses"]]
    assert sections == ["Ⅰ. 상해입원실손의료비(갱신형)보장", "Ⅱ. 상해통원실손의료비(갱신형)보장"]


def test_running_header_section_line_does_not_override_roman_dot_label():
    #: 실측(현대해상 `2532b4e1d643` p43) 재현 — 매 쪽 맨 위에 `보통약관` 러닝헤더가
    #: 반복되고 바로 뒤에 쪽번호가 온다. 이게 진짜 부 경계로 잡히면 로마숫자 부
    #: 라벨을 도로 덮어써서, 사람 표본 검증에서 제1→3→1→3→1 처럼 번호가
    #: 되풀이되는데 section 이 셋 다 `보통약관`인 오탐이 났다.
    page1 = "Ⅰ.\n상해입원실손의료비(갱신형)보장\n제1조 (보험금의 지급사유)\n본문.\n"
    page2 = "보통\n약관\n보통약관\n43\n제3조 (보험금을 지급하지 않는 사유)\n본문.\n"
    built = build(_doc(page1, page2))
    sections = [c["section"] for c in built["clauses"]]
    assert sections == [
        "Ⅰ. 상해입원실손의료비(갱신형)보장",
        "Ⅰ. 상해입원실손의료비(갱신형)보장",
    ], f"러닝헤더가 로마숫자 라벨을 덮어썼다: {sections}"


def test_genuine_bold_section_line_not_followed_by_page_number_still_recognized():
    #: 회귀 방지 — 쪽번호 없이 진짜로 단독 줄인 `보통약관`은 여전히 부 경계다
    #: (기존 v5 기능, §9-1 이전부터 있던 것).
    page = "보통약관\n제1조(적용범위)\n본문.\n"
    built = build(_doc(page))
    sections = [c["section"] for c in built["clauses"]]
    assert sections == ["보통약관"]


def test_roman_numeral_without_trailing_period_is_recognized():
    #: 실측(현대해상 `6dc178dab47f` p52) 재현 — 마침표 없는 로마자 단독 줄
    #: (`Ⅱ\n노후실손의료비질병(갱신형) 보장\n제1조 …`, 실측 2,104줄·323문서).
    page = "Ⅱ\n노후실손의료비질병(갱신형) 보장\n제1조 (보험금의 지급사유)\n본문.\n"
    built = build(_doc(page))
    sections = [c["section"] for c in built["clauses"]]
    assert sections == ["Ⅱ. 노후실손의료비질병(갱신형) 보장"]


# ────────────────────────────────────────────────────────────────
# 15) ①로 시작하는 진짜 제1항이 다른 조를 인용하는 것까지 자기참조로
#     오판해서 걸러지는 결함 — 3번째 시도(Codex 설계, 2026-08-26)로 해결.
#     1·2차는 `_REF_TAIL` 자체를 넓히거나 문서 전역 상태를 heads 에만
#     둬서 전량 재측정에서 역효과가 나 되돌렸다(경위는 `to_clauses.py`
#     `_REF_TAIL`·`_LEADING_ARTICLE_REF` 위 주석 참조). 이번엔 `_REF_TAIL`
#     자체는 안 건드리고, heads 루프 안에서만 "제목 있음 + `_REF_TAIL` 이
#     매치한 게 `제M조` 형태의 조 인용 + M≠자기 번호 + policy 구역 +
#     같은 부에서 처음 보는 제목" 다섯 조건을 전부 만족할 때만 참조→머리로
#     뒤집는다. `_REF_TAIL`/S3 판정식은 그대로라 두 판정이 어긋날 수 없다.
# ────────────────────────────────────────────────────────────────
def test_self_reference_immediately_after_title_without_paragraph_mark_still_dropped():
    #: 회귀 방지 — 막아야 하는 원래 버그(§9-1 3번)는 그대로 잡혀야 한다.
    #: ①이 안 끼고 제목 뒤에 곧바로 "제N항"류 참조가 이어지는 경우.
    #: (§15 자체 — ①이 낀 진짜 제1항이 다른 조를 인용하는 경우 — 는 아직
    #: 안전한 수정안이 없어 테스트도 없다. 위 절 머리말 참조.)
    page = (
        "제1조(적용범위)\n본문.\n"
        "제2조(용어의 정의)\n본문.\n"
        "제3조(전환후계약의 보장개시일)\n본문.\n"
        "제4조(계약 전 알릴 의무)\n본문.\n"
        "제3조(전환후계약의 보장개시일) 제2항에도 불구하고 다음 각 호에\n"
        "해당하면 그러하지 아니합니다.\n"
        "제5조(계약전환의 무효)\n본문.\n"
    )
    built = build(_doc(page))
    nos = [c["clause_no"] for c in built["clauses"]]
    assert nos.count("제3조") == 1, f"①이 없는데도 자기참조가 새 머리로 잡혔다: {nos}"
    assert built["structure_faults"]["S1_aba_reentry"] == 0


def test_policy_zone_leading_reference_with_paragraph_mark_is_recovered_as_head():
    #: 실측(population B 4라운드 재추정, 변형 A — b5·b27·b30·b48·b56·b61·b66)
    #: 재현 — "제4조의2(제목)\n① 제3조(제목)의 규정에도 불구하고 …" 는 진짜
    #: 새 조인데, 자기 조가 아니라 **다른** 조(제3조)를 인용하며 시작한다.
    #: `_REF_TAIL` 은 이걸 참조로 걸렀지만 5개 조건(제목 있음·`제M조` 형태
    #: 인용·M≠자기번호·policy 구역·중복 제목 아님)을 전부 만족하면 머리로
    #: 뒤집어야 한다.
    page = (
        "보통약관\n"
        "제3조(보상내용)\n① 회사는 보상합니다.\n"
        "제4조(보상하지 않는 사항)\n① 회사는 다음의 경우 보상하지 않습니다.\n"
        "제4조의2(기본형실손의료비보장보험에서 보상하지 않는 사항)\n"
        "① 제3조(보상내용)의 규정에도 불구하고 다음 각 호에 해당하는 사유로\n"
        "인한 손해는 보상하지 아니합니다.\n"
        "제5조(보험금 지급절차)\n① 회사는 …\n"
    )
    built = build(_doc(page))
    nos = [c["clause_no"] for c in built["clauses"]]
    assert "제4조의2" in nos, f"타조를 인용하며 시작하는 진짜 새 조가 회복되지 않았다: {nos}"
    assert nos.count("제3조") == 1, f"인용문 안의 '제3조'가 중복 머리로 새로 생겼다: {nos}"


def test_policy_zone_leading_reference_without_paragraph_mark_is_recovered_as_head():
    #: 실측(population B 4라운드 재추정, 변형 B — samsungfire b69·b76 원문
    #: 그대로) 재현 — "제48조(갱신계약의보장개시)\n제45조(계약의갱신및보험기간)
    #: 에따라계약이갱신되는경우, …" 처럼 ① 없이 제목 뒤에 곧바로 다른 조
    #: 인용이 온다. 변형 A 와 달리 항 기호가 없어도 같은 5개 조건으로 회복돼야
    #: 한다.
    page = (
        "보통약관\n"
        "제44조(계약의 취소)\n① 회사는 …\n"
        "제45조(계약의갱신및보험기간)\n① 회사는 …\n"
        "제48조(갱신계약의보장개시)\n"
        "제45조(계약의갱신및보험기간)에따라계약이갱신되는경우, 갱신계약의보장개시는\n"
        "갱신일당일부터개시됩니다.\n"
        "제49조(보험료의 납입)\n① 계약자는 …\n"
    )
    built = build(_doc(page))
    nos = [c["clause_no"] for c in built["clauses"]]
    assert "제48조" in nos, f"항 기호 없이 타조를 인용하며 시작하는 진짜 새 조가 회복되지 않았다: {nos}"
    assert nos.count("제45조") == 1, f"인용문 안의 '제45조'가 중복 머리로 새로 생겼다: {nos}"


def test_statute_zone_leading_reference_is_not_recovered():
    #: 회귀 방지(Codex population B 4라운드 오판 13건과 같은 함정) — "관계법령"
    #: 부록 구역 안에서는 제목이 있고 다른 조 번호를 인용하는 형태라도
    #: 뒤집으면 안 된다. 외부 법령을 이 정책 자신의 조항으로 착각하는 것이
    #: Codex 오판 13/24건의 정체였다.
    page = (
        "관계법령\n"
        "제3조(정의)\n① 이 법에서 사용하는 용어의 뜻은 다음과 같다.\n"
        "제5조(적용범위)\n"
        "제3조(정의)의 규정에도 불구하고 다음 각 호에 해당하는 경우에는 …\n"
        "제7조(벌칙)\n① 다음 각 호에 해당하는 자는 처벌한다.\n"
    )
    built = build(_doc(page))
    nos = [c["clause_no"] for c in built["clauses"]]
    assert "제5조" not in nos, f"관계법령 구역의 타조 인용이 머리로 잘못 회복됐다: {nos}"


def test_duplicate_title_leading_reference_in_policy_zone_is_still_dropped():
    #: 회귀 방지(1차 시도 실패 재현 방지, aba 716→789 악화의 원인) — 같은 부
    #: 안에서 동일 (조번호·가지번호·제목)이 이미 머리로 회복된 적이 있으면,
    #: 그 다음 등장은 진짜 새 조가 아니라 반복 인용/재수록이다 — 다시
    #: 머리로 뒤집지 않는다.
    page = (
        "보통약관\n"
        "제3조(보상내용)\n① 회사는 보상합니다.\n"
        "제4조(보상하지 않는 사항)\n① 회사는 다음의 경우 보상하지 않습니다.\n"
        "제4조의2(기본형실손의료비보장보험에서 보상하지 않는 사항)\n"
        "① 제3조(보상내용)의 규정에도 불구하고 다음 각 호에 해당하는 사유로\n"
        "인한 손해는 보상하지 아니합니다.\n"
        "1. 첫째 사유\n"
        "제4조의2(기본형실손의료비보장보험에서 보상하지 않는 사항)\n"
        "① 제3조(보상내용)의 규정에도 불구하고 또 다른 문장이 이어집니다.\n"
        "제5조(보험금 지급절차)\n① 회사는 …\n"
    )
    built = build(_doc(page))
    nos = [c["clause_no"] for c in built["clauses"]]
    assert nos.count("제4조의2") == 1, (
        f"같은 부 안의 중복 제목 자기참조가 두 번째로 또 머리로 회복됐다: {nos}"
    )
    assert built["structure_faults"]["S1_aba_reentry"] == 0


def test_reverse_order_statute_marker_is_recognized_as_statute_zone():
    #: 실측(Codex 구현검토 재현, 2026-08-26) — 글리프 순서가 뒤집힌 법령
    #: 판본("법 이름\n○\n", `_STATUTE_LAW_REV`+`_STATUTE_BULLET`, `_statute_events`
    #: 위 주석 참조·역순 9문서는 정순을 하나도 안 씀)을 §15 의 section_kind
    #: 스캔이 처음엔 놓쳤다 — 그 구간이 계속 `policy`로 남아 외부 법령
    #: 인용("개인정보보호법 제3조(정의)")을 이 정책 자신의 조항으로 착각하고
    #: 회복해버렸다(population B 4라운드 Codex 오판 13건과 같은 함정).
    page = (
        "보통약관\n"
        "제1조(적용범위)\n① 회사는 …\n"
        "제2조(용어의 정의)\n① 회사는 …\n"
        "개인정보보호법\n"
        "○\n"
        "제3조(정의)\n① 이 법에서 사용하는 용어의 뜻은 다음과 같다.\n"
        "제5조(적용범위)\n"
        "제3조(정의)의 규정에도 불구하고 다음 각 호에 해당하는 경우에는 …\n"
        "제7조(벌칙)\n① 다음 각 호에 해당하는 자는 처벌한다.\n"
    )
    built = build(_doc(page))
    nos = [c["clause_no"] for c in built["clauses"]]
    assert "제5조" not in nos, f"역순 법령 표지 구역의 타조 인용이 머리로 잘못 회복됐다: {nos}"


def test_same_named_section_reentry_does_not_merge_signatures():
    #: 실측(Codex 구현검토 재현, 2026-08-26) — 문서 안에 이름이 같은("보통약관")
    #: 서로 다른 두 부(部)가 있으면, 부 이름 문자열만으로 "같은 부"를 판정하는
    #: 서명이 둘을 하나로 합쳐서 **두 번째 부의 정당한 조항**을 "중복 제목
    #: 자기참조"로 오판해 떨어뜨렸다(부 발생 색인을 서명에 넣어 해결).
    page = (
        "보통약관\n"
        "제3조(보상내용)\n① 회사는 보상합니다.\n"
        "제4조의2(기본형실손)\n① 제3조(보상내용)의 규정에도 불구하고 본문입니다.\n"
        "제5조(끝)\n① 회사는 …\n"
        "보통약관\n"
        "제3조(보상내용)\n① 회사는 보상합니다.\n"
        "제4조의2(기본형실손)\n① 제3조(보상내용)의 규정에도 불구하고 본문입니다.\n"
        "제5조(끝)\n① 회사는 …\n"
    )
    built = build(_doc(page))
    nos = [c["clause_no"] for c in built["clauses"]]
    assert nos.count("제4조의2") == 2, (
        f"이름이 같은 서로 다른 부의 정당한 조항이 중복으로 오판돼 하나가 빠졌다: {nos}"
    )
    #: ★`S1_aba_reentry` 는 여기서 검사하지 않는다 — 그건 `structure_faults()`
    #:   가 클라우스의 `section` **표시 이름**만으로 재진입을 판정하는 별개
    #:   메커니즘이라(이 테스트가 고치는 `seen_titled_heads` 서명과 무관),
    #:   이름이 같은 두 부는 번호가 겹치면 원래도 걸린다(기존 회귀 테스트
    #:   `test_structure_faults_still_flags_reentry_within_the_same_section`
    #:   가 이미 그 설계 의도를 고정해 뒀다) — §15 수정 범위 밖이다.


def test_title_whitespace_difference_still_counts_as_duplicate_signature():
    #: 실측(Codex 구현검토 재현, 2026-08-26) — 정규화 없이 `.strip()`만 하면
    #: "기본형 실손"과 "기본형실손"(공백 차이)을 다른 제목으로 봐서 중복 제목
    #: 가드가 안 걸리고 **같은 조가 두 번 회복**됐다. 부 이름 정규화와 같은
    #: 방식(`re.sub(r"\s+", "", ...)`)으로 맞춰야 한다.
    page = (
        "보통약관\n"
        "제3조(보상내용)\n① 회사는 보상합니다.\n"
        "제4조의2(기본형 실손)\n① 제3조(보상내용)의 규정에도 불구하고 본문입니다.\n"
        "제4조의2(기본형실손)\n① 제3조(보상내용)의 규정에도 불구하고 반복됩니다.\n"
        "제5조(끝)\n① 회사는 …\n"
    )
    built = build(_doc(page))
    nos = [c["clause_no"] for c in built["clauses"]]
    assert nos.count("제4조의2") == 1, (
        f"공백만 다른 같은 제목이 서로 다른 제목으로 오판돼 두 번 회복됐다: {nos}"
    )


def test_leading_reference_to_different_branch_of_same_base_number_is_recovered():
    #: 실측(Codex 구현검토 재현, 2026-08-26) — 인용 대상이 자기 자신과 본번호는
    #: 같고 가지번호만 다른 조(`제4조의2`가 `제4조의1`을 인용)일 때, 본번호만
    #: 비교하면 진짜 다른 조인데 자기참조로 오판해 회복을 놓친다.
    page = (
        "보통약관\n"
        "제4조의1(앞)\n① 회사는 …\n"
        "제4조의2(뒤)\n① 제4조의1(앞)의 규정에도 불구하고 본문입니다.\n"
        "제5조(끝)\n① 회사는 …\n"
    )
    built = build(_doc(page))
    nos = [c["clause_no"] for c in built["clauses"]]
    assert "제4조의2" in nos, f"가지번호만 다른 타조 인용이 자기참조로 오판돼 회복되지 않았다: {nos}"


# ────────────────────────────────────────────────────────────────
# s5L 연동계획 단계2(2026-08-27) — 부 제목 크기/굵기 확인 신호
# (판정은 안 바꾼다 — stats 집계만. §2 YAGNI)
# ────────────────────────────────────────────────────────────────
def _doc_with_layout_insurer(insurer: str, *pages: tuple[str, list[dict]]) -> dict:
    return {
        "pages": [{"page": i + 1, "text": t, "layout": lay} for i, (t, lay) in enumerate(pages)],
        "source": {"insurer": insurer},
        "stats": {"pages": len(pages)},
    }


def test_title_visual_signal_hits_when_insurer_has_signal_and_title_is_bigger():
    #: 신호가 있는 보험사(DB손해보험, 부 제목 모집단 실측 67.8%)에서 부 제목
    #: 줄이 실제로 다음 줄보다 크면 `section_title_visual_contrast_hit` 가
    #: 올라간다.
    page = "질병입원특약\n제1조(목적)\n본문.\n"
    layout = [
        _layout_line("질병입원특약", 20, size=14.0),
        _layout_line("제1조(목적)", 40, size=10.0),
        _layout_line("본문.", 60, size=10.0),
    ]
    built = build(_doc_with_layout_insurer("DB손해보험", (page, layout)))
    assert built["stats"]["section_title_visual_checked"] == 1
    assert built["stats"]["section_title_visual_contrast_hit"] == 1
    assert built["stats"]["section_title_visual_unavailable"] == 0


def test_title_visual_signal_no_hit_when_title_is_same_size():
    #: 같은 보험사라도 이번 제목 줄이 다음 줄과 크기가 같으면(대비 없음)
    #: `checked` 는 늘지만 `contrast_hit` 는 안 늘어야 한다 — 판정 자체는
    #: 그래도 그대로 채택된다(§2 YAGNI, 대비 없음이 채택을 취소하지 않음).
    page = "질병입원특약\n제1조(목적)\n본문.\n"
    layout = [
        _layout_line("질병입원특약", 20, size=10.0),
        _layout_line("제1조(목적)", 40, size=10.0),
        _layout_line("본문.", 60, size=10.0),
    ]
    built = build(_doc_with_layout_insurer("DB손해보험", (page, layout)))
    sections = [c["section"] for c in built["clauses"]]
    assert sections == ["질병입원특약"], "대비 없음으로 채택 자체가 취소됐다 — §2 YAGNI 위반"
    assert built["stats"]["section_title_visual_checked"] == 1
    assert built["stats"]["section_title_visual_contrast_hit"] == 0


def test_title_visual_signal_skips_insurers_without_measured_signal():
    #: 실측에서 신호가 약했던 보험사(삼성생명, 부 제목 모집단 8.0%)는
    #: 레이아웃이 있어도 확인을 아예 안 한다 — "보험사별 조건부"가 핵심
    #: 설계다.
    page = "질병입원특약\n제1조(목적)\n본문.\n"
    layout = [
        _layout_line("질병입원특약", 20, size=14.0),
        _layout_line("제1조(목적)", 40, size=10.0),
        _layout_line("본문.", 60, size=10.0),
    ]
    built = build(_doc_with_layout_insurer("삼성생명", (page, layout)))
    assert built["stats"]["section_title_visual_checked"] == 0
    assert built["stats"]["section_title_visual_contrast_hit"] == 0
    assert built["stats"]["section_title_visual_unavailable"] == 0


def test_title_visual_signal_zero_when_layout_absent_even_for_signal_insurer():
    #: 신호 있는 보험사라도 이 문서에 레이아웃 자체가 없으면(s5 입력)
    #: checked/contrast_hit/unavailable 전부 0 — 하위호환 경로(§4 "하지 않는 것").
    page = "질병입원특약\n제1조(목적)\n본문.\n"
    built = build(_doc(page))
    assert built["stats"]["section_title_visual_checked"] == 0
    assert built["stats"]["section_title_visual_contrast_hit"] == 0
    assert built["stats"]["section_title_visual_unavailable"] == 0


def test_title_visual_signal_missing_size_is_unavailable_not_a_miss():
    #: 코덱스 표본검수 지적 — 결측(size 없음)을 0/False 로 채우면 없는
    #: 데이터가 "대비 없음"으로 잘못 셈해진다. `unavailable` 로 따로 센다.
    page = "질병입원특약\n제1조(목적)\n본문.\n"
    layout = [
        _layout_line("질병입원특약", 20, size=14.0),
        _layout_line("제1조(목적)", 40, size=10.0),
        _layout_line("본문.", 60, size=10.0),
    ]
    del layout[1]["size"]  # 다음 줄 size 결측
    built = build(_doc_with_layout_insurer("DB손해보험", (page, layout)))
    assert built["stats"]["section_title_visual_checked"] == 0
    assert built["stats"]["section_title_visual_contrast_hit"] == 0
    assert built["stats"]["section_title_visual_unavailable"] == 1


def test_title_visual_signal_uses_or_across_both_axes():
    #: 두 축을 다 쓰는 보험사(NH농협손해보험, size+bold)는 **어느 한쪽만
    #: 대비가 있어도** hit — size 는 대비 없어도 bold 대비가 있으면 hit.
    page = "질병입원특약\n제1조(목적)\n본문.\n"
    layout = [
        _layout_line("질병입원특약", 20, size=10.0, bold=True),
        _layout_line("제1조(목적)", 40, size=10.0, bold=False),
        _layout_line("본문.", 60, size=10.0, bold=False),
    ]
    built = build(_doc_with_layout_insurer("NH농협손해보험", (page, layout)))
    assert built["stats"]["section_title_visual_checked"] == 1
    assert built["stats"]["section_title_visual_contrast_hit"] == 1


# ────────────────────────────────────────────────────────────────
# population A 사람 표본검수(2026-08-26) 중 발견 — S3·S4 정밀도 수정
# ────────────────────────────────────────────────────────────────
def test_ref_tail_matches_jeonghaneun_inflection_not_just_jeonghan():
    #: 실측(dbins "43. 분쟁의 조정" 표준조항 반복, 표본 20건 중 13건) 재현 —
    #: "제42조에서 정하는"(활용형)을 "제42조에서 정한"만 받는 옛 규칙이 놓쳐서
    #: S3(파묻힌 머리)가 정상 참조 문장을 파묻힌 머리로 오판했다.
    blocks = [{
        "no": 43, "kind": "numbered", "section": "보통약관",
        "text": (
            "43. (분쟁의 조정)\n"
            "계약에 관하여 분쟁이 있는 경우 …\n"
            "회사는 계약자가 조정을 통하여 주장하는 권리나 이익의 가액이\n"
            "「금융소비자보호에 관한 법률」\n"
            "제42조에서 정하는 일정 금액 이내인 분쟁사건에 대하여\n"
            "조정절차가 개시된 경우에는 소를 제기하지 않습니다.\n"
        ),
    }]
    faults = structure_faults(blocks)
    assert faults["S3_embedded_header"] == 0, (
        "'정하는' 활용형 참조가 여전히 파묻힌 머리로 오탐됐다"
    )


def test_annex_gate_reuses_annex_head_ref_tail_not_bare_marker():
    #: 실측(samsungfire "제7조(보험금의 지급절차)", S4 게이트 모집단 4건 전부)
    #: 재현 — "<붙임2>에서 정한 이율로…"가 줄바꿈으로 줄머리에 오는 정상
    #: 문장인데, struct_audit.py 의 옛 `_ANNEX`(닫는 괄호·참조꼬리 미확인)가
    #: 진짜 부록 시작으로 오판했다. `to_clauses._ANNEX_HEAD`/`_ANNEX_REF_TAIL`
    #: (닫는 괄호까지 확인 + 참조꼬리 판정)을 재사용하도록 고쳤다.
    long_tail = "이 문장이 이어집니다. " * 30  # 300자 넘겨 ANNEX_MIN_TAIL 통과 조건 만족
    blocks = [{
        "no": 7, "kind": "article", "section": "보통약관",
        "text": (
            "제7조 (보험금의 지급절차)\n"
            "④ 회사는 그 다음날부터 지급일까지의 기간에 대하여\n"
            "<붙임2>에서 정한 이율로 계산한 금액을 보험금에 더하여 지급합니다.\n"
            + long_tail
        ),
    }]
    faults = structure_faults(blocks)
    assert faults["S4_annex_absorption"] == 0, (
        "'<붙임2>에서 정한'(정상 문장 중간 인용)이 부록 흡수로 오탐됐다"
    )


def test_annex_gate_still_catches_a_real_swallowed_annex():
    #: 회귀 방지 — 진짜 부록(닫는 괄호 있는 마커 뒤에 참조 조사가 없고
    #: 긴 본문이 이어짐)은 여전히 걸려야 한다.
    long_tail = "표 내용이 이어집니다. " * 30
    blocks = [{
        "no": 9, "kind": "article", "section": "보통약관",
        "text": (
            "제9조 (특정질병 분류표)\n"
            "<붙임3>\n"
            + long_tail
        ),
    }]
    faults = structure_faults(blocks)
    assert faults["S4_annex_absorption"] == 1


# ────────────────────────────────────────────────────────────────
# s5L 연동계획 단계 1 — 위치+반복 러닝헤더 신호(레이아웃 있을 때만)
# ────────────────────────────────────────────────────────────────
def test_layout_repeated_header_does_not_reset_section_without_page_number():
    #: 재현 — "특별약관"(bare _SECTION_LINE 어휘)이 매 쪽 맨 위 같은 위치에
    #: 장식으로 반복되는데, 그 다음 줄이 쪽번호가 아니라 진짜 조 머리라서
    #: 기존 텍스트 휴리스틱("다음 줄=쪽번호")은 못 잡는다. 위치+반복 신호가
    #: 이걸 잡아서 진짜 부 제목("질병입원형 특별약관")이 매 쪽 도로 안 뭉개지게
    #: 해야 한다.
    #: ★러닝헤더는 **매 쪽 같은 물리적 위치**(맨 위)에 온다 — 진짜 부 제목이
    #: 시작하는 쪽에서도 예외 없다. 그래서 장식용 "특별약관"을 항상 y0=20(맨
    #: 위)에 두고, 진짜 제목("질병입원형 특별약관")은 그 아래 y0=60에 둔다.
    p1 = "특별약관\n질병입원형 특별약관\n제1조(목적)\n본문.\n"
    p2 = "특별약관\n제2조(정의)\n본문.\n"
    p3 = "특별약관\n제3조(보상내용)\n본문.\n"
    p4 = "특별약관\n제4조(면책)\n본문.\n"
    p5 = "제5조(기타)\n본문.\n"
    layouts = [
        [_layout_line("특별약관", 20), _layout_line("질병입원형 특별약관", 60),
         _layout_line("제1조(목적)", 90)],
        [_layout_line("특별약관", 20), _layout_line("제2조(정의)", 40)],
        [_layout_line("특별약관", 20), _layout_line("제3조(보상내용)", 40)],
        [_layout_line("특별약관", 20), _layout_line("제4조(면책)", 40)],
        [_layout_line("제5조(기타)", 20)],
    ]
    built = build(_doc_with_layout(*zip([p1, p2, p3, p4, p5], layouts)))
    sections = {c["clause_no"]: c["section"] for c in built["clauses"]}
    assert set(sections.values()) == {"질병입원형 특별약관"}, (
        f"장식용 반복 '특별약관'이 진짜 부 제목을 도로 덮어썼다: {sections}"
    )


def test_layout_absent_keeps_old_behavior_unchanged():
    #: 하위호환 — 레이아웃 없는 입력(s5)은 새 신호가 빈 집합이라 기존 동작
    #: 그대로다(기존 22개 테스트가 이미 s5 픽스처로 통과하는 것과 같은 뜻이지만,
    #: "레이아웃 필드 자체가 없을 때"를 명시적으로 한 번 더 확인한다).
    page = "보통약관\n제1조(적용범위)\n본문.\n"
    built = build(_doc(page))
    sections = [c["section"] for c in built["clauses"]]
    assert sections == ["보통약관"]


# ────────────────────────────────────────────────────────────────
# S3 잔여26건 Codex 전수분석(2026-08-26) — 새 오탐 2종, S3 전용 예외 추가
# (_ARTICLE/_REF_TAIL/heads 는 안 건드림 — S3 게이트 정밀도만 고침)
# ────────────────────────────────────────────────────────────────
def test_s3_ignores_law_crime_list_with_qualifier_clause():
    #: 실측(dbins, 노인학대범죄피해위로금 특별약관) 원문 재현 — "가."~"타." 로
    #: 나열되는 형법 조문 열거 중 "제281조(체포ㆍ감금등의 치사상) (상해에
    #: 이르게 한 때에만 해당한다)의 죄"처럼 제목 뒤 두 번째 괄호(한정문)가
    #: 오는 형태는 `_REF_TAIL` 이 못 거른다(여는 괄호로 바로 시작해서 어느
    #: 분기에도 안 걸림). S3 전용 예외로 걸러야 한다.
    blocks = [{
        "no": 1, "kind": "numbered", "section": "노인학대범죄피해위로금(친족제외) 특별약관",
        "text": (
            "1. (보험금의 지급사유)\n"
            "①회사는 …「노인학대관련범죄」로 노인학대 피해자가 되어 …\n"
            "보험가입금액을 지급합니다.\n"
            "③위 ①에서 「노인학대관련범죄」란 노인복지법 제1조의2 제5호에 따른 노인학대범\n"
            "죄를 말하며, 「노인학대」란 노인복지법 제1조의2 제4호에 따른 노인학대를 말합\n"
            "니다.\n"
            "5. “노인학대관련범죄”란 보호자에 의한 65세 이상 노인에 대한 노인학대로\n"
            "   서 다음 각 목의 어느하나에 해당하는 죄를 말한다.\n"
            "  가. 「형법」 제2편제25장 상해와 폭행의 죄 중 제257조(상해, 존속상해), \n"
            "     제258조(중상해, 존속중상해), 제260조(폭행, 존속폭행)제1항ㆍ제2항, \n"
            "     제261조(특수폭행) 및 제264조(상습범)의 죄 \n"
            "  다. 「형법」 제2편제29장 체포와 감금의 죄 중 제276조(체포, 감금, 존속 \n"
            "      체포,존속감금), 제277조(중체포, 중감금, 존속중체포, 존속중감금),제 \n"
            "      278조(특수체포, 특수감금), 제279조(상습범), 제280조(미수범) 및 \n"
            "      제281조(체포ㆍ감금등의 치사상) (상해에 이르게 한 때에만 해당한다)의 죄\n"
        ),
    }]
    faults = structure_faults(blocks)
    assert faults["S3_embedded_header"] == 0, (
        "형법 조문 열거(가.~타. 목록)가 파묻힌 머리로 오탐됐다"
    )


def test_s3_ignores_summary_sheet_terminal_reference():
    #: 실측(meritzfire 약관요약서) 원문 재현 — "9\n해약환급금\n제31조(제목),"
    #: 처럼 표 행 다음에 조 인용이 쉼표로 끝나고 그 뒤 본문이 없는 참조는
    #: `_REF_TAIL` 이 못 거른다(쉼표 뒤가 바로 끝이라 참조 분기가 없음).
    blocks = [{
        "no": 1, "kind": "article", "section": "약관요약서",
        "text": (
            "제28조(보험료의 납입을 연체하여 해지된 계약의 부활(효력회복))\n"
            " \n"
            "9\n"
            "해약환급금\n"
            "제31조(계약자의 임의해지 및 피보험자의 서면동의 철회권),\n"
        ),
    }]
    faults = structure_faults(blocks)
    assert faults["S3_embedded_header"] == 0, (
        "약관요약서의 쉼표종결 참조가 파묻힌 머리로 오탐됐다"
    )


def test_s3_still_catches_real_embedded_head_in_summary_sheet_section():
    #: 회귀 방지 — 실측(nhlife 약관요약서) 재현. **같은 "약관요약서" section**
    #: 안이어도, 뒤에 진짜 본문(①…)이 이어지는 진짜 파묻힌 머리는 여전히
    #: 잡혀야 한다 — section 이름만으로 뭉뚱그려 예외 처리하면 안 된다.
    blocks = [{
        "no": 1, "kind": "article", "section": "약관요약서",
        "text": (
            "제17조【보험계약의 성립】\n"
            "① 계약은 계약자의 청약과 회사의 승낙으로 이루어집니다.\n"
            "② 회사는 피보험자가 계약에 적합하지 않은 경우에는 승낙을 거절하거나 별도의 조건\n"
            "제18조【청약의 철회】\n"
        ),
    }]
    faults = structure_faults(blocks)
    assert faults["S3_embedded_header"] == 1, (
        "진짜 파묻힌 머리(제18조)가 새 예외 때문에 안 걸리게 됐다"
    )


def test_s3_still_catches_real_embedded_head_with_law_citation_nearby():
    #: 회귀 방지 — 실측(samsunglife) 재현. 법령 인용과 무관하게, 목록 구조
    #: 없이 바로 이어지는 진짜 파묻힌 머리("제46조…"→"제47조…")는 여전히
    #: 잡혀야 한다.
    blocks = [{
        "no": 1, "kind": "article", "section": "Ⅱ. 외 래",
        "text": (
            "제46조 [특칙의 적용] \n"
            "이 특칙은 피보험자로 될 자가 계약을 체결할 때 태아(胎兒)인 계약에 한하여 적용합니다. \n"
            " \n"
            "제47조 [피보험자] \n"
        ),
    }]
    faults = structure_faults(blocks)
    assert faults["S3_embedded_header"] == 1
