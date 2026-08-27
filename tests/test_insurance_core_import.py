"""`core` 원장 적재 도구가 **무엇을 보고 무엇을 막나.**

★★이 파일은 2026-08-27 에 통째로 다시 썼다. 앞 판은 **결함을 기대값으로 못박아** 뒀다 —

    assert report["structured_documents"] == 236
    assert report["blockers"]["identification_not_confirmed"] == 236
    assert report["blockers"]["release_approval:candidate"] == 236

  도구가 `data/structured/dbins/s7_hybrid-table-v1` **한 보험사 236건**만 보고 있었고,
  확정 여부를 산출물 파일의 `identification` 에서 읽는 바람에 **확정 1,355건이 하나도
  안 보였다.** 시험이 그 상태를 「정상」으로 지키고 있었으니, 고치면 시험이 깨진다 —
  그러면 사람은 **시험을 보고 기능을 되돌리기 쉽다.**

  ★그래서 지금은 **지켜야 할 성질**을 적는다: 몇 건이냐가 아니라
    「전량을 보는가」·「못 넣는 것을 세어 말하는가」·「서명 없이 안 쓰는가」다.
"""

from __future__ import annotations

import pytest

from scripts.db.import_insurance_core import (
    DEFAULT_MANIFESTS,
    accepted_structured_dirs,
    load_documents,
    main,
    summarize,
)


def _all_documents():
    docs = []
    for d in accepted_structured_dirs():
        docs.extend(load_documents(d, DEFAULT_MANIFESTS))
    return docs


def test_승인_clause_tag_전량을_본다():
    """★한 보험사만 보면 「적재 준비 안 됨」의 분모가 틀린다."""
    dirs = accepted_structured_dirs()
    assert len(dirs) > 1, (
        f"산출물 디렉터리를 {len(dirs)}곳만 본다 — 승인 clause_tag 는 12개사에 걸쳐 있다. "
        "경로를 박아 두면 나머지 보험사가 통째로 안 보인다."
    )
    #: ★경로를 박지 않고 승인 릴리스에서 파생하는가.
    tag = dirs[0].name
    assert all(d.name == tag for d in dirs), "디렉터리마다 다른 태그를 보고 있다"


def test_못_넣는_문서를_세어서_말한다():
    """★조용히 빼지 않는다(CLAUDE.md §3). 그리고 **몇 건이 막혔다고 전량을 막지 않는다.**"""
    report = summarize(_all_documents())
    assert report["structured_documents"] == report["loadable_documents"] + report["skipped_documents"]
    assert report["skipped_documents"] >= 0
    if report["skipped_documents"]:
        assert report["blockers"], "건너뛴 문서가 있는데 사유가 비어 있다"
    #: ★넣을 수 있는 문서가 하나라도 있고 전역 차단이 없으면 준비된 것이다.
    #:   parse_status 가 나쁜 문서 몇 건이 **나머지 전부**를 막으면 안 된다.
    if report["loadable_documents"] and not report["fatal_blockers"]:
        assert report["ready"] is True


def test_세는_것은_실제로_넣을_것만이다():
    """★못 넣는 문서의 조항까지 세면 「몇 건 들어가나」에 실제보다 많게 답한다."""
    docs = _all_documents()
    report = summarize(docs)
    loadable_clauses = 0
    from scripts.db.import_insurance_core import loadable_documents

    for d in loadable_documents(docs):
        loadable_clauses += len(d.row.get("clauses") or [])
    assert report["clauses"] == loadable_clauses


def test_서명자_없이는_쓰지_않는다():
    """★`core.confirmed_policy_document.identified_by` 는 「누가 확정했나」다. 비울 수 없다."""
    assert main(["--apply", "--dsn", "postgresql://unused"]) == 2


def test_릴리스가_승인_안_되면_전역으로_막는다(monkeypatch):
    """★문서 하나가 나쁜 것과 **릴리스 자체가 승인 안 된 것**은 다르다.

    앞엣것은 그 문서만 빼면 되지만, 뒤엣것은 **아무것도 넣으면 안 된다.**
    """
    import scripts.db.import_insurance_core as m

    monkeypatch.setattr(m, "_release_accepted", lambda: False)
    report = summarize(_all_documents())
    assert report["ready"] is False
    assert report["fatal_blockers"], "릴리스 미승인이 전역 차단으로 잡히지 않았다"


def test_사람_승인_전_문서는_안_넣는다(monkeypatch):
    """★기계 대조까지만 끝난 문서를 원장에 넣으면 **기계 대조가 원장이 된다.**"""
    import scripts.db.import_insurance_core as m
    from app.core.domain import identification_mode as im

    monkeypatch.setattr(im, "is_pending_signoff", lambda entry: True)
    report = summarize(_all_documents())
    assert report["blockers"].get("human_signoff_pending"), (
        "사람 승인 전인데 차단되지 않았다"
    )
    assert report["loadable_documents"] == 0
