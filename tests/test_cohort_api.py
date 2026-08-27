"""청구 승인율 엔드포인트.

★합성과 실제를 **엔드포인트로 나눈다.** 한 곳에 스위치를 두면 섞인다.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.adapters import file_cohort_stats as adapter
from app.main import create_app


@pytest.fixture
def client(monkeypatch, tmp_path):
    base = tmp_path / "cohort"
    (base / "verified_real").mkdir(parents=True)
    (base / "synthetic").mkdir(parents=True)
    (base / "verified_real" / "events.jsonl").write_text("", encoding="utf-8")
    (base / "synthetic" / "events.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(adapter, "_BASE", base)
    return TestClient(create_app())


def test_실제_데이터는_지금_0건이고_그걸_숨기지_않는다(client):
    j = client.get("/v1/cohorts?code=F32").json()
    assert j["n"] == 0
    assert j["data_source"] == "verified_real"
    assert j["approval_rate"] is None
    assert "검증된 사례가 없습니다" in j["headline"]


def test_합성은_별도_엔드포인트다(client):
    j = client.get("/v1/demo/cohorts?code=F32").json()
    assert j["data_source"] == "synthetic"


def test_코드가_없으면_거부한다(client):
    assert client.get("/v1/cohorts?code=").status_code == 422


def test_코호트_저장소가_없으면_0건이_아니라_503이다(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter, "_BASE", tmp_path / "missing-cohort")
    r = TestClient(create_app()).get("/v1/cohorts?code=F32")
    assert r.status_code == 503
    assert r.json() == {
        "ok": False,
        "error_code": "infra_error",
        "message": "서비스 의존 시스템을 사용할 수 없습니다.",
    }


def test_코호트_디렉터리만_있고_이벤트_파일이_없어도_503이다(monkeypatch, tmp_path):
    base = tmp_path / "cohort"
    (base / "verified_real").mkdir(parents=True)
    monkeypatch.setattr(adapter, "_BASE", base)
    r = TestClient(create_app()).get("/v1/cohorts?code=F32")
    assert r.status_code == 503


def test_unverified_는_집계에_넣지_않는다(monkeypatch, tmp_path):
    """★`/v1/observations` 로 들어온 보고는 unverified 다. 통계에 넣으면 안 된다."""
    d = tmp_path / "verified_real"
    d.mkdir(parents=True)
    rows = [
        {"kcd_codes": ["F32"], "outcome": "paid", "verification": "unverified"},
        {"kcd_codes": ["F32"], "outcome": "paid", "verification": "document_backed"},
    ]
    (d / "events.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    monkeypatch.setattr(adapter, "_BASE", tmp_path)

    from app.core.domain.insurance import DataSource, KcdCode

    st = adapter.fetch(
        kcd_code=KcdCode(version_label="", code="F32", name_ko=""),
        product_id="",
        age_band=None,
        data_source=DataSource.VERIFIED_REAL,
    )
    assert st.n == 1  # unverified 1건은 빠졌다


def test_표본이_적으면_비율을_계산하지_않는다(monkeypatch, tmp_path):
    """★40건도 신뢰구간이 ±12%p 다. 적은 표본으로 비율을 단정하지 않는다."""
    d = tmp_path / "synthetic"
    d.mkdir(parents=True)
    rows = [
        {"kcd_codes": ["F32"], "outcome": "paid", "verification": "confirmed"}
    ] * 5
    (d / "events.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    monkeypatch.setattr(adapter, "_BASE", tmp_path)

    c = TestClient(create_app())
    j = c.get("/v1/demo/cohorts?code=F32").json()
    assert j["n"] == 5
    assert j["min_sample_met"] is False
    assert j["approval_rate"] is None
    assert "표본이 적어" in j["headline"]


def test_합성_데이터는_경고를_반드시_붙인다(monkeypatch, tmp_path):
    d = tmp_path / "synthetic"
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text(
        json.dumps({"kcd_codes": ["F32"], "outcome": "paid", "verification": "confirmed"})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(adapter, "_BASE", tmp_path)

    c = TestClient(create_app())
    j = c.get("/v1/demo/cohorts?code=F32").json()
    assert any("합성 데이터" in w for w in j["warnings"])


def test_합성_경고가_두_번_나오지_않는다(client):
    """★실제로 화면을 열어 보고 찾은 결함이다.

    어댑터와 유스케이스가 각각 "합성 데이터입니다" 를 붙여 **두 번** 나왔다.
    경고가 겹쳐 보이면 읽는 쪽이 경고 자체를 흘려보낸다.
    """
    b = client.get("/v1/demo/cohorts", params={"code": "F32"}).json()
    n = sum(1 for w in b["warnings"] if "합성 데이터입니다" in w)
    assert n == 1, f"합성 표시가 {n}번 나옵니다: {b['warnings']}"
