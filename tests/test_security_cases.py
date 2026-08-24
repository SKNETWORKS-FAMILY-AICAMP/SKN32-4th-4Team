"""TEST-SEC-VERIFY-001 — 보안 10케이스 매핑 정적 검증(Phase 10).

`security_cases.yaml`에 적힌 pytest node id가 실제로 pytest 수집기로 collect되고
skip/xfail이 아님을 `test_requirements_matrix.py`와 **같은(더 강한) 검증 로직**을
재사용해 확인한다(로직 중복 금지 — 한쪽만 강화되고 다른 쪽이 뒤처지는 것을 방지).
"""

from __future__ import annotations

import pathlib

import pytest
import yaml

from tests.test_requirements_matrix import _is_collected, _parse_node, _skip_or_xfail_marks

_CASES_PATH = pathlib.Path(__file__).resolve().parent / "security_cases.yaml"
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_cases() -> list[dict]:
    data = yaml.safe_load(_CASES_PATH.read_text(encoding="utf-8"))
    return data["cases"]


def _iter_sec_entries():
    for case in _load_cases():
        if case.get("retired"):
            #: ★기능이 사라진 케이스. 아래 `test_retired_...` 가 따로 검증한다.
            continue
        for t in case["tests"]:
            if t.get("retired"):
                #: ★케이스는 살아 있는데 **그 안의 한 노드만** 사라진 경우가 있다
                #:   (TEST-SEC-001: 한 노드는 커머스 에이전트, 다른 하나는 RAG).
                #:   케이스째 폐기하면 **살아 있는 보안 검사까지 꺼진다.**
                continue
            yield case["id"], t["node"], t.get("basis")


@pytest.mark.parametrize(
    "case_id,node,basis", list(_iter_sec_entries()), ids=lambda v: str(v)[:40]
)
def test_security_case_node_is_actually_collectible_and_not_skipped(case_id, node, basis):
    rel_path, func_name = _parse_node(node)  # 형식 오류는 여기서 바로 실패
    file_path = _REPO_ROOT / rel_path
    assert file_path.exists(), f"[{case_id}] 파일이 없습니다: {rel_path}"

    assert _is_collected(node), f"[{case_id}] pytest가 실제로 collect하지 못했습니다: {node}"

    mark_names = _skip_or_xfail_marks(rel_path, func_name)
    assert "skip" not in mark_names, f"[{case_id}] {node}가 skip 처리돼 있습니다."
    assert "xfail" not in mark_names, f"[{case_id}] {node}가 xfail입니다."

    assert isinstance(basis, str) and basis.strip(), f"[{case_id}] {node}에 basis가 비어 있습니다."


def test_all_ten_security_cases_present_and_done():
    cases = _load_cases()
    ids = [c["id"] for c in cases]
    assert ids == [f"TEST-SEC-{i:03d}" for i in range(1, 11)], f"보안 10케이스 목록 불일치: {ids}"
    for c in cases:
        assert c["status"] == "DONE", f"{c['id']}가 DONE이 아닙니다."
        assert c["tests"], f"{c['id']}에 매핑된 테스트가 없습니다."
        assert c["threat_model"].strip(), f"{c['id']}에 위협모델 설명이 없습니다."

    #: ★살아 있는 케이스 수를 **센다.** 폐기가 늘면 보안 커버리지가 준 것이다.
    live = [c for c in cases if not c.get("retired")]
    assert len(live) >= 3, (
        f"살아 있는 보안 케이스가 {len(live)}개뿐입니다(폐기 {len(cases)-len(live)}). "
        "커머스를 격리한 만큼 보험 도메인의 보안 케이스를 새로 세워야 합니다."
    )


def test_retired_security_cases_are_actually_gone_not_just_labelled():
    """폐기 표시가 **깨진 테스트를 덮는 딱지**로 쓰이지 않는지.

    ★레거시 압축 격리 이후 이 파일의 13개가 줄곧 실패하고 있었다 —
      가리키던 커머스 테스트가 zip 안으로 들어갔기 때문이다.
      딱지만 붙이면 같은 일이 반복되므로, 가리키는 파일이 **정말로 없는지** 확인한다.
    """
    for c in _load_cases():
        entries = [(t, c.get("retired") or t.get("retired")) for t in c["tests"]]
        for t, reason in entries:
            if not reason:
                continue
            assert len(str(reason).strip()) > 10, f"{c['id']}: 폐기 이유가 비어 있습니다."
            rel = t["node"].split("::")[0]
            assert not (_REPO_ROOT / rel).exists(), (
                f"{c['id']}: {rel} 는 아직 있습니다. 폐기가 아니라 고쳐야 할 테스트입니다."
            )
