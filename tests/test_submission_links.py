"""제출 문서(`docs/submission/`)의 링크 계약.

★왜 테스트로 두나

    제출물은 **링크 허브**다 — `00` 이 전부를, `05` 가 `05A~05D` 를 가리킨다.
    링크 하나가 죽으면 심사자는 그 항목을 **제출하지 않은 것으로 본다.**
    문서를 나눈 대가라 사람이 아니라 기계가 지킨다.

★그리고 커머스 화면이 다시 들어오는 것을 막는다.

    `docs/screen_walkthrough.html` 은 쇼핑몰 실습 시절 산출물이다(주문·장바구니·facebench 18장).
    제출 문서가 이걸 "전체 앱 워크스루"로 링크해 뒀던 적이 있다 — 열면 쇼핑몰이 나온다.
    2026-08-04 에 뗐고, 되풀이되지 않게 여기서 막는다.
"""

from __future__ import annotations

from scripts.verify import check_submission_links as checker

#: 제출 요구 3항목의 정본. 하나라도 사라지면 제출물이 아니다.
REQUIRED = (
    "00_제출산출물_인덱스.md",
    "05_프로젝트_발표_보고서.md",
    "05A_DB_스키마.md",
    "05B_UI_와이어프레임_스토리보드.md",
    "05C_사용_LLM_모델.md",
    "05D_파인튜닝_모델_설계.md",
    "06_시연영상_시나리오.md",
    "07_프로젝트앱_결과물_인수인계.md",
    "08_시각화_자료_목록.md",
)

#: 발표에 띄우는 그림. 링크만이 아니라 **파일 자체**가 있어야 한다.
REQUIRED_VISUALS = (
    "docs/delivery/storyboard.html",
    "docs/delivery/presentation_visuals.html",
    "docs/handoff/system_diagrams.html",
    "docs/submission/01_부록_전처리_시각화.html",
)


def test_제출_정본_문서가_전부_있다():
    missing = [n for n in REQUIRED if not (checker.SUBMISSION / n).exists()]
    assert not missing, f"제출 정본 누락: {missing}"


def test_제출_문서의_상대링크가_전부_살아_있다():
    problems, summary = checker.check()
    #: ★검사한 링크가 0개면 통과가 아니라 **검사기가 고장 난 것**이다.
    assert summary["relative_links_checked"] > 0, "링크를 하나도 못 읽었다 — 검사기 확인 필요"
    assert not problems, "\n".join(
        f"[{p['kind']}] {p['where']} → {p['target']} : {p['detail']}" for p in problems
    )


def test_커머스_워크스루는_금지_목록에_있다():
    #: 금지 목록 자체가 지워지면 검사가 조용히 무력해진다.
    assert "docs/screen_walkthrough.html" in checker.BANNED


def test_발표용_시각화_파일이_있다():
    root = checker.SUBMISSION.parents[1]
    missing = [p for p in REQUIRED_VISUALS if not (root / p).exists()]
    assert not missing, f"발표에 쓰는 그림 누락: {missing}"


def test_전처리_시각화가_옛_DB_적재_수치를_달고_있지_않다():
    """★§G 가 손으로 적은 s5 시절 숫자를 달고 있어 「DB 3.5% 적재」로 읽혔다(2026-08-04).

    지금은 `build_preprocess_viz.py` 가 매번 DB 를 조회해 만든다.
    옛 숫자가 되살아나면 여기서 걸린다.
    """
    html = (checker.SUBMISSION / "01_부록_전처리_시각화.html").read_text(
        encoding="utf-8", errors="replace")
    stale = [s for s in ("156,946", "고유 내용은 3.5%뿐") if s in html]
    assert not stale, f"s5 시절 적재 수치가 다시 들어왔다: {stale}"
