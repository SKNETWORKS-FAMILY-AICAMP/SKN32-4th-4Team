"""TEST-DOC-001 — 핸드오프 계약 문서가 코드와 갈라지지 않았는지 검사한다.

★왜 이 테스트가 있나

    이 프로젝트가 반복해서 겪은 실패는 **같은 사실이 두 곳에 있으면 반드시 갈라진다**는 것이다.

      · ERD v2 안에 v1층과 v2층이 공존해 같은 테이블에 두 답이 나왔다
      · `verdict` enum 이 코드에는 4값인데 계약 문서 6개는 **코드에 없는 4값**을 쓰고 있었다
        (2026-08-02 발견). 프론트가 그 표대로 분기하면 어떤 응답에도 매칭되지 않는다 —
        `ModuleNotFoundError` 처럼 즉시 터지지 않고 **조용히 빈 화면**이 된다

    그런데 문서를 한 벌로 줄이는 것은 답이 아니다. 계약은 마크다운으로 읽고
    브리핑은 그림으로 읽는 게 낫다. **두 벌을 두되 갈라지는 순간 걸리게** 만든다.

    "그러지 말자"는 규칙이 아니라 **어기면 빨간불이 켜지는 구조**다.

★이 테스트가 검사하지 **않는** 것

    문장의 옳고 그름은 못 본다. 기계가 대조할 수 있는 것 —
    **enum 값**과 **import 경로** — 만 본다. 나머지는 사람이 읽어야 한다.
"""

from __future__ import annotations

import pathlib
import re

from app.core.domain.insurance import Verdict

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_HANDOFF = _ROOT / "docs" / "handoff"

#: ★한때 계약 문서가 쓰던, **코드에 존재하지 않는** verdict 값.
#:   `app/schemas/precheck.py` 주석에 경위가 남아 있다 —
#:   "처음에 이 4값을 새로 만들었다가 도메인에 이미 Verdict 가 있는 것을 뒤늦게 알았다."
_RETIRED_VERDICTS = ("not_covered", "conditional")

#: ★없는 패키지. `11_AI_구조_지도.md` 가 "여기에 다시 만들지 마라"고 못박았다.
_DEAD_PACKAGE = re.compile(r"app[./]insurance[./]")

#: ★죽은 경로를 **설명하려고** 적은 문서는 위반이 아니다.
#:
#:   처음엔 "쓰지 않는다" 같은 키워드로 걸러 봤는데 "없었다" · "없는 것은" ·
#:   "에 만들지 않는다" 같은 변형을 계속 놓쳐 오탐이 났다.
#:   `test_arch.py` 가 주석 속 `app.schemas` 를 위반으로 잡았던 것과 같은 함정이다.
#:
#:   ★결론: **키워드로 예외를 만들지 않는다.** 대신 두 가지로 정리했다.
#:     1) 경고 문구에서 죽은 경로를 아예 빼 썼다("새 최상위 패키지를 만들지 말 것").
#:        경고문에 나쁜 경로를 적어 두면 복붙 사고의 출처가 된다.
#:     2) 그 경로가 틀렸다는 **대조표 자체가 목적인 문서**만 아래에 면제한다.
#:   ★목록을 늘릴 때는 "이 문서의 목적이 정정 기록인가"를 근거로만 늘린다.
_PATH_CHECK_EXEMPT = {
    "10_계약_모델_평가.md",   # §2-0 이 경로 불일치 대조표다
    "11_AI_구조_지도.md",     # "app/insurance/ 에 다시 만들지 마라" 결정 기록
}


def _docs() -> list[pathlib.Path]:
    return sorted(p for p in _HANDOFF.iterdir() if p.suffix in (".md", ".html"))


def _offending_lines(path: pathlib.Path, pattern) -> list[tuple[int, str]]:
    return [
        (i, line.strip()[:110])
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]


# ── 1. verdict enum ──────────────────────────────────────────────────────

def test_계약문서에_폐기된_verdict_값이_남아있지_않다():
    """★이게 이 파일의 핵심이다. 조용히 빈 화면이 되는 결함을 막는다."""
    pat = re.compile("|".join(rf"`{v}`|\"{v}\"|'{v}'" for v in _RETIRED_VERDICTS))
    bad = {p.name: _offending_lines(p, pat) for p in _docs()}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, (
        "코드에 없는 verdict 값이 계약 문서에 남아 있다.\n"
        f"실제 값: {[v.value for v in Verdict]}\n"
        + "\n".join(f"  {f}:{ln}  {txt}" for f, hits in bad.items() for ln, txt in hits)
    )


def test_프론트_상태표가_실제_verdict_4값을_모두_다룬다():
    """화면이 못 그리는 verdict 가 있으면 그 응답은 사용자에게 안 보인다."""
    text = (_HANDOFF / "08_계약_프론트.md").read_text(encoding="utf-8")
    missing = [v.value for v in Verdict if f"`{v.value}`" not in text]
    assert not missing, f"08_계약_프론트.md 상태표에 빠진 verdict: {missing}"


def test_시각화와_계약문서의_verdict_가_같다():
    """★md 와 html 두 벌을 둔 대가 — 갈라지면 여기서 걸린다."""
    md = (_HANDOFF / "08_계약_프론트.md").read_text(encoding="utf-8")
    html = (_HANDOFF / "storyboard.html").read_text(encoding="utf-8")
    for v in Verdict:
        assert v.value in md, f"08 에 없다: {v.value}"
        assert v.value in html, f"storyboard.html 에 없다: {v.value}"


# ── 2. import 경로 ───────────────────────────────────────────────────────

def test_계약문서가_없는_패키지를_가리키지_않는다():
    """`app/insurance/` 는 존재하지 않는다. 계약서대로 쓰면 ModuleNotFoundError 다."""
    assert not (_ROOT / "app" / "insurance").exists(), (
        "app/insurance/ 가 생겼다. 11_AI_구조_지도.md 의 결정과 어긋난다 — "
        "먼저 그 문서를 고치고 이 테스트를 갱신하라."
    )
    bad = {p.name: _offending_lines(p, _DEAD_PACKAGE)
           for p in _docs() if p.name not in _PATH_CHECK_EXEMPT}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, (
        "계약 문서가 존재하지 않는 app/insurance/ 를 가리킨다.\n"
        + "\n".join(f"  {f}:{ln}  {txt}" for f, hits in bad.items() for ln, txt in hits)
    )


# ── 3. 링크 ──────────────────────────────────────────────────────────────

def test_README_가_가리키는_핸드오프_파일이_모두_존재한다():
    """★링크만 커밋되고 파일이 빠지면 그 순간 깨진 계약이 된다(코덱스 지적)."""
    readme = (_HANDOFF / "README.md").read_text(encoding="utf-8")
    missing = [
        t for t in re.findall(r"\]\(([^)]+\.(?:md|html))\)", readme)
        if not t.startswith(("http", "..", "/")) and not (_HANDOFF / t).exists()
    ]
    assert not missing, f"README 가 없는 파일을 가리킨다: {missing}"
