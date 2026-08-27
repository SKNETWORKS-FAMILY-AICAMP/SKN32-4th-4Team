# -*- coding: utf-8 -*-
"""gold 221건에서 **baseline 과 어댑터를 나란히** 잰다 — 05D §7.

원격에서:
    cd /workspace/ft && . .venv/bin/activate && HF_HOME=/workspace/hf python eval_gold.py

★★05D §7-1 — **baseline 을 먼저 고정한다.** 같은 검색·같은 프롬프트에
  어댑터만 붙였다 뗐다 한다. 검색 경로는 건드리지 않는다.

★★여기서 재는 것은 **문장 품질**이지 판정 정확도가 아니다.
  판정(verdict·reason_code·citations)은 입력으로 주어지고 모델은 문장만 만든다.
  그래서 §7-2 의 네 지표 중 이 스크립트가 답할 수 있는 것은 셋뿐이다.

      규칙 위반율      내부용어·근거 없는 단정·모순·중복조항이 나오는가   ← 잰다
      기권 유지        기권 판정에 단정을 얹지 않는가                    ← 잰다
      사람 답과의 거리  사람이 쓴 문장과 얼마나 가까운가                  ← 잰다
      groundedness    주장이 근거로 검증되는가                          ★못 잰다(사람 필요)

  ★못 재는 것을 잰 척하지 않는다. `groundedness` 는 사람 검수로만 나온다.
"""

from __future__ import annotations

import collections
import difflib
import json
import pathlib
import re
import time

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

HERE = pathlib.Path(__file__).resolve().parent
GOLD = HERE / "data" / "gold.jsonl"
ADAPTER = HERE / "out" / "adapter"
OUT = HERE / "out" / "eval_gold.json"
PRED = HERE / "out" / "predictions.jsonl"
MAX_NEW = 220

#: 고객 문장에 나오면 안 되는 내부 표현 — `triage_qa_pilot.py` 와 같은 목록을 쓴다.
JARGON = ["parse_status", "citation_eligible", "occurrence", "content_hash",
          "index_generation", "reason_code", "verdict", "sha256",
          "needs_expert", "needs_documents", "likely_covered", "suspect",
          "근거 인용을 검증하지 못해", "인용을 검증"]
ASSERT_WORDS = ["보장되지 않습니다", "보상되지 않습니다", "판매기간 밖입니다",
                "해당하지 않습니다", "보장됩니다", "지급됩니다", "면책입니다"]
CLAUSE_NO = re.compile(r"제\s?\d+\s?조")
CLAUSE_PATH = re.compile(r"[^\s,()]+/제\s?\d+\s?조")


def violations(text: str, has_citation: bool) -> list[str]:
    out = []
    for j in JARGON:
        if j in text:
            out.append(f"내부용어:{j}")
    if not has_citation:
        #: ★인용이 있으면 「…는 보상하지 않습니다」가 **조항을 옮긴 것**일 수 있다.
        #:   근거 없이 단정하는 것만 위반이다.
        for a in ASSERT_WORDS:
            if a in text:
                out.append(f"단정:{a}")
    if ("특정할 수 없" in text or "특정하지 못" in text) and CLAUSE_NO.search(text):
        out.append("모순:특정불가인데_조항명시")
    for path, n in collections.Counter(CLAUSE_PATH.findall(text)).items():
        if n > 1:
            out.append(f"중복조항:{path}")
    return sorted(set(out))


def generate(model, tok, rows, tag):
    preds = []
    t0 = time.time()
    for i, r in enumerate(rows):
        msgs = r["messages"][:-1]
        prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        enc = tok(prompt, return_tensors="pt", truncation=True, max_length=2048).to(model.device)
        with torch.no_grad():
            gen = model.generate(**enc, max_new_tokens=MAX_NEW, do_sample=False,
                                 pad_token_id=tok.pad_token_id)
        text = tok.decode(gen[0][enc["input_ids"].shape[1]:], skip_special_tokens=True).strip()
        preds.append(text)
        if (i + 1) % 25 == 0:
            print(f"  [{tag}] {i+1}/{len(rows)}  ({time.time()-t0:.0f}s)", flush=True)
    return preds, time.time() - t0


def score(rows, preds, tag):
    viol, dists, empty = collections.Counter(), [], 0
    per_axis = collections.defaultdict(lambda: {"n": 0, "viol": 0, "dist": []})
    for r, p in zip(rows, preds):
        human = r["messages"][-1]["content"]
        #: 인용 유무 — 프롬프트에 「[인용 조항]\n없음」이면 근거가 없다.
        has_cite = "[인용 조항]\n없음" not in r["messages"][1]["content"]
        v = violations(p, has_cite)
        for x in v:
            viol[x.split(":")[0]] += 1
        d = difflib.SequenceMatcher(None, human, p).ratio()
        dists.append(d)
        if not p.strip():
            empty += 1
        a = per_axis[r.get("axis") or "?"]
        a["n"] += 1
        a["viol"] += 1 if v else 0
        a["dist"].append(d)
    n = len(rows)
    return {
        "tag": tag, "n": n,
        "규칙위반 항목수": sum(1 for r, p in zip(rows, preds)
                          if violations(p, "[인용 조항]\n없음" not in r["messages"][1]["content"])),
        "규칙위반 비율": round(sum(1 for r, p in zip(rows, preds)
                             if violations(p, "[인용 조항]\n없음" not in r["messages"][1]["content"])) / n, 4),
        "위반 종류별": dict(viol),
        "사람답과 평균 유사도": round(sum(dists) / n, 4),
        "빈 출력": empty,
        "축별": {k: {"n": v["n"], "위반": v["viol"],
                    "유사도": round(sum(v["dist"]) / len(v["dist"]), 4)}
                for k, v in sorted(per_axis.items())},
    }


def main():
    base = json.loads((HERE / "base_model.json").read_text(encoding="utf-8"))["model"]
    rows = [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"gold {len(rows)}건 · 베이스 {base}")

    tok = AutoTokenizer.from_pretrained(base)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                               bnb_4bit_use_double_quant=True,
                               bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(
        base, quantization_config=quant, dtype=torch.bfloat16, device_map={"": 0})
    model.eval()

    print("=== baseline (어댑터 없음)")
    base_preds, base_s = generate(model, tok, rows, "baseline")

    print("=== 어댑터 적용")
    model = PeftModel.from_pretrained(model, str(ADAPTER))
    model.eval()
    ft_preds, ft_s = generate(model, tok, rows, "adapter")

    res = {
        "base_model": base, "gold": len(rows),
        "baseline": score(rows, base_preds, "baseline"),
        "adapter": score(rows, ft_preds, "adapter"),
        "생성시간_초": {"baseline": round(base_s, 1), "adapter": round(ft_s, 1)},
        "★못 잰 것": "groundedness — 주장이 근거로 검증되는지는 사람 검수로만 나온다(05D §7-2)",
        "★학습 라벨": "train/valid 는 규칙 라벨(silver). gold 는 학습에 넣지 않았다.",
    }
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with PRED.open("w", encoding="utf-8") as f:
        for r, b, a in zip(rows, base_preds, ft_preds):
            f.write(json.dumps({
                "item_id": r["item_id"], "axis": r.get("axis"), "stratum": r.get("stratum"),
                "사람답": r["messages"][-1]["content"],
                "baseline": b, "adapter": a,
            }, ensure_ascii=False) + "\n")
    print()
    print(json.dumps({k: v for k, v in res.items() if k != "★학습 라벨"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
