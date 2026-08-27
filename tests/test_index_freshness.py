"""색인이 **현행 산출물과 같은 판인가.**

★★2026-08-26 실측으로 필요해졌다.

    다른 세션이 `s6` 산출물을 전량 재생성했는데 색인은 그 전 판이었다.
    **아무도 몰랐다** — 검색도 판정도 정상으로 보였고, 다만 **낡은 조항을 근거로 냈다.**
    DB 문서 1,313 중 1,172(89.3%)가 산출물에 없는 해시를 들고 있었다.

★기존 `ensure_index_matches_release()` 는 이걸 **못 잡는다.** 그건
  「세대·모델 필터가 맞는가」를 보지 「내용이 같은 판인가」를 안 본다.
  필터는 맞는데 내용이 낡은 상태가 정확히 이번 경우다.
"""

from __future__ import annotations

import pytest


def test_판정을_막지_않는다():
    """★낡았다고 **판정을 멈추면 그 순간 서비스가 죽는다.**

    이 도구는 세고 말할 뿐이다. 다시 적재할지는 사람이 정한다.
    그래서 종료 코드로만 알리고 예외를 던지지 않는다.
    """
    import inspect

    from scripts.db import check_index_freshness as m

    src = inspect.getsource(m.main)
    assert "return 2" in src, "낡음을 종료 코드로 알려야 한다"
    #: 판정 경로를 멈추는 예외를 던지지 않는다.
    assert "raise" not in src.replace("raise SystemExit", ""), (
        "이 도구가 예외를 던지면 부르는 쪽 판정이 멈춘다"
    )


def test_산출물이_없으면_낡았다고_하지_않는다():
    """★「산출물이 없다」와 「색인이 낡았다」는 **다른 말**이다.

    없는 것을 근거로 「낡았다」고 하면, 아직 안 만든 문서 때문에
    멀쩡한 색인을 다시 적재하게 된다.
    """
    import inspect

    from scripts.db import check_index_freshness as m

    src = inspect.getsource(m.main)
    assert "no_artifact" in src and "판단 보류" in src, (
        "산출물 없는 문서를 따로 세어 보류라고 말해야 한다"
    )


def test_표본이면_표본이라고_말한다():
    """★편향된 표본으로 비율을 말하지 않는다(CLAUDE.md §4).

    이 저장소는 그 실수를 여러 번 했다 —
    큰 파일 5개만 보고 「3%」라고 했다가 실제는 11.7% 였고,
    s6 만 보고 고아 원인 비율을 전체로 말했다가 전수에서 뒤집혔다.
    """
    import inspect

    from scripts.db import check_index_freshness as m

    src = inspect.getsource(m.main)
    assert "표본이다" in src and "전체로 말하지 않는다" in src
    #: 표본을 **결정적으로** 뽑아야 다시 돌려도 비교가 된다.
    assert "random.seed(0)" in src, "표본이 매번 달라지면 비교를 못 한다"


@pytest.mark.pg
def test_현행_색인이_산출물과_같은_판인가():
    """★★**이 시험은 지금 실패하는 것이 맞다**(2026-08-26).

    다른 세션의 s6 전량 재생성 뒤 색인을 아직 다시 적재하지 않았다.
    재적재가 끝나면 통과한다 — 그때까지 이 시험이 그 사실을 계속 말한다.

    ★`skip` 하지 않는다. 건너뛰면 「없는 문제」가 되고, 그러면 낡은 근거가
      나가는 상태가 조용히 굳는다.
    """
    from scripts.db import check_index_freshness as m

    #: CLI 를 직접 부르지 않고 같은 계산을 한다 — 인자 파싱과 출력을 피한다.
    from db.postgres import pgvector_clause_index as ix
    from db.postgres.pgvector_index import get_conn

    gen = ix.current_generation()
    art, _ = m._artifact_hashes(gen)
    if not art:
        pytest.skip(f"세대 {gen} 산출물이 없다 — 대조할 것이 없다")
    try:
        conn = get_conn()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PG 없음: {str(exc)[:60]}")
    try:
        with conn.cursor() as cur:
            #: ★★**산출물이 낳지 않은 행은 심판하지 않는다** — 도구와 같은 규칙이다
            #:   (`scripts/db/check_index_freshness.py` 의 `--source-kinds` 주석 참조).
            #:   `approved_ocr_table_fact` 850행은 `load_s7_1_approved_facts.py` 가
            #:   **다른 출처에서** 넣은 것이라 구조화 산출물에 없는 게 당연하다.
            #:   이 걸러내기가 없으면 179문서가 영원히 「낡음」으로 잡힌다 —
            #:   실제로 그 179가 OCR fact 문서 179와 **정확히 같은 집합**이었다.
            cur.execute("SELECT sha256, array_agg(DISTINCT content_hash) "
                        "FROM policy_clause_occurrence "
                        "WHERE index_generation = %s AND source_kind = ANY(%s) GROUP BY 1",
                        (gen, ["clause", "annex"]))
            rows = cur.fetchall()
    finally:
        conn.close()

    stale = [s[:12] for s, hs in rows
             if art.get(s[:12]) is not None and set(hs) - art[s[:12]]]
    checked = sum(1 for s, _ in rows if art.get(s[:12]) is not None)
    assert not stale, (
        f"색인이 현행 산출물과 다른 판이다: {len(stale):,}/{checked:,}문서. "
        "판정이 낡은 조항을 근거로 낸다. "
        "`python -m scripts.index.build_clause_index` 로 다시 적재해야 한다."
    )
