"""측정이 **어떤 데이터 위에서 돌았는지** 기록하는지 고정한다 (계획서 §6.4 · P0).

★**바로 값을 했다**(2026-08-26) — 이 함수를 붙이자마자 로컬 조항 파일이 **1,355개**인데
  내가 원격 두 기계에 올린 스냅샷은 **1,367개**임이 드러났다.
  같은 날 다른 세션이 `s6` 를 격리 반영해 전량 재생성했기 때문이다.
  ★**지문을 안 남겼으면 두 측정이 다른 데이터 위에서 돌았다는 걸 몰랐을 것이다.**
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.delivery

from delivery.bench.profile import data_snapshot  # noqa: E402


def test_문서_수와_지문을_함께_낸다() -> None:
    snap = data_snapshot()

    assert "documents" in snap and isinstance(snap["documents"], int)
    if snap["documents"]:
        assert snap["fingerprint_sha256"], "문서가 있는데 지문이 없다"
        assert len(snap["fingerprint_sha256"]) == 64


def test_없는_태그는_0과_사유를_낸다() -> None:
    """★조용히 빈 값을 내지 않는다 — 「데이터가 없다」와 「안 세었다」는 다르다."""
    snap = data_snapshot(clause_tag="이런태그는_없다")

    assert snap["documents"] == 0
    assert snap["fingerprint_sha256"] is None


def test_지문이_내용해시가_아님을_말한다() -> None:
    """★한계를 숨기면 다음 사람이 「지문이 같으니 내용도 같다」고 잘못 읽는다."""
    snap = data_snapshot()

    assert "내용 해시가 아니" in snap.get("note", "")


def test_프로필에_스냅샷이_실린다() -> None:
    """결과 JSON 에 남아야 사후에 재현·대조할 수 있다."""
    from delivery.bench.profile import describe

    prof = describe(
        memory_probe=lambda: (99.0, None),
        postgres_probe=lambda _dsn: (None, None),
    )

    assert "data_snapshot" in prof
    json.dumps(prof, ensure_ascii=False)  # 직렬화 가능해야 결과 파일에 실린다
