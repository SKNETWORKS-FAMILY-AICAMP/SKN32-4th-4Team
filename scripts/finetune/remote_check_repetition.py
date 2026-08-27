# -*- coding: utf-8 -*-
"""반복 출력이 **학습 탓인가 디코딩 탓인가** 가른다 — 재학습 없이.

원격에서:
    cd /workspace/ft && . .venv/bin/activate && HF_HOME=/workspace/hf python check_repetition.py

★★왜 이걸 먼저 재나 (2026-08-27)

    어댑터가 반복 출력을 **6% → 12%** 로 늘렸다(D축 25 · C축 2).
    원인 후보가 둘인데 **처방이 다르다.**

        학습 탓    D축 352건 중 183건(52%)이 **답변 토큰 0** 이라 아무것도 못 배웠다
                   → 데이터를 고쳐 **재학습**해야 한다
        디코딩 탓  greedy 로만 뽑아 루프에 빠졌다
                   → `repetition_penalty` 만 켜면 된다. **재학습 불필요**

    둘을 안 가르고 재학습하면, 좋아져도 **무엇이 고쳤는지 모른다.**
    ★그래서 **같은 어댑터**에 디코딩만 바꿔 먼저 잰다.

★반복이 일어난 축(C·D)만 본다 — A·B 는 baseline·어댑터 모두 0건이었다.
"""

from __future__ import annotations

import collections
import difflib
import json
import os
import pathlib
import re
import time

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

HERE = pathlib.Path(__file__).resolve().parent
GOLD = HERE / "data" / "gold.jsonl"
ADAPTER = HERE / "out" / "adapter"
OUT = HERE / "out" / "repetition_check.json"
MAX_NEW = 220

#: 디코딩 설정 — `repetition_penalty` 만 다르다. 나머지는 동일하게 둔다.
CONFIGS = {
    "greedy": dict(do_sample=False),
    "rp1.05": dict(do_sample=False, repetition_penalty=1.05),
    "rp1.15": dict(do_sample=False, repetition_penalty=1.15),
    "norep3": dict(do_sample=False, no_repeat_ngram_size=3),
}


def repetitive(t: str, k: int = 18) -> bool:
    """같은 k글자 조각이 3번 이상 나오면 반복으로 본다(평가와 같은 기준)."""
    s = re.sub(r"\s+", " ", t)
    if len(s) < k * 3:
        return False
    c = collections.Counter(s[i:i + k] for i in range(len(s) - k))
    return max(c.values(), default=0) >= 3


def main():
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    base = json.loads((HERE / "base_model.json").read_text(encoding="utf-8"))["model"]
    rows = [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()]
    #: ★반복이 난 축만 본다. A·B 는 두 조건 다 0건이라 여기서 볼 것이 없다.
    rows = [r for r in rows if r.get("axis") in ("C", "D")]
    print(f"표본 {len(rows)}건 (C·D축) · 베이스 {base}")

    tok = AutoTokenizer.from_pretrained(base)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                               bnb_4bit_use_double_quant=True,
                               bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(
        base, quantization_config=quant, dtype=torch.bfloat16, device_map={"": 0})
    model = PeftModel.from_pretrained(model, str(ADAPTER))
    model.eval()

    res, preds = {}, collections.defaultdict(list)
    for name, kw in CONFIGS.items():
        t0 = time.time()
        rep = collections.Counter()
        sims, ntok = [], []
        for i, r in enumerate(rows):
            prompt = tok.apply_chat_template(r["messages"][:-1], tokenize=False,
                                             add_generation_prompt=True)
            enc = tok(prompt, return_tensors="pt", truncation=True,
                      max_length=2048).to(model.device)
            with torch.no_grad():
                gen = model.generate(**enc, max_new_tokens=MAX_NEW,
                                     pad_token_id=tok.pad_token_id, **kw)
            text = tok.decode(gen[0][enc["input_ids"].shape[1]:],
                              skip_special_tokens=True).strip()
            preds[name].append(text)
            if repetitive(text):
                rep[r["axis"]] += 1
            sims.append(difflib.SequenceMatcher(None, r["messages"][-1]["content"], text).ratio())
            ntok.append(int(gen.shape[1] - enc["input_ids"].shape[1]))
            if (i + 1) % 30 == 0:
                print(f"  [{name}] {i+1}/{len(rows)}", flush=True)
        n = sum(rep.values())
        res[name] = {
            "반복": n, "반복비율": round(n / len(rows), 4), "축별": dict(rep),
            "사람답과 유사도": round(sum(sims) / len(sims), 4),
            "생성토큰_평균": round(sum(ntok) / len(ntok), 1),
            "소요_초": round(time.time() - t0, 1),
        }
        print(f"  → {name}: {json.dumps(res[name], ensure_ascii=False)}", flush=True)

    g = res["greedy"]
    best = min(res, key=lambda k: (res[k]["반복"], -res[k]["사람답과 유사도"]))
    out = {
        "base_model": base, "표본": len(rows), "축": "C·D",
        "설정별": res,
        "가장 나은 설정": best,
        "★해석": (
            "greedy 대비 반복이 크게 줄면 **디코딩 탓**이라 재학습 없이 해결된다. "
            "거의 안 줄면 **학습 탓**이고 데이터를 고쳐 재학습해야 한다. "
            "★유사도가 같이 떨어지면 반복만 막고 내용이 나빠진 것이므로 채택하지 않는다."),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (HERE / "out" / "repetition_preds.jsonl").open("w", encoding="utf-8") as f:
        for i, r in enumerate(rows):
            f.write(json.dumps({
                "item_id": r["item_id"], "axis": r.get("axis"),
                "사람답": r["messages"][-1]["content"],
                **{k: preds[k][i] for k in CONFIGS},
            }, ensure_ascii=False) + "\n")
    print()
    print(json.dumps(out["설정별"], ensure_ascii=False, indent=2))
    print("가장 나은 설정:", best)


if __name__ == "__main__":
    main()
