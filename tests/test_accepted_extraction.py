"""판정에 쓸 추출 버전은 **설정으로 못박는다** — 자동으로 고르지 않는다.

★왜 — "가장 최신"과 "쓸 만함"은 다른 말이다.

  이전 구현은 `가장 큰 sN` 을 자동 선택했다. 그러면:
    · 전처리를 새로 돌리는 **도중에** 판정 결과가 바뀐다
    · 새 버전이 더 낫다는 보장이 없다 — 실측으로 v5 는 본문 24,511쪽을
      되찾았지만 `parse_status=ok` 가 1,240 → 1,108 로 줄었다
    · 같은 질문에 다른 답이 나오는데 아무 기록도 남지 않는다
"""

import json
import pathlib

import pytest

from app.adapters import file_clause_store as store
from app.core.errors import InfraError

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CFG = _ROOT / "config" / "accepted_extraction.json"


def test_설정_파일이_있다():
    assert _CFG.exists(), f"판정에 쓸 추출 버전 설정이 없습니다: {_CFG}"


def test_설정에_근거가_적혀_있다():
    """★버전을 고른 이유와 대가를 반드시 남긴다. 숫자만 있으면 나중에 못 판단한다."""
    cfg = json.loads(_CFG.read_text(encoding="utf-8"))
    assert cfg.get("tag"), "tag 가 비어 있습니다"
    assert cfg.get("reason"), "왜 이 버전을 골랐는지 적어야 합니다"
    assert cfg.get("tradeoff"), "무엇을 잃었는지도 적어야 합니다"
    assert cfg.get("accepted_by"), "누가 정했는지 적어야 합니다"


def test_지정된_버전의_산출물이_실제로_있다():
    tag = store._accepted_tag()
    hits = list((_ROOT / "data" / "structured").glob(f"*/{tag}"))
    assert hits, f"지정된 버전의 산출물이 없습니다: {tag}"


def test_설정이_없으면_실패한다(monkeypatch, tmp_path):
    """★아무거나 골라 쓰느니 멈추는 편이 낫다."""
    #: ★진실원이 `app/core/release.py` 로 옮겨졌다. 저장소는 그걸 읽을 뿐이다.
    from app.core import release

    monkeypatch.setattr(release, "_FILE", tmp_path / "없음.json")
    with pytest.raises(InfraError) as e:
        store._accepted_tag()
    assert "지정되지 않았습니다" in str(e.value)


def test_없는_버전을_지정하면_실패한다(monkeypatch, tmp_path):
    bad = tmp_path / "accepted.json"
    bad.write_text(json.dumps({"clause_tag": "s99_없는추출기"}), encoding="utf-8")
    from app.core import release

    monkeypatch.setattr(release, "_FILE", bad)
    with pytest.raises(InfraError) as e:
        store._accepted_tag()
    assert "산출물이 없습니다" in str(e.value)


def test_자동_선택_함수가_남아_있지_않다():
    """★`가장 큰 sN 자동 선택` 이 되살아나지 않게 막는다."""
    assert not hasattr(store, "_latest_version_dir"), (
        "자동 선택 함수가 되살아났습니다. 판정 버전은 설정으로 못박습니다."
    )
