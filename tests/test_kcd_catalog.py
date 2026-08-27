"""약관에 나오는 질병기호 목록 — **KCD 사전이라고 말하지 않는다.**

★왜 이 시험이 있나

    우리는 KCD 코드→질병명 표를 갖고 있지 않다(약 2만 항목).
    `app/core/domain/kcd_ranges.py` 머리말이 그렇게 적어 두었다.

    그런데 관리자 화면에 「질병기호 표」를 만들면, 다음 사람이 거기에
    질병명을 채워 넣고 싶어진다. 근거 없이 채우면 **틀린 병명이 판정 화면까지
    간다.** 그래서 「무엇이 아닌지」를 시험으로 못박는다.

★무엇을 지키나

    1. 응답이 **분모를 함께** 낸다 — 걸러도 전체 수를 잃지 않는다
    2. 응답이 **한계를 스스로 말한다** — 사전이 아니라는 것
    3. 목록이 없으면 **없다고 말한다** — 빈 목록으로 때우지 않는다
    4. 장 판정은 경계에서 정확하다
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.core.domain.kcd_ranges import chapter_of

_CATALOG = pathlib.Path("data/exports/kcd_catalog.json")


@pytest.mark.parametrize(
    ("code", "chapter"),
    [
        ("A00", "1 감염성·기생충"),
        ("B99", "1 감염성·기생충"),
        ("C00", "2 신생물"),
        ("D48", "2 신생물"),
        ("D50", "3 혈액·면역"),   # ★같은 글자 안에서 갈린다 — 숫자를 봐야 한다
        ("F04", "5 정신·행동"),
        ("H59", "7 눈"),
        ("H60", "8 귀"),          # ★H 는 눈과 귀로 갈린다
        ("N39.3", "14 비뇨생식"),
        ("O00", "15 임신·출산"),
        ("Q82.5", "17 선천기형"),
        ("S72.0", "19 손상·중독"),
        ("T98", "19 손상·중독"),
    ],
)
def test_장_판정이_경계에서_정확하다(code: str, chapter: str) -> None:
    """★문자열 비교로는 `H59`/`H60` 을 못 가른다. `(글자, 숫자)` 로 봐야 한다."""
    assert chapter_of(code) == chapter


def test_모르는_코드는_기타로_답한다():
    """★없는 장을 지어내지 않는다."""
    assert chapter_of("") == "기타"
    assert chapter_of("몰라") == "기타"


def test_장_표가_도메인에_있다():
    """★★이 표는 `scripts/viz/regen_preprocess_viz.py` 안에 **묻혀 있었다**.

    시각화 스크립트가 도메인 지식을 들고 있으면 그게 필요한 다른 곳이
    **같은 표를 또 만든다.** 그러면 둘이 어긋난다.
    """
    import inspect

    from app.core.domain import kcd_ranges

    src = inspect.getsource(kcd_ranges)
    assert "_CHAPTERS" in src and "def chapter_of" in src


def test_카탈로그가_사전이_아님을_스스로_말한다():
    if not _CATALOG.exists():
        pytest.skip("카탈로그 미생성 — `python -m scripts.eval.kcd_catalog`")
    d = json.loads(_CATALOG.read_text(encoding="utf-8"))
    limits = " ".join(d.get("★한계") or [])
    assert limits, "한계를 적지 않았습니다 — 사전으로 오해된다"
    assert "사전이 아니" in limits
    #: ★분모를 갖고 있어야 한다. 「525종」만 내보내면 KCD 전체인 줄 안다.
    assert d.get("scanned_policies", 0) > 0
    assert "total_mentions" in d and "read_failed" in d
    #: ★질병명 필드를 만들지 않았는지. 있으면 누군가 근거 없이 채운 것이다.
    for x in (d.get("items") or [])[:50]:
        assert "name" not in x and "disease" not in x and "name_ko" not in x, (
            f"질병명 필드가 생겼습니다 — 근거 없이 채운 것 아닙니까: {x}")


def test_API_가_걸러도_분모를_잃지_않는다():
    if not _CATALOG.exists():
        pytest.skip("카탈로그 미생성")
    from fastapi.testclient import TestClient

    from app.auth.roles import require_admin
    from app.main import admin_app

    admin_app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "ADMIN"}
    try:
        c = TestClient(admin_app)
        whole = c.get("/api/admin/kcd-codes").json()
        assert whole["matched"] == whole["total_ranges"]

        part = c.get("/api/admin/kcd-codes?kind=exception").json()
        #: ★거른 뒤에도 **전체 수가 남아 있어야** 한다.
        assert part["total_ranges"] == whole["total_ranges"]
        assert part["matched"] <= whole["matched"]
        assert all(x["kind"] == "exception" for x in part["items"])
    finally:
        admin_app.dependency_overrides.clear()


def test_고객_입력도우미가_약관범위를_단일_상병코드로_제공하지_않는다():
    """C30~C39는 약관의 분류 범위이지 환자 진단서의 단일 코드가 아니다."""
    if not _CATALOG.exists():
        pytest.skip("카탈로그 미생성")
    from fastapi.testclient import TestClient

    from app.main import create_app

    c = TestClient(create_app("customer"))
    ranged = c.get("/v1/catalog/codes?q=C30~C39").json()
    item = next(x for x in ranged["items"] if x["code"] == "C30~C39")
    assert item["input_allowed"] is False

    exact = c.get("/v1/catalog/codes?q=N39.3").json()
    item = next(x for x in exact["items"] if x["code"] == "N39.3")
    assert item["input_allowed"] is True


def test_약관범위를_직접_제출해도_판정하지_않고_422로_설명한다():
    """오래 캐시된 클라이언트나 외부 API 호출도 범위를 판정으로 흘리지 않는다."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    r = TestClient(create_app("customer")).post(
        "/v1/prechecks",
        json={"insurer": "테스트", "enrolled_on": "20260804", "kcd_codes": ["C30~C39"]},
    )
    assert r.status_code == 422
    assert "약관의 코드 범위" in r.json()["detail"]
    assert "C34.1" in r.json()["detail"]


def test_목록이_없으면_없다고_말한다():
    """★빈 목록으로 때우면 「등장하는 코드가 없다」로 읽힌다 — 전혀 다른 뜻이다.

    ★확인 방법: 카탈로그를 **잠깐 옮겨** 두고 응답을 본다. 경로를 몽키패치하려
      했더니 그 경로가 함수 안에서 만들어져 잡히지 않았고, `__dict__` 를 건드리는
      쪽으로 새다가 시험이 깨졌다 — **파일을 옮기는 쪽이 단순하고 진짜에 가깝다.**
    """
    from fastapi.testclient import TestClient

    from app.auth.roles import require_admin
    from app.main import admin_app

    cat = pathlib.Path("data/exports/kcd_catalog.json")
    if not cat.exists():
        pytest.skip("카탈로그 미생성")

    admin_app.dependency_overrides[require_admin] = lambda: {"username": "t", "role": "ADMIN"}
    backup = cat.with_suffix(".json.testbak")
    cat.rename(backup)
    try:
        r = TestClient(admin_app).get("/api/admin/kcd-codes")
        assert r.status_code == 503, "목록이 없는데 200 으로 답하고 있습니다"
        assert "kcd_catalog" in r.json()["detail"], "무엇을 실행해야 하는지 안 알려 줍니다"
    finally:
        #: ★반드시 되돌린다. 시험이 저장소 상태를 바꾸고 끝나면 안 된다.
        backup.rename(cat)
        admin_app.dependency_overrides.clear()
