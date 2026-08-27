"""탐침 재측정 병합이 **조건이 바뀐 사실을 지우지 않는가.**

★왜 필요한가 — 같은 실수를 **두 번** 했다.

    ① `dtype`: 입력이 이미 한 번 병합된 파일이면 `dtype` 은 **원 측정**의 값이다.
       그걸 보고 판정하면 재측정의 실제 정밀도를 놓친다. `probes_dtype` 을
       먼저 보도록 고쳤다.
    ② `gpu`: ①을 고쳐 놓고 **바로 아래 줄에서 똑같이 틀렸다**(코덱스 지적).
       `new["gpu"]` 를 쓰면 재측정 GPU 가 원본 GPU 로 되돌아가고,
       `probes_gpu_matches_original` 이 `true` 가 되어
       **조건이 바뀐 사실이 사라진다.**

    한 번은 실수고 두 번은 습관이다. 그래서 시험으로 고정한다.
"""

from __future__ import annotations

import json

import pytest

from scripts.eval import merge_probe_remeasure as M


def _write(d, name, **kw):
    base = {
        "model": name, "dim": 1024, "max_seq_length": 512,
        "mrr@10": 0.5, "recall@10": 0.7, "truncated_ratio": 0.0,
        "proviso_blind_count": 0, "proviso_probes": 60,
    }
    base.update(kw)
    p = d / f"{name}.json"
    p.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    return p


@pytest.fixture
def _dirs(tmp_path, monkeypatch):
    out, src = tmp_path / "out", tmp_path / "src"
    out.mkdir(), src.mkdir()
    monkeypatch.setattr(M, "_OUT", out)
    return out, src


def test_이미_병합된_입력의_gpu_를_원본으로_되돌리지_않는다(_dirs):
    """★코덱스가 잡은 회귀. `probes_gpu` 가 있으면 **그것**이 재측정 GPU다."""
    out, src = _dirs
    _write(out, "m", dtype="4bit", gpu="ORIGINAL", proviso_blind_count=13)
    _write(src, "m", dtype="4bit", gpu="ORIGINAL", probes_gpu="REMEASURED",
           blind_eps=1e-9, proviso_blind_count=0)

    assert M.merge(src) == 0
    got = json.loads((out / "m.json").read_text(encoding="utf-8"))
    assert got["probes_gpu"] == "REMEASURED", "재측정 GPU 가 원본으로 되돌아갔습니다"
    assert got["probes_gpu_matches_original"] is False
    assert got["proviso_blind_count"] == 0


def test_이미_병합된_입력의_dtype_을_원본으로_읽지_않는다(_dirs):
    """`dtype` 은 낡을 수 있다 — 실제 탐침 정밀도는 `probes_dtype` 이다.

    실제로 `ko-sroberta` 박스본이 그랬다(`dtype=float32` · `probes_dtype=float16`).
    """
    out, src = _dirs
    _write(out, "m", dtype="float16", gpu="G", proviso_blind_count=34)
    _write(src, "m", dtype="float32", probes_dtype="float16", gpu="G",
           blind_eps=1e-9, proviso_blind_count=46)

    assert M.merge(src) == 0
    got = json.loads((out / "m.json").read_text(encoding="utf-8"))
    assert got["proviso_blind_count"] == 46
    assert got["probes_dtype"] == "float16"


def test_정밀도가_다르면_합치지_않고_실패로_끝난다(_dirs):
    """★건너뛰고 **0 으로 끝나면** 부른 쪽이 "다 합쳤다"로 읽는다."""
    out, src = _dirs
    _write(out, "m", dtype="float16", gpu="G", proviso_blind_count=34)
    _write(src, "m", dtype="float32", gpu="G", blind_eps=1e-9, proviso_blind_count=99)

    assert M.merge(src) == 1, "건너뛰었는데 성공 코드로 끝났습니다"
    got = json.loads((out / "m.json").read_text(encoding="utf-8"))
    assert got["proviso_blind_count"] == 34, "거부했는데 값이 바뀌었습니다"


def test_옛_공식으로_잰_것은_합치지_않는다(_dirs):
    """`blind_eps` 가 없으면 옛 공식이다(§5-10)."""
    out, src = _dirs
    _write(out, "m", dtype="float16", gpu="G", proviso_blind_count=34)
    _write(src, "m", dtype="float16", gpu="G", proviso_blind_count=99)  # blind_eps 없음

    assert M.merge(src) == 1
    assert json.loads((out / "m.json").read_text(encoding="utf-8"))["proviso_blind_count"] == 34


def test_순위_지표를_덮어쓰지_않는다(_dirs):
    """★`ranks` 가 사라지면 §3-1 짝비교를 다시 못 만든다."""
    out, src = _dirs
    _write(out, "m", dtype="float16", gpu="G", proviso_blind_count=34,
           **{"mrr@10": 0.557})
    p = out / "m.json"
    j = json.loads(p.read_text(encoding="utf-8"))
    j["title"] = {"ranks": [1, 2, 3]}
    p.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
    _write(src, "m", dtype="float16", gpu="G", blind_eps=1e-9, proviso_blind_count=0,
           **{"mrr@10": 0.0})

    assert M.merge(src) == 0
    got = json.loads(p.read_text(encoding="utf-8"))
    assert got["title"]["ranks"] == [1, 2, 3], "순위 지표가 날아갔습니다"
    assert got["mrr@10"] == 0.557, "MRR 이 재측정본으로 덮어써졌습니다"


def test_로컬에_대응_파일이_없으면_새로_만들지_않는다(_dirs):
    """이름이 어긋난 것일 수 있다 — 조용히 새 파일을 만들면 못 알아챈다."""
    out, src = _dirs
    _write(src, "없던모델", dtype="float16", gpu="G", blind_eps=1e-9)

    assert M.merge(src) == 1
    assert not (out / "없던모델.json").exists()
