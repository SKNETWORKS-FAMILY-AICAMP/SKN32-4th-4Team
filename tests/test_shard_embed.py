"""분산 임베딩 — **차원이 어긋난 벡터를 적재하지 않는다.**

★2026-08-26 실측으로 잡은 결함이 배경이다.

    `shard_embed.DIM = 768` 이 박혀 있었는데 현행 승인 프로필은 arctic-ko **1024** 다.
    그 상태로 `load` 를 돌리면 `reshape(-1, 768)` 이 **에러를 안 낸다** —
    총 원소 수가 768의 배수면 그냥 통과하고, **모든 벡터가 어긋난 채 박힌다.**
    검색이 조용히 망가지는데 아무도 모른다.

★★같은 파일 주석에 「분산 적재는 기본 CI 가 안 도는 경로라 시험이 아니라
  운영에서 터질 자리」가 이미 **두 번** 적혀 있었다. 이 파일이 그 세 번째를 막는다.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from scripts.index import shard_embed as se


def _write(tmp: pathlib.Path, n_chunks: int, dim: int, profile: dict):
    """조각 `n_chunks` 개 · 차원 `dim` 인 가짜 `.f32` 와 짝 파일들."""
    import numpy as np

    jsonl = tmp / "s.jsonl"
    jsonl.write_text("".join(
        json.dumps({"h": f"h{i}", "ci": 0, "n": 1, "t": "본문", "body": "본문"},
                   ensure_ascii=False) + "\n" for i in range(n_chunks)), encoding="utf-8")
    vecs = tmp / "s.f32"
    np.zeros((n_chunks, dim), dtype=np.float32).tofile(vecs)
    (tmp / "s.meta.json").write_text(
        json.dumps({"profile": profile}, ensure_ascii=False), encoding="utf-8")
    return jsonl, vecs


def _fp():
    return se._profile_fingerprint()


def test_차원이_어긋난_벡터를_거부한다(tmp_path, monkeypatch):
    """★★핵심. 768 로 만든 파일을 1024 색인에 넣으려 하면 **멈춰야 한다.**

    ★`reshape` 에 기대면 안 된다 — 768×N 원소는 1024 로 안 나뉘지만,
      **조각 수를 바꾸면 나뉘는 조합이 생긴다.** 그래서 바이트로 먼저 검사한다.
    """
    dim = se._dim()
    assert dim == 1024, f"승인 프로필 차원이 바뀌었다: {dim} — 이 시험의 전제를 다시 본다"

    jsonl, vecs = _write(tmp_path, n_chunks=12, dim=768, profile=_fp())
    with pytest.raises(SystemExit) as ei:
        se.main(["load", "--jsonl", str(jsonl), "--vecs", str(vecs)])
    msg = str(ei.value)
    assert "바이트" in msg and "적재하지 않습니다" in msg, msg


def test_조각_수가_안_맞아도_거부한다(tmp_path):
    jsonl, vecs = _write(tmp_path, n_chunks=10, dim=se._dim(), profile=_fp())
    #: 조각을 하나 더 붙여 짝을 깬다
    with open(jsonl, "a", encoding="utf-8") as f:
        f.write(json.dumps({"h": "x", "ci": 0, "n": 1, "t": "t", "body": "b"}) + "\n")
    with pytest.raises(SystemExit, match="적재하지 않습니다"):
        se.main(["load", "--jsonl", str(jsonl), "--vecs", str(vecs)])


def test_프로필_기록이_없으면_거부한다(tmp_path):
    """★무엇으로 만든 벡터인지 모르는 채 적재하면 「어느 모델 것이냐」를 못 답한다."""
    jsonl, vecs = _write(tmp_path, n_chunks=4, dim=se._dim(), profile=_fp())
    (tmp_path / "s.meta.json").unlink()
    with pytest.raises(SystemExit, match="프로필 기록이 없습니다"):
        se.main(["load", "--jsonl", str(jsonl), "--vecs", str(vecs)])


def test_다른_모델로_만든_벡터를_거부한다(tmp_path):
    """모델·revision 이 다른 벡터를 섞으면 검색 순위가 조용히 갈린다."""
    other = {**_fp(), "model": "다른/모델", "revision": "0" * 40}
    jsonl, vecs = _write(tmp_path, n_chunks=4, dim=se._dim(), profile=other)
    with pytest.raises(SystemExit, match="승인 프로필과 다릅니다"):
        se.main(["load", "--jsonl", str(jsonl), "--vecs", str(vecs)])


def test_차원_상수를_박아_두지_않았다():
    """★`DIM = 768` 같은 상수가 되살아나면 이 시험이 잡는다."""
    src = pathlib.Path(se.__file__).read_text(encoding="utf-8")
    import re

    bad = re.findall(r"^DIM\s*=\s*\d+", src, re.M)
    assert not bad, f"차원을 상수로 박았다: {bad} — 승인 프로필에서 파생해야 한다"
