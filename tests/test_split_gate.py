# -*- coding: utf-8 -*-
"""05D §3-4 분할 게이트가 **실제로 무는지** 잰다.

★이 게이트는 「①이 0 이 아니면 학습을 시작하지 않는다」다.
  게이트가 안 물면 조항 누수가 있는 채로 학습이 돌고, 성능이 부풀려진 채
  「좋아졌다」고 말하게 된다 — 되돌릴 수 없는 종류의 오류다.

★2026-08-27 실측 — 05D §3-4 문구대로 `(document_sha256, product_line)` 로만 나눴더니
  실데이터에서 **① 조항 내용 교집합이 6개** 나왔다. 문서를 갈라도 같은 조항이
  여러 문서에 실려 내용이 샌다. 연결 성분으로 바꿔서 0 이 됐다.
  그 사실을 시험으로 못박는다.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from scripts.finetune.split_dataset import checks, components, content_hashes, split

ROOT = pathlib.Path(__file__).resolve().parents[1]
CAND = ROOT / "data" / "finetune" / "qa_pilot" / "candidates.jsonl"


def _items():
    if not CAND.exists():
        pytest.skip("후보 파일이 없습니다 — python -m scripts.finetune.build_qa_pilot")
    return [json.loads(l) for l in CAND.read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.fixture(scope="module")
def plines():
    from scripts.finetune.split_dataset import load_product_lines
    return load_product_lines()


def test_연결성분_안에서는_조항이_새지_않는다(plines):
    """성분이 원자 단위인 이유 — 성분끼리는 조항 내용을 공유하지 않는다."""
    items = _items()
    comp = components(items, plines)
    seen: dict[str, int] = {}
    for cid, idxs in comp.items():
        for i in idxs:
            for h in content_hashes(items[i]):
                if h in seen:
                    assert seen[h] == cid, (
                        f"조항 {h[:12]} 이 성분 {seen[h]} 과 {cid} 에 걸쳐 있습니다 — "
                        "성분을 나눠도 내용이 샙니다")
                else:
                    seen[h] = cid


def test_분할이_다섯_검사를_통과한다(plines):
    """★하나라도 실패하면 학습을 시작하지 않는다(05D §3-4)."""
    items = _items()
    out, _groups, _assign = split(items, plines)
    failed = [(n, d) for n, ok, d in checks(out, items) if not ok]
    assert not failed, "분할 검사 실패: " + "; ".join(f"{n} → {d}" for n, d in failed)


def test_같은_seed_면_같은_분할이_나온다(plines):
    """★재현되지 않는 분할은 manifest 에 적을 값이 없다(05D §3-4 ⑤)."""
    items = _items()
    a, _, _ = split(items, plines)
    b, _, _ = split(items, plines)
    for cell in ("train", "valid", "test"):
        assert [x["item_id"] for x in a[cell]] == [x["item_id"] for x in b[cell]]


def test_조항이_겹치면_게이트가_실패한다():
    """★가드가 **무는지** 확인한다 — 일부러 새게 만들어 본다."""
    leaky = {
        "train": [{"item_id": "T1", "axis": "A",
                   "evidence": [{"content_hash": "같은조항", "insurer": "삼성화재"}]}],
        "valid": [],
        "test": [{"item_id": "S1", "axis": "A",
                  "evidence": [{"content_hash": "같은조항", "insurer": "삼성화재"}]}],
    }
    items = leaky["train"] + leaky["test"]
    res = {n: (ok, d) for n, ok, d in checks(leaky, items)}
    ok, detail = res["① 조항 내용 교집합 = 0"]
    assert ok is False, "조항이 양쪽에 있는데 게이트가 통과시켰습니다"
    assert "겹침" in detail


def test_분할_결과가_파일로_남는다():
    """manifest 에 박을 SHA 와 배정이 실제로 기록되는지."""
    out = ROOT / "data" / "finetune" / "qa_pilot" / "split.json"
    if not out.exists():
        pytest.skip("아직 분할을 돌리지 않았습니다 — python -m scripts.finetune.split_dataset")
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["seed"] == 42
    assert set(d["assignment"]) == {"train", "valid", "test"}
    assert sum(d["counts"].values()) == sum(len(v) for v in d["assignment"].values())
    #: ★검사 결과를 **함께** 남긴다 — 통과했다는 기록 없이 분할만 남으면 근거가 없다.
    assert d["checks"] and all("passed" in c for c in d["checks"])
