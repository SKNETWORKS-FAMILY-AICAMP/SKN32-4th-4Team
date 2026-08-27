# -*- coding: utf-8 -*-
"""검수 결과를 **SFT 학습 쌍**으로 바꾼다 — 05D §3-1.

    python -m scripts.finetune.build_sft_dataset
    python -m scripts.finetune.build_sft_dataset --teacher   # 미검수분에 규칙 라벨을 붙인다

★★두 종류를 **섞지 않고 나눈다.**

    gold      사람이 승인·수정한 것          → **평가 전용.** 학습에 넣지 않는다
    silver    규칙이 라벨을 붙인 것           → 학습용. 「사람이 안 본 것」이라고 표시한다

  05D §3-3 은 「모델·기계 출력을 검수 없이 학습에 넣지 않는다」이다.
  ★그래서 silver 를 쓸 때는 **그 사실이 산출물과 리포트에 남아야 한다.**
    이 스크립트는 모든 행에 `label_source` 를 박고, 요약에 비율을 찍는다.
    「파인튜닝했다」가 아니라 「기계 라벨로 파인튜닝했다」가 맞는 표현이다.

★★gold 를 학습에서 빼는 이유

    gold 는 우리가 가진 **유일한 사람 기준선**이다. 여기에 학습하면
    「좋아졌다」를 잴 자가 없어진다(05D §7-1 — baseline 을 먼저 고정한다).
    224건뿐이라 아깝지만, 평가에 쓰는 편이 학습에 넣는 것보다 값이 크다.

출력 (`data/finetune/sft/`)
    train.jsonl · valid.jsonl   silver — 학습
    gold.jsonl                  사람 확정 — 평가 전용
    manifest.json               건수 · 비율 · 분할 SHA · 라벨 출처
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "data" / "finetune" / "sft"
REVIEW_DIR = ROOT / "docs" / "review" / "qa_pilot_pkg"

#: 05D §3-1 — 서비스 스키마를 그대로 쓴다. 판정은 **입력**이고 모델은 문장만 만든다.
SYSTEM = (
    "당신은 실손의료보험 약관을 근거로 고객에게 안내 문장을 씁니다.\n"
    "판정(verdict·reason_code)과 인용 조항은 이미 정해져 있습니다. 바꾸지 마세요.\n"
    "규칙:\n"
    "1. 근거로 확인되지 않은 것을 단정하지 않습니다.\n"
    "2. 내부 용어(parse_status, citation_eligible 등)를 쓰지 않습니다.\n"
    "3. 인용이 없으면 「확인하지 못했다」까지만 말합니다.\n"
    "4. 고객이 다음에 무엇을 해야 할지 알 수 있게 씁니다."
)


def _load(p) -> list[dict]:
    p = pathlib.Path(p)
    return ([json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
            if p.exists() else [])


def _user_prompt(item: dict) -> str:
    """입력 — 질문 · 엔진 판정 · 인용 조항."""
    lines = [f"[질문]\n{item.get('question', '')}"]
    eng = item.get("engine") or {}
    if eng:
        lines.append(
            f"\n[판정]\nverdict={eng.get('verdict')} reason_code={eng.get('reason_code')} "
            f"기권={'예' if eng.get('abstained') else '아니오'}")
    req = item.get("request") or {}
    if req:
        lines.append(f"\n[가입정보]\n보험사={req.get('insurer')} 상품={req.get('product_name')} "
                     f"가입일={req.get('enrolled_on')} 질병기호={','.join(req.get('kcd_codes') or [])}")
    ev = (item.get("evidence") or [None])[0]
    if ev:
        #: ★인용 본문은 **자르지 않는다.** 자르면 모델이 못 본 것을 근거로 쓰게 된다.
        lines.append(f"\n[인용 조항] {ev.get('insurer')} · {ev.get('qualified_no')} "
                     f"· p.{ev.get('page_from')}\n{ev.get('text', '')}")
    else:
        lines.append("\n[인용 조항]\n없음")
    return "\n".join(lines)


def _pair(item: dict, answer: str, label_source: str, decision: str = "") -> dict:
    return {
        "item_id": item["item_id"],
        "axis": item.get("axis"),
        "stratum": item.get("stratum"),
        "label_source": label_source,     #: ★행마다 남긴다. 나중에 섞였는지 셀 수 있게.
        "decision": decision,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": _user_prompt(item)},
            {"role": "assistant", "content": answer},
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="SFT 학습 쌍 생성")
    ap.add_argument("--candidates", default=str(ROOT / "data/finetune/qa_pilot/candidates_2500.jsonl"))
    ap.add_argument("--also", default=str(ROOT / "data/finetune/qa_pilot/candidates_all.jsonl"),
                    help="함께 읽을 후보(파일럿 448건 등)")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args()

    items: dict[str, dict] = {}
    for src in (args.also, args.candidates):
        for r in _load(src):
            items.setdefault(r["item_id"], r)
    print(f"후보 {len(items)}건")

    #: 사람 결정 — 다섯 파트 전부 사람 검수다.
    human: dict[str, dict] = {}
    for f in sorted(REVIEW_DIR.glob("qa_pilot_review_part*.jsonl")):
        for r in _load(f):
            if r.get("decision") and r["item_id"] in items:
                human[r["item_id"]] = r

    from scripts.review.triage_qa_pilot import scan, propose

    gold, silver, skipped = [], [], collections.Counter()
    for iid, it in items.items():
        hv = human.get(iid)
        if hv:
            d = hv["decision"]
            if d == "A":
                gold.append(_pair(it, it["draft_answer"], "human:approved", d))
            elif d == "E" and (hv.get("edited_answer") or "").strip():
                gold.append(_pair(it, hv["edited_answer"].strip(), "human:edited", d))
            elif d in ("R", "S"):
                #: ★반려·보류는 **정답 문장이 없다.** 학습 쌍으로 만들지 않는다.
                #:   기권 학습 신호로 쓰려면 별도 설계가 필요하다(05D §3-2 C축).
                skipped[f"사람 {d} — 정답 문장 없음"] += 1
            else:
                skipped["사람 E 인데 문장 없음"] += 1
            continue

        #: 사람이 안 본 것 — 규칙이 라벨을 붙인다. **silver 로 표시**한다.
        has_cite = bool(it.get("evidence"))
        defects = scan(it.get("draft_answer") or "")
        if has_cite:
            defects = [x for x in defects if x["rule"] not in ("빈참조", "단정")]
        dec, ans, _why = propose(it, defects)
        if dec == "E" and ans:
            silver.append(_pair(it, ans, "rule:rewritten", "E"))
        elif not defects:
            #: 규칙이 결함을 못 찾았으면 **초안 그대로**를 정답으로 본다.
            #: ★이건 「맞다」가 아니라 「규칙이 틀렸다고 말할 근거가 없다」는 뜻이다.
            silver.append(_pair(it, it["draft_answer"], "rule:draft_kept", "A"))
        else:
            skipped["결함은 있는데 고칠 틀이 없음"] += 1

    #: 분할 — silver 만 나눈다. gold 는 통째로 평가용이다.
    from scripts.finetune.split_dataset import load_product_lines, split, checks
    plines = load_product_lines()
    sil_items = [items[p["item_id"]] for p in silver]
    parts, _g, _a = split(sil_items, plines)
    cell = {i["item_id"]: c for c, v in parts.items() for i in v}
    train = [p for p in silver if cell.get(p["item_id"]) in ("train", "valid")]
    valid = [p for p in silver if cell.get(p["item_id"]) == "test"]

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("train", train), ("valid", valid), ("gold", gold)):
        with (out / f"{name}.jsonl").open("w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    gate = checks(parts, sil_items)
    digest = hashlib.sha256(
        "|".join(sorted(p["item_id"] for p in train)).encode("utf-8")).hexdigest()[:16]
    (out / "manifest.json").write_text(json.dumps({
        "만든날": "2026-08-27",
        "건수": {"train": len(train), "valid": len(valid), "gold": len(gold)},
        "라벨 출처": dict(collections.Counter(p["label_source"] for p in train + valid + gold)),
        "★사람이 본 비율": f"{len(gold)}/{len(train) + len(valid) + len(gold)}",
        "gold 는 학습에 넣지 않음": True,
        "분할 검사": [{"name": n, "passed": p, "detail": d} for n, p, d in gate],
        "train sha256(앞16)": digest,
        "제외": dict(skipped),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    print()
    print(f"  train  {len(train):5d}  (silver — 규칙 라벨)")
    print(f"  valid  {len(valid):5d}  (silver)")
    print(f"  gold   {len(gold):5d}  ★사람 확정 — **평가 전용, 학습 제외**")
    print()
    print("  라벨 출처:", dict(collections.Counter(p["label_source"] for p in train + valid + gold)))
    print("  제외:", dict(skipped))
    print()
    for n, ok, d in gate:
        print(f"  {'통과' if ok else '★실패':5s} {n:30s} {d}")
    print()
    print(f"작성: {out}")
    print("★이 데이터로 학습한 결과는 「기계 라벨로 파인튜닝했다」가 맞는 표현이다(05D §3-3).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
