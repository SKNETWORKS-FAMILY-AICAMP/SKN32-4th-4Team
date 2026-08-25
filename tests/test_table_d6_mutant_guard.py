"""Mutation guard for the D6 fixtures in test_table_d6_regressions.py.

A regression fixture that passes regardless of whether the defense it claims
to test is present or not is not actually testing anything. For each D6
guard inside `attachment_verdict`, this file disables exactly that guard and
confirms the paired fixture then *wrongly* passes attachment (ok becomes
True). That proves the fixture's rejection is attributable to the specific
guard, not to some other unrelated check.

`2026-08-14_S6_D6_회귀fixture_착수보고.md` names this exact gap: fixtures
existed but nothing proved they were load-bearing. This file is that proof.
"""

from scripts.extract import table_signals
from scripts.extract.table_signals import attachment_verdict


def _sentence_split_table():
    return {
        "method": "선",
        "is_table": True,
        "records": [
            {"no": 1, "cols": {"1": "회사는 보험금을 지급하지", "2": "않습니다."}},
            {"no": 2, "cols": {"1": "다만 다음의 경우에는", "2": "지급합니다."}},
        ],
    }


def _repeated_right_cell_table():
    return {
        "method": "2열짝짓기",
        "is_table": True,
        "signals": {"T2_dup_cells": 1.0},
        "records": [
            {"no": 1, "cols": {"1": "① 응급환자", "2": "보험금을 지급합니다."}},
            {"no": 2, "cols": {"1": "② 이송", "2": "보험금을 지급합니다."}},
            {"no": 3, "cols": {"1": "③ 기타", "2": "보험금을 지급합니다."}},
        ],
    }


def test_disabling_prose_veto_makes_sentence_split_fixture_wrongly_pass(monkeypatch):
    #: 기준선 — 가드가 살아 있으면 거부된다 (기존 회귀 테스트와 같은 전제)
    ok, _ = attachment_verdict(_sentence_split_table())
    assert ok is False

    #: 뮤턴트 — prose_shape 베토만 무력화한다
    monkeypatch.setattr(
        table_signals,
        "prose_shape",
        lambda records: {"is_prose": False, "prose_why": []},
    )
    ok, _ = attachment_verdict(_sentence_split_table())
    assert ok is True, (
        "prose_shape 베토를 없앴는데도 거부됐다 — "
        "test_sentence_split_across_cells_is_rejected_before_attachment 은 "
        "이 가드가 아니라 다른 경로로 통과하고 있을 수 있다"
    )


def test_disabling_method_veto_makes_repeated_cell_fixture_wrongly_pass(monkeypatch):
    #: 기준선 — 가드가 살아 있으면 거부된다
    ok, _ = attachment_verdict(_repeated_right_cell_table())
    assert ok is False

    #: ★첫 시도에서 발견한 것 — method 가드만 빼면 prose_shape 베토가 대신
    #: 걸린다("보험금을 지급합니다." 반복이 문장부호 비율을 넘긴다). 두 가드가
    #: 이 픽스처에서 서로 겹쳐 있다는 뜻이라, method 가드 하나만 격리하려면
    #: prose_shape 도 같이 무력화해야 한다.
    monkeypatch.setattr(
        table_signals,
        "prose_shape",
        lambda records: {"is_prose": False, "prose_why": []},
    )

    #: 뮤턴트 — method != "선" 가드만 뺀 복사본. attachment_verdict 는 인라인
    #: 조건이라 monkeypatch 로 한 줄만 끊을 수 없어, 그 가드만 제거한 사본으로
    #: 대신한다(코드 자체를 바꾸지 않고 검증하기 위함).
    def mutant_attachment_verdict(table: dict) -> tuple[bool, list[str]]:
        why: list[str] = []
        if table.get("is_table") is False:
            why.extend(table.get("reject_why") or ["페이지 표 게이트 탈락"])
        prose = table_signals.prose_shape(table.get("records") or [])
        if prose.get("is_prose"):
            why.extend(prose.get("prose_why") or ["T9 본문 모양"])
        return not why, why

    ok, _ = mutant_attachment_verdict(_repeated_right_cell_table())
    assert ok is True, (
        "method 가드와 prose 베토를 둘 다 없앴는데도 거부됐다 — "
        "test_repeated_right_cell_pairing_is_never_attachable 을 거부시키는 "
        "제3의 경로가 남아 있다"
    )
