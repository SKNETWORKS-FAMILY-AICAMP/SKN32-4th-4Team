"""`verify()` 의 URL 대조 — **함수 단위가 아니라 실제 흐름**을 통과시킨다.

★이 파일이 지키는 명제

    1. 기본 경로(URL 보정 없이)가 이미 성공하면, URL 보정은 **건드리지 않는다**
       (재시도조차 안 한다).
    2. 기본 경로가 실패하고 URL·sale_start 가 일치할 때만 정정한다.
    3. `sale_start` 형식이 이상하면(8자리 아님) 정정하지 않는다.
    4. 정체성 부기를 URL이 확인해 준 경우도 같은 원칙을 따른다.

★★왜 이 파일이 필요한가 (2026-08-12)

    코덱스 교차검증이 지적했다 — 「⑬(test_cover_match.py) 은 `cover_match()`
    단위 시험이지 `verify()` 회귀 시험이 아니다. 기본 경로 우선 여부·
    `url_anchored` 가 정말 필요할 때만 찍히는지·`sale_start` 형식 미검증은
    이 파일들만으로 검증할 수 없다」. 실제로 그 지적대로 세 개의 버그가
    실측(진짜 매니페스트 데이터)에서만 드러났다 — 단위 시험은 다 통과하는데
    실제 흐름은 틀려 있었다. 이 파일은 그 틈을 **가짜 데이터로도** 메운다.
"""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest

import scripts.confirm.identify_documents as idm

_REAL_ROOT = pathlib.Path(__file__).resolve().parents[1]

_PAGE_TAG = "s4_test"
_CLAUSE_TAG = "s6_test"


def _write_pages(root, insurer, sha12, pages):
    d = root / "data" / "extracted" / insurer / _PAGE_TAG
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sha12}.json").write_text(
        json.dumps({"pages": [{"text": p} for p in pages]}, ensure_ascii=False), encoding="utf-8")
    #: `_parse_status()` 가 이 산출물을 요구한다 — 없으면 `extraction_blocked` 로
    #: 먼저 걸려 이 파일이 시험하려는 `url_anchored` 분기까지 못 간다.
    s = root / "data" / "structured" / insurer / _CLAUSE_TAG
    s.mkdir(parents=True, exist_ok=True)
    (s / f"{sha12}.clauses.json").write_text(
        json.dumps({"parse_status": "ok"}), encoding="utf-8")


@pytest.fixture
def root(tmp_path, monkeypatch):
    #: `_generation_ranges()` 가 이 파일을 읽는다 — 실제 설정을 그대로 복사한다.
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    shutil.copy(_REAL_ROOT / "config" / "generation_profiles.json",
                tmp_path / "config" / "generation_profiles.json")
    monkeypatch.setattr(idm, "_ROOT", tmp_path)
    return tmp_path


def _row(**over):
    base = {
        "insurer": "테스트화재",
        "product_name": "실손의료비보장 안정화 할인 특별약관2507",
        "sha256": "b" * 64,
        "sale_start": "20251013",
        "date_confidence": "exact",
        "generation": None,
        "product_line": "standard",
        "url": "https://x.example/약관_ACU(00)_20251013.pdf",
        "saved_as": "",
    }
    base.update(over)
    return base


def test_기본_경로가_성공하면_url_보정은_건드리지_않는다(root):
    #: 표지가 이름과 정확히 일치 — 보정이 **필요 없다.**
    row = _row(product_name="실손의료비보장 안정화 할인 특별약관2507",
              url="https://x.example/약관_ACU(00)_20250701.pdf",  # 정정할 게 없는 URL
              sale_start="20250701")
    _write_pages(root, "테스트화재", row["sha256"][:12],
                ["실손의료비보장 안정화 할인 특별약관2507\n약관"])
    out = idm.verify(row, page_tag=_PAGE_TAG, clause_tag=_CLAUSE_TAG, siblings=[row])
    assert out["ok"]
    assert "url_anchored" not in out
    assert out["evidence"] != "url_anchored"


def test_기본_경로가_실패하고_url이_정정하면_확정된다(root):
    #: 실측 사례 재현 — 이름은 2507, 표지·URL·sale_start 는 전부 2510.
    row = _row()
    _write_pages(root, "테스트화재", row["sha256"][:12],
                ["실손의료비보장 안정화 할인 특별약관2510\n약관"])
    out = idm.verify(row, page_tag=_PAGE_TAG, clause_tag=_CLAUSE_TAG, siblings=[row])
    assert out["ok"], out["reasons"]
    assert out["evidence"] == "url_anchored"
    assert out["url_anchored"]["정정판본"] == "2510"
    #: ☠판본 충돌로 잘못 찍히면 안 된다 — 정정되면 그 경로는 안 탄다.
    assert "version_conflict" not in out


def test_url도_표지도_안_맞으면_그대로_막힌다(root):
    #: 위험 — URL 이 sale_start 와 안 맞으면(진짜 무관한 문서) 정정하지 않는다.
    row = _row(url="https://x.example/약관_ACU(00)_20260101.pdf")  # sale_start(2510)와 다름
    _write_pages(root, "테스트화재", row["sha256"][:12],
                ["실손의료비보장 안정화 할인 특별약관2601\n약관"])
    out = idm.verify(row, page_tag=_PAGE_TAG, clause_tag=_CLAUSE_TAG, siblings=[row])
    assert not out["ok"]
    assert "url_anchored" not in out


@pytest.mark.parametrize("bad_sale_start", ["2510", "202510", "", "2025101X", "20251013 "])
def test_sale_start_형식이_이상하면_정정하지_않는다(root, bad_sale_start):
    #: 위험 — 코덱스 지적. `ss[2:6]` 은 8자리를 가정하는데, 그때까지 형식을
    #:   확인하지 않았다. 짧거나 이상한 값이 우연히 슬라이스와 맞아떨어질 수 있다.
    row = _row(sale_start=bad_sale_start)
    _write_pages(root, "테스트화재", row["sha256"][:12],
                ["실손의료비보장 안정화 할인 특별약관2510\n약관"])
    out = idm.verify(row, page_tag=_PAGE_TAG, clause_tag=_CLAUSE_TAG, siblings=[row])
    assert "url_anchored" not in out


#: 위험 — 표지 글에 상품명을 **그대로** 넣으면 더 앞선(전체문서 대조) 관문이
#:   먼저 통과해 버려 date_anchored 경로를 실제로는 안 탄다(처음 그렇게 썼다가
#:   「범위 밖인데도 통과」로 시험이 잡았다). 「보험」 삽입으로 살짝 어긋나게 —
#:   cover_match 의 `_INSERTIONS` 는 봐주지만 전체문서 축자 대조는 안 통과한다.
_ANCHOR_NAME = "실손의료비보장 안정화 할인 특약"
_ANCHOR_COVER = "실손의료비보험보장 안정화 할인 특약"


def test_sale_end_범위_안이면_판매기간_닻이_통한다(root):
    #: 참고 — 실측 2026-08-14. 처음엔 sale_start **시작월과 정확히 같을 때만**
    #:   인정했다. sale_end 를 그때까지 안 썼다(매니페스트에 734건 있었는데도).
    #:   범위로 넓히니 실제 4건이 충돌 없이 풀렸다.
    row = _row(product_name=_ANCHOR_NAME, sale_start="20220101", sale_end="20220331",
              url="https://x.example/약관_20220101.pdf")
    _write_pages(root, "테스트화재", row["sha256"][:12], [
        "1. 안정화 할인 적용 대상 가. 신계약 (1) 2022년 1월 1일부터 2022년 3월 31일까지 "
        f"판매하는 실손의료비 보장이 부가된 보험\n{_ANCHOR_COVER}\n약관"])
    #: 형제(kin)를 하나 심어 `lone` 경로가 아니라 `date_anchored` 경로를 타게 한다.
    sibling = _row(product_name=_ANCHOR_NAME, sha256="c" * 64)
    out = idm.verify(row, page_tag=_PAGE_TAG, clause_tag=_CLAUSE_TAG, siblings=[row, sibling])
    assert out["ok"], out["reasons"]
    assert out["evidence"] == "date_anchored"


def test_sale_end_범위_밖이면_판매기간_닻이_안_통한다(root):
    #: 위험 — 문서가 밝힌 기간이 sale_start~sale_end 범위를 벗어나면 막는다.
    row = _row(product_name=_ANCHOR_NAME, sale_start="20220101", sale_end="20220331",
              url="https://x.example/약관_20220101.pdf")
    _write_pages(root, "테스트화재", row["sha256"][:12], [
        "1. 안정화 할인 적용 대상 가. 신계약 (1) 2022년 7월 1일부터 2022년 9월 30일까지 "
        f"판매하는 실손의료비 보장이 부가된 보험\n{_ANCHOR_COVER}\n약관"])
    sibling = _row(product_name=_ANCHOR_NAME, sha256="c" * 64)
    out = idm.verify(row, page_tag=_PAGE_TAG, clause_tag=_CLAUSE_TAG, siblings=[row, sibling])
    assert not out["ok"]
    assert "date_anchored" not in out.get("evidence", "")


def test_정체성_부기도_기본_경로_실패_시에만_url로_보충한다(root):
    row = _row(product_name="무배당 테스트 실손의료비보험(계약전환용)2404(CM)",
              sale_start="20240401",
              url="https://x.example/약관_실손의료비보험(계약전환용)2404(CM)_20240401.pdf")
    #: 표지엔 「계약전환용」이 없다 — 기본 경로는 실패해야 한다.
    _write_pages(root, "테스트화재", row["sha256"][:12],
                ["무배당 테스트 실손의료비보험2404(CM)\n약관"])
    out = idm.verify(row, page_tag=_PAGE_TAG, clause_tag=_CLAUSE_TAG, siblings=[row])
    assert out["ok"], out["reasons"]
    assert out["evidence"] == "url_anchored"
    assert out["url_anchored"]["확인된부기"] == ["계약전환용"]
