"""챗봇 용어 도우미 — **사전이라고 말하지 않는다.**

★왜 이 시험이 있나

    챗봇 예시 칩이 4개 하드코딩돼 있어 사용자가 무엇을 물어볼 수 있는지 몰랐다.
    그래서 「약관에 정의가 있는 용어」 목록을 만들었는데, 여기엔 두 가지 함정이 있다.

      ① **사전으로 오해된다.** 뜻은 여기 담지 않는다 — 챗봇이 약관 원문으로 답한다.
      ② **「물어볼 수 있는 전부」로 오해된다.** 목록에 없는 낱말도 물어볼 수 있다.
         목록에서 자기 낱말을 못 찾은 사용자가 **질문 자체를 포기**하는 것이
         이 기능의 주된 위험이다.

★그리고 목록은 **검증된 것만** 담아야 한다

    검증을 안 하면 「목록에 있는데 눌러 보면 못 찾는」 칩이 생긴다.
    입력 도우미로서 그게 최악이다.
"""

from __future__ import annotations

import json
import pathlib

import pytest

_CATALOG = pathlib.Path("data/exports/glossary_terms.json")


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_카탈로그가_사전이_아님을_스스로_말한다():
    if not _CATALOG.exists():
        pytest.skip("용어 목록 미생성 — `python -m scripts.eval.glossary_terms`")
    d = json.loads(_CATALOG.read_text(encoding="utf-8"))
    limits = " ".join(d.get("★한계") or [])
    assert "사전이 아니" in limits, "사전으로 오해될 수 있는데 한계를 안 적었습니다"
    assert "목록에 없는" in limits, "목록 밖 낱말도 물어볼 수 있다는 사실이 없습니다"
    #: ★뜻 필드를 만들지 않았는지. 있으면 누군가 사전으로 만들려 한 것이다.
    for x in (d.get("items") or [])[:80]:
        assert not ({"definition", "meaning", "desc", "뜻"} & set(x)), (
            f"뜻 필드가 생겼습니다 — 여긴 사전이 아닙니다: {x}")
    #: 분모를 갖고 있어야 한다.
    assert d.get("scanned_policies", 0) > 0
    assert "junk_removed" in d, "거른 조각 수를 안 셌습니다 — 조용한 스킵입니다"


def test_목록의_용어는_실제로_답이_나온다():
    """★★검증 안 된 목록은 「눌렀는데 못 찾는」 칩을 만든다."""
    if not _CATALOG.exists():
        pytest.skip("용어 목록 미생성")
    d = json.loads(_CATALOG.read_text(encoding="utf-8"))
    items = d.get("items") or []
    if not items:
        pytest.skip("용어 0건")

    from app.core.errors import InfraError

    from app.adapters import file_glossary_source as src

    #: 상위·중간·하위를 골고루 — 상위만 보면 꼬리의 쓰레기를 놓친다.
    idx = [0, len(items) // 4, len(items) // 2, (3 * len(items)) // 4, len(items) - 1]
    for i in idx:
        term = items[i]["term"]
        try:
            hits = src.find(term, limit=1)
        except InfraError as exc:
            #: ★★신규 클론·팀 CI 에는 용어 색인 원본이 없다(gitignore 대상).
            #:   그때 `find()` 는 「색인이 없다」고 **정확히** 답한다 —
            #:   그건 이 시험이 볼 대상이 아니다. 환경 부재를 결함으로 보고하면 거짓 실패다.
            #:   실측 2026-08-04: 이 처리를 안 해서 팀 트리에서 깨졌다.
            pytest.skip(f"용어 색인이 없는 환경 — {str(exc)[:60]}")
        assert hits, f"목록에 있는데 검색이 0건입니다: {term!r}"


def test_조각을_용어라고_하지_않는다():
    """★처음엔 본문에서 한글 낱말을 그냥 긁어 `에서`·`료비`·`료기관` 같은
    **깨진 조각** 5,968개가 나왔다. 정의표가 PDF 안에서 칸이 무너져 있기 때문이다.
    """
    if not _CATALOG.exists():
        pytest.skip("용어 목록 미생성")
    d = json.loads(_CATALOG.read_text(encoding="utf-8"))
    terms = {x["term"] for x in (d.get("items") or [])}
    for junk in ("에서", "료비", "료기관", "따른", "이며", "말함"):
        assert junk not in terms, f"조각이 용어로 들어갔습니다: {junk!r}"
    #: 쪽번호·조문 참조도 용어가 아니다.
    #: ★검사를 「제·조 글자가 있나」로 했더니 `직접조제비` 가 걸렸다 —
    #:   정상 용어인데 시험이 틀린 것이다. **조문 참조의 모양**(`제42조`)으로 본다.
    import re as _re

    article = _re.compile(r"제\s*\d+\s*조")
    for t in terms:
        assert not t.isdigit(), f"쪽번호가 용어로 들어갔습니다: {t!r}"
        assert not article.search(t), f"조문 참조가 용어로 들어갔습니다: {t!r}"


def test_API_가_분모를_잃지_않고_한계를_말한다():
    if not _CATALOG.exists():
        pytest.skip("용어 목록 미생성")
    c = _client()
    whole = c.get("/v1/chat/terms").json()
    assert whole["matched"] == whole["total_terms"]
    notes = " ".join(whole.get("notes") or [])
    assert "목록에 없는" in notes, "목록 밖 낱말도 물어볼 수 있다는 안내가 없습니다"
    assert "보장 여부는" in notes, "이 대화창이 보장을 답하지 않는다는 사실이 없습니다"

    part = c.get("/v1/chat/terms?q=치료").json()
    #: ★거른 뒤에도 전체 수가 남아야 한다.
    assert part["total_terms"] == whole["total_terms"]
    assert part["matched"] <= whole["matched"]
    assert all("치료" in x["term"] for x in part["items"])


def test_목록이_없으면_없다고_말한다():
    """★빈 목록으로 때우면 「약관에 정의된 용어가 없다」로 읽힌다 — 전혀 다른 뜻이다."""
    if not _CATALOG.exists():
        pytest.skip("용어 목록 미생성")
    backup = _CATALOG.with_suffix(".json.testbak")
    _CATALOG.rename(backup)
    try:
        r = _client().get("/v1/chat/terms")
        assert r.status_code == 503, "목록이 없는데 200 으로 답하고 있습니다"
        assert "직접 물어보실 수 있습니다" in r.json()["detail"], (
            "목록이 없어도 물어볼 수 있다는 사실을 안 알려 줍니다")
    finally:
        #: ★반드시 되돌린다. 시험이 저장소 상태를 바꾸고 끝나면 안 된다.
        backup.rename(_CATALOG)


def test_화면이_칩을_하드코딩하지_않는다():
    """★칩을 박아 두면 약관이 바뀌어도 그대로 남아 **눌렀는데 못 찾는** 칩이 생긴다.

    화면은 서버 목록(`/v1/chat/terms`)에서 칩을 채워야 한다.
    """
    js = pathlib.Path("app/static/insurance.js").read_text(encoding="utf-8")
    assert "/v1/chat/terms" in js, "화면이 용어 목록을 서버에서 받지 않습니다"
    assert "loadChatTerms" in js

    html = pathlib.Path("app/static/insurance.html").read_text(encoding="utf-8")
    #: 입력창 자동완성이 붙어 있어야 한다.
    assert 'list="chatTerms"' in html and 'id="chatTerms"' in html
    assert 'data-q=' not in html, "API 실패 뒤에도 하드코딩 추천 질문이 정상 데이터처럼 남습니다"
    #: 칩 영역이 좌우로 넘칠 수 있어야 한다.
    assert "overflow-x: auto" in html
