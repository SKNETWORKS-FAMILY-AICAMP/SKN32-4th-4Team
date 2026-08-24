"""TEST-MATRIX-001 — 요구사항 매트릭스 정적 검증(Phase 10).

`requirements_matrix.yaml`에 적힌 pytest node id가 (1) 실제로 **pytest 자신의 수집기로
collect**되고 (2) skip/xfail로 죽어있지 않은지 확인한다.

Codex 지적 반영: 최초 버전은 `import + hasattr`로만 "존재"를 확인했는데, 이는 pytest가
실제로 그 node를 collect할 수 있다는 보장이 아니다(fixture 의존성 문제·수집 훅 등으로
import는 되지만 collect는 실패할 수 있음). 이번 버전은 `pytest --collect-only`를 파일당
1회 서브프로세스로 실행해 **실제 수집된 node id 목록**과 대조한다(더 강한 보장).
또한 node 문자열 파싱 실패를 조용히 건너뛰지 않는다 — 매트릭스의 모든 항목은 예외 없이
검증 대상이어야 하며, 형식이 안 맞으면 그 자체로 실패다(가짜/오타 매핑을 숨기지 않음).
"""

from __future__ import annotations

import functools
import os
import pathlib
import re
import subprocess
import sys

import pytest
import yaml

_MATRIX_PATH = pathlib.Path(__file__).resolve().parent / "requirements_matrix.yaml"
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_NODE_RE = re.compile(r"^(tests/[\w./]+\.py)::(\w+)$")


def _load_matrix() -> list[dict]:
    data = yaml.safe_load(_MATRIX_PATH.read_text(encoding="utf-8"))
    return data["requirements"]


def _parse_node(node: str) -> tuple[str, str]:
    """`tests/test_x.py::test_y` → (상대경로, 함수명). 형식이 안 맞으면 예외(조용한 스킵 없음)."""
    m = _NODE_RE.match(node)
    if not m:
        raise ValueError(f"node id 형식이 아닙니다(가짜/오타 매핑 의심): {node!r}")
    return m.group(1), m.group(2)


@functools.lru_cache(maxsize=None)
def _all_collected_node_ids() -> frozenset[str]:
    """`pytest --collect-only tests/`를 **전체 1회**만 실행해 실제 collect된 전체 node id
    집합을 반환한다(프로세스 시작 시 1회 계산 후 캐시).

    import+hasattr보다 강한 보장: pytest 자신의 수집기를 그대로 쓴다. 처음엔 매트릭스
    항목마다(또는 파일마다) 서브프로세스를 새로 띄웠는데, 무거운 앱 임포트(torch·
    sentence-transformers 등)가 매번 재실행돼 파일 15개 기준 2분을 넘겨 비현실적이었다.
    전체 스위트를 **한 번만** collect해 20초 안팎으로 낮췄다(측정 확인).

    타임아웃 값(240초)의 근거: 테스트 파일이 늘어나며 collect 시간이 증가했다. 유휴 상태
    실측 **49초**이지만, 전체 스위트와 동시에 돌 때(무거운 ML 모델 로딩과 CPU 경합) 기존
    90초 제한을 넘겨 **거짓 실패**가 났다. 이 검사의 목적은 "node가 수집 가능한가"이므로
    타임아웃은 답이 아니라 측정 실패다 → 여유를 늘리고, 아래에서 타임아웃을 수집 실패와
    **구분해** 보고한다(원인을 오해하지 않도록).
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "tests/"],
            cwd=_REPO_ROOT,
            #: ★★**자식 프로세스의 출력 인코딩을 못 박는다.**
            #:
            #:   파이프로 받을 때 Windows 자식 pytest 는 stdout 을 **로케일(cp949)** 로 쓴다.
            #:   우리는 utf-8 로 디코딩하므로 **한글 테스트 이름이 U+FFFD 로 뭉개진다.**
            #:   그러면 매트릭스의 한글 node id 가 절대 일치하지 않는다.
            #:
            #:   그동안 안 터진 이유는 매트릭스가 가리키던 node 가 전부 ASCII 이름이었기
            #:   때문이다. 2026-08-04 에 한글 이름 node 를 등재하자 **전체 스위트로 돌릴 때만**
            #:   실패했다 — 단독 실행 때는 셸이 `PYTHONIOENCODING` 을 넘겨 우연히 통과했다.
            #:   "가끔 통과하는 가드"는 가드가 아니다.
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            capture_output=True,
            text=True,
            #: ★인코딩을 못 박는다.
            #:
            #:   `text=True` 만 두면 Windows 에서 **시스템 코드페이지(cp949)** 로 디코딩한다.
            #:   테스트 id 에 한글이 섞여 있어 리더 스레드가 `UnicodeDecodeError` 로 죽고,
            #:   `proc.stdout` 이 `None` 이 되어 아래가 통째로 실패했다 —
            #:   그래서 매트릭스 36개·보안 13개가 **Windows 에서만 항상 빨간불**이었다.
            #:   CI(우분투)는 UTF-8 기본이라 통과해 아무도 눈치채지 못했다.
            #:   `errors="replace"` 는 node id 비교에 영향을 주지 않는다(ASCII 부분만 쓴다).
            encoding="utf-8",
            errors="replace",
            timeout=240,
        )
    except subprocess.TimeoutExpired as exc:
        # 수집 불가가 아니라 **측정 자체가 시간 안에 끝나지 않은 것**이다. 둘을 섞어
        # 보고하면 "요구사항이 테스트에 연결되지 않았다"는 잘못된 결론으로 이어진다.
        raise RuntimeError(
            "전체 collect가 제한 시간(240초) 안에 끝나지 않았습니다 — 수집 실패가 아니라 "
            "측정 타임아웃입니다(머신 부하 가능성). 단독 실행으로 재확인하세요: "
            "pytest tests/test_requirements_matrix.py"
        ) from exc
    if proc.returncode != 0:
        # 일부 파일만 수집 오류가 나도 나머지 파일의 정상 node는 여전히 출력에 섞여 나온다
        # (Codex 지적) — "결과가 비어있지 않음"만으로는 전체 성공을 보장 못 하므로 exit code로 확인.
        raise RuntimeError(
            f"전체 collect가 실패했습니다(returncode={proc.returncode}). "
            f"stdout={proc.stdout[-800:]!r} stderr={proc.stderr[-800:]!r}"
        )
    ids = {line.strip() for line in proc.stdout.splitlines() if "::" in line}
    if not ids:
        raise RuntimeError(
            f"전체 collect 결과가 비어 있습니다. "
            f"stdout={proc.stdout[-500:]!r} stderr={proc.stderr[-500:]!r}"
        )
    return frozenset(ids)


def _is_collected(node: str) -> bool:
    """node가 실제 collect 결과에 있는지 확인. 파라미터화 함수는 `node[param]` 형태로
    collect되므로 정확 일치뿐 아니라 접두 매칭도 인정한다(매트릭스는 파라미터 값을
    명시하지 않고 함수 전체를 가리키는 것이 정상 용법)."""
    collected = _all_collected_node_ids()
    return node in collected or any(c.startswith(f"{node}[") for c in collected)


def _skip_or_xfail_marks(rel_path: str, func_name: str) -> set[str]:
    """함수 자체의 pytestmark만 본다(모듈/클래스 상속 마크나 동적 skipif는 한계로 인정 — §리포트)."""
    import importlib

    module = importlib.import_module(rel_path[:-3].replace("/", "."))
    func = getattr(module, func_name, None)
    if func is None:
        return set()
    return {m.name for m in getattr(func, "pytestmark", [])}


def _iter_all_entries():
    for req in _load_matrix():
        if req.get("retired"):
            #: ★기능 자체가 사라진 요구사항. 아래 `test_retired_...` 가 따로 검증한다.
            continue
        for t in req["tests"]:
            if t.get("retired"):
                #: ★요구사항은 살아 있는데 **노드 하나만** 사라진 경우.
                #:   요구사항째 폐기하면 살아 있는 검증까지 꺼진다.
                continue
            yield req["id"], t["node"], t.get("basis")


@pytest.mark.parametrize(
    "req_id,node,basis", list(_iter_all_entries()), ids=lambda v: str(v)[:40]
)
def test_matrix_node_is_actually_collectible_and_not_skipped(req_id, node, basis):
    rel_path, func_name = _parse_node(node)  # 형식 오류는 여기서 바로 실패(조용한 제외 없음)
    file_path = _REPO_ROOT / rel_path
    assert file_path.exists(), f"[{req_id}] 파일이 없습니다: {rel_path}"

    assert _is_collected(node), f"[{req_id}] pytest가 실제로 이 node를 collect하지 못했습니다: {node}"

    mark_names = _skip_or_xfail_marks(rel_path, func_name)
    assert "skip" not in mark_names, f"[{req_id}] {node}가 skip 처리돼 있어 실행되지 않습니다."
    assert "xfail" not in mark_names, f"[{req_id}] {node}가 xfail이라 실패를 검증하지 않습니다."

    assert isinstance(basis, str) and basis.strip(), (
        f"[{req_id}] {node}에 basis(직접확인 근거, 문자열)가 비어 있습니다."
    )


def test_matrix_covers_every_must_req_at_least_once():
    """매트릭스에 등재된 REQ가 최소 1개 이상이고 중복 id가 없는지.

    한계(정직): 이 프로젝트의 "정본 MUST 목록"을 별도 기계 판독 가능 소스로 관리하지
    않으므로, 여기서는 개수 하한과 중복 없음만 확인한다 — v3.2 계획서 표와의 완전 대조는
    수행하지 않는다(Codex 지적, 리포트에 한계로 명시).
    """
    reqs = _load_matrix()
    ids = [r["id"] for r in reqs]
    assert len(ids) == len(set(ids)), "매트릭스에 중복 REQ id가 있습니다."
    assert len(ids) >= 20, f"MUST REQ 수가 비정상적으로 적습니다: {len(ids)}"
    for r in reqs:
        assert r["status"] == "DONE", f"{r['id']}가 DONE이 아닙니다: {r['status']}"
        assert r["tests"], f"{r['id']}에 매핑된 테스트가 없습니다."

    #: ★폐기된 것을 **센다.** 조용히 빼면 커버리지가 실제보다 좋아 보인다.
    live = [r for r in reqs if not r.get("retired")]
    assert len(live) >= 15, (
        f"살아 있는 REQ 가 {len(live)}개뿐입니다(폐기 {len(reqs)-len(live)}). "
        "폐기가 늘고 있다면 매트릭스가 제품을 못 따라가고 있는 것입니다."
    )


def test_retired_requirements_are_actually_gone_not_just_labelled():
    """폐기 표시가 **깨진 테스트를 덮는 딱지**로 쓰이지 않는지.

    ★레거시를 압축 격리하면서 커머스 테스트 파일들이 사라졌는데
      매트릭스는 계속 그 파일을 가리키고 있었다. 그 결과 격리 커밋 이후
      매트릭스 테스트 49개가 **줄곧 빨간불이었다**(2026-08-02에 발견).
      선택한 테스트 파일만 돌려 보느라 몰랐다.

    딱지를 붙이는 것으로 끝내면 같은 일이 반복된다. 그래서 두 가지를 강제한다 —
      1. 폐기에는 **이유**가 있어야 한다.
      2. 가리키는 파일이 **정말로 없어야** 한다. 파일이 살아 있으면 폐기가 아니라
         **고쳐야 할 테스트**다.
    """
    for r in _load_matrix():
        for t in r["tests"]:
            reason = r.get("retired") or t.get("retired")
            if not reason:
                continue
            assert isinstance(reason, str) and len(reason.strip()) > 10, (
                f"{r['id']}: 폐기 이유가 비어 있습니다."
            )
            rel = t["node"].split("::")[0]
            assert not (_REPO_ROOT / rel).exists(), (
                f"{r['id']}: {rel} 는 아직 있습니다. 폐기가 아니라 고쳐야 할 테스트입니다."
            )
