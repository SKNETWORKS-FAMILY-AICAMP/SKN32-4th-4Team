"""용어 설명 — 판정으로 넘어가지 않는가, 지어내지 않는가.

★이 테스트가 지키는 것은 **출력 계약**이다.
  "약관 원문만 인용한다" 와 "보장 여부는 답하지 않는다".
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.errors import InfraError, ValidationErr
from app.core.ports.glossary import GlossarySourcePort, TermPassage
from app.core.usecases import glossary


def _p(text: str, *, kind="clause", insurer="가보험", sha="a" * 64) -> TermPassage:
    return TermPassage(
        kind=kind,
        sha256=sha,
        insurer=insurer,
        qualified_no="보통약관/2.",
        section="보통약관",
        title="용어의 정의",
        page_from=3,
        page_to=3,
        content_hash="deadbeefcafe",
        text=text,
    )


class _Fake:
    def __init__(self, rows: list[TermPassage]):
        self.rows = rows

    def find(self, term, *, insurer=None, limit=20):
        hit = [r for r in self.rows if term in r.text and (not insurer or r.insurer == insurer)]
        #: ★`limit=0` 은 상한 없음 — 어댑터와 같게 둔다.
        return hit[:limit] if limit else hit

    def meta(self):
        return {"built_from": "s5", "documents": 2, "clause_passages": 1, "appendix_passages": 1}


def test_포트를_만족한다():
    assert isinstance(_Fake([]), GlossarySourcePort)


def test_찾으면_원문을_그대로_인용한다():
    src = _Fake([_p("2. (용어의 정의)\n도수치료 치료자가 손을 이용해서 실시하는 치료행위")])
    ans = glossary.explain("도수치료", source=src)
    assert ans.found
    assert "치료자가 손을" in ans.quotes[0].quote
    assert ans.quotes[0].locator.startswith("aaaaaaaaaaaa/보통약관/2. p3")


def test_못_찾으면_지어내지_않는다():
    ans = glossary.explain("도수치료", source=_Fake([]))
    assert ans.found is False
    assert ans.quotes == ()
    assert "찾지 못했습니다" in ans.message


def test_근거_없이_찾았다고_말할_수_없다():
    #: ★구조로 막는다. 필드를 손으로 채워도 만들어지지 않는다.
    with pytest.raises(ValidationErr):
        glossary.TermExplanation(term="상해", found=True, quotes=())


def test_보장여부는_답하지_않는다():
    src = _Fake([_p("2. (용어의 정의)\n상해 보험기간 중 발생한 급격하고 우연한 외래의 사고")])
    ans = glossary.explain("상해", source=src)
    #: ★출력에 verdict 필드 자체가 없다. 있으면 언젠가 채우게 된다.
    assert not hasattr(ans, "verdict")
    assert any("보장 여부는 여기서 판정하지 않습니다" in w for w in ans.warnings)


def test_약관마다_다른_정의를_합치지_않는다():
    src = _Fake(
        [
            _p("통원 병원에 가서 치료받는 것 (가)", insurer="가보험", sha="a" * 64),
            _p("통원 병원에 가서 치료받는 것 (나)", insurer="나보험", sha="b" * 64),
        ]
    )
    ans = glossary.explain("통원", source=src)
    assert len(ans.quotes) == 2
    assert ans.insurers == ("가보험", "나보험")
    assert any("다를 수 있어 합치지 않고" in w for w in ans.warnings)


def test_붙임_정의표는_칸이_무너진다고_알린다():
    src = _Fake([_p("붙임1_용어의 정의\n용 어\n정  의\n계약\n보험계약", kind="appendix")])
    ans = glossary.explain("계약", source=src)
    assert any("줄과 칸이 흐트러져" in w for w in ans.warnings)


def test_너무_짧은_용어는_거절한다():
    with pytest.raises(ValidationErr):
        glossary.explain("의", source=_Fake([]))


def test_같은_인용을_반복하지_않는다():
    #: 조항 중복률이 59.7% 라 같은 문장이 여러 문서에 그대로 실려 있다.
    same = "2. (용어의 정의)\n입원 의사가 필요하다고 인정한 경우로서 …"
    src = _Fake([_p(same, sha=c * 64) for c in "abcd"])
    ans = glossary.explain("입원", source=src, max_quotes=3)
    assert len(ans.quotes) == 1


def test_줄바꿈과_페이지_장식만_다른_동일_정의를_묶는다():
    rows = [
        _p(
            "입원의료비 입원실료\n통원\n의사가 피보험자의 질병 또는 상해로 치료가 "
            "필요하다고 인정하는 경우로서 의료기관에 입원하지 않고 의료기관을 방문하여 "
            "의사의 관리하에 치료에 전념하는 것\n처방조제"
        ),
        _p(
            "입원의료비 비급여 병실료\n통원\n의사가 피보험자의 질병 또는 상해로 치료가\n"
            "필요하다고 인정하는 경우로서 의료기관에\n보\n통\n약\n관\n"
            "☞ 목차로 돌아가기\n84\n용 어\n정  의\n입원하지 않고 의료기관을 방문하여 "
            "의사의 관리 하에 치료에 전념하는 것\n처방조제",
            sha="b" * 64,
        ),
        _p(
            "입원의료비 상급병실료 차액\n통원\n의사가 피보험자의 질병 또는 상해로 치료가 "
            "필요하다고 인정하는 경우로서 병원에 입원하지 않고 병원을 방문하여 의사의 "
            "관리 하에 치료에 전념하는 것\n처방조제",
            sha="c" * 64,
        ),
    ]
    ans = glossary.explain("통원", source=_Fake(rows), insurer="가보험")
    assert len(ans.quotes) == 2
    assert "의료기관에 입원하지 않고" in ans.quotes[0].quote
    assert "병원에 입원하지 않고" in ans.quotes[1].quote
    assert any("동일 정의 1개" in warning for warning in ans.warnings)


def test_같은_보험사라도_뜻이_다른_정의는_남긴다():
    src = _Fake(
        [
            _p("통원 의료기관에 입원하지 않고 방문하여 치료받는 것"),
            _p("통원 전화나 화상으로 상담만 받는 것", sha="b" * 64),
        ]
    )
    ans = glossary.explain("통원", source=src)
    assert len(ans.quotes) == 2


def test_보험사가_다르면_같은_문구도_각각_남긴다():
    same = "통원 의료기관에 입원하지 않고 방문하여 치료받는 것"
    src = _Fake(
        [
            _p(same, insurer="가보험"),
            _p(same, insurer="나보험", sha="b" * 64),
        ]
    )
    ans = glossary.explain("통원", source=src)
    assert len(ans.quotes) == 2


# ---------------------------------------------------------------- API


@pytest.fixture()
def client(monkeypatch):
    from app import composition
    from app.main import create_app

    rows = [
        _p("2. (용어의 정의)\n도수치료 치료자가 손을 이용해서 실시하는 치료행위"),
        _p("붙임1_용어의 정의\n용 어\n정  의\n계약\n보험계약", kind="appendix", insurer="나보험"),
    ]
    monkeypatch.setattr(composition, "build_glossary", lambda: _Fake(rows))
    return TestClient(create_app("full"))


def test_찾음은_200(client):
    r = client.get("/v1/terms/explain", params={"term": "도수치료"})
    assert r.status_code == 200
    b = r.json()
    assert b["found"] is True and b["quotes"]
    assert "verdict" not in b
    assert b["index"]["built_from"] == "s5"


def test_못_찾음도_200이다(client):
    #: ★"약관에 없다"는 오류가 아니라 **정상 결과**다. 404 로 내보내면
    #:   클라이언트가 장애와 구분하지 못한다.
    r = client.get("/v1/terms/explain", params={"term": "존재하지않는용어"})
    assert r.status_code == 200
    assert r.json()["found"] is False


def test_짧은_용어는_422(client):
    assert client.get("/v1/terms/explain", params={"term": "의"}).status_code == 422


def test_색인이_없으면_503이다(monkeypatch):
    from app import composition
    from app.main import create_app

    class _Broken:
        def find(self, *a, **k):
            raise InfraError("postgresql://admin:secret@internal/db C:/private/passages.jsonl")

        def meta(self):
            raise InfraError("용어 색인 메타가 없습니다")

    monkeypatch.setattr(composition, "build_glossary", lambda: _Broken())
    r = TestClient(create_app("full")).get("/v1/terms/explain", params={"term": "상해"})
    #: ★색인이 없는 것을 빈 결과로 돌려주면 "약관에 그 용어가 없다"로 읽힌다.
    assert r.status_code == 503
    assert "secret" not in r.text and "private" not in r.text
    assert r.json()["detail"] == "서비스 의존 시스템을 사용할 수 없습니다."


def test_실제_색인이_있으면_동작한다():
    """색인이 만들어져 있으면 진짜로 조회된다(없으면 건너뛴다)."""
    from pathlib import Path

    idx = Path(__file__).resolve().parents[1] / "data" / "glossary" / "passages.jsonl"
    if not idx.exists():
        pytest.skip("용어 색인 없음 — build_glossary 미실행")
    from app.adapters import file_glossary_source

    ans = glossary.explain("도수치료", source=file_glossary_source)
    assert ans.found and ans.total_passages > 0
    meta = json.loads((idx.parent / "meta.json").read_text(encoding="utf-8"))
    assert meta["clause_passages"] > 0
