# -*- coding: utf-8 -*-
"""**서빙 조건**을 잰다 — 어댑터를 붙인 모델이 실제로 돌아가는가. 05D §5·§12-2.

원격에서:
    cd /workspace/ft && . .venv/bin/activate && HF_HOME=/workspace/hf python measure_serving.py

★★학습 peak(16.39GiB)과 **추론 peak 은 다른 값**이다.
  학습은 활성값·optimizer·로짓까지 들고 있지만, 서빙은 가중치와 KV 캐시뿐이다.
  05D §5 가 예산을 잡은 대상은 **학습**이었고, 배포에 필요한 것은 **추론** 쪽이다.
  둘을 같은 숫자로 말하면 「12GB 에 안 된다」가 틀린 결론이 된다.

★★같은 항목을 두 조건으로 **번갈아** 재지 않는다 — 조건마다 모델을 새로 올려
  `reset_peak_memory_stats()` 로 초기화한다. 어댑터를 붙였다 떼면 앞 조건의
  캐시가 남아 peak 이 섞인다.

재는 것
    추론 peak VRAM      12GB 카드에 들어가는가
    건당 지연           §12-2 와 같은 방식(같은 프롬프트·같은 검색 경로)
    생성 토큰 수·속도    지연 증가가 **어댑터 비용**인지 **출력이 길어진 탓**인지 가른다
"""

from __future__ import annotations

import json
import os
import pathlib
import time

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

HERE = pathlib.Path(__file__).resolve().parent
GOLD = HERE / "data" / "gold.jsonl"
ADAPTER = HERE / "out" / "adapter"
OUT = HERE / "out" / "serving_measure.json"
N = 60              #: 지연을 재는 표본. 전량은 이미 품질 평가에서 돌렸다
MAX_NEW = 220


def load_model(base, with_adapter):
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                               bnb_4bit_use_double_quant=True,
                               bnb_4bit_compute_dtype=torch.bfloat16)
    m = AutoModelForCausalLM.from_pretrained(
        base, quantization_config=quant, dtype=torch.bfloat16, device_map={"": 0})
    if with_adapter:
        m = PeftModel.from_pretrained(m, str(ADAPTER))
        #: ★어댑터를 **합치지 않는다**(merge). 4bit 베이스에 merge 하면 양자화가 풀려
        #:   메모리가 뛴다. 붙인 채로 서빙하는 것이 실제 배포 형태다.
    m.eval()
    return m


def measure(base, with_adapter, rows, tok, tag):
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    model = load_model(base, with_adapter)
    weights_gib = torch.cuda.max_memory_allocated() / 1024 ** 3

    lat, ntok = [], []
    for i, r in enumerate(rows):
        prompt = tok.apply_chat_template(r["messages"][:-1], tokenize=False,
                                         add_generation_prompt=True)
        enc = tok(prompt, return_tensors="pt", truncation=True, max_length=2048).to(model.device)
        torch.cuda.synchronize()
        t0 = time.time()
        with torch.no_grad():
            gen = model.generate(**enc, max_new_tokens=MAX_NEW, do_sample=False,
                                 pad_token_id=tok.pad_token_id)
        torch.cuda.synchronize()
        lat.append(time.time() - t0)
        ntok.append(int(gen.shape[1] - enc["input_ids"].shape[1]))
        if (i + 1) % 20 == 0:
            print(f"  [{tag}] {i+1}/{len(rows)}", flush=True)

    peak = torch.cuda.max_memory_allocated() / 1024 ** 3
    del model
    torch.cuda.empty_cache()

    lat_s = sorted(lat)
    return {
        "tag": tag,
        "가중치만_GiB": round(weights_gib, 2),
        "추론_peak_GiB": round(peak, 2),
        "12GB_진입": peak < 11.5,
        "지연_평균_초": round(sum(lat) / len(lat), 3),
        "지연_중앙_초": round(lat_s[len(lat_s) // 2], 3),
        "지연_p95_초": round(lat_s[int(len(lat_s) * 0.95)], 3),
        "생성토큰_평균": round(sum(ntok) / len(ntok), 1),
        "토큰당_초": round(sum(lat) / max(1, sum(ntok)), 4),
        "생성속도_토큰per초": round(sum(ntok) / sum(lat), 2),
        "_per_item": [{"lat": round(a, 4), "tok": b} for a, b in zip(lat, ntok)],
    }


def main():
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    base = json.loads((HERE / "base_model.json").read_text(encoding="utf-8"))["model"]
    rows = [json.loads(l) for l in GOLD.read_text(encoding="utf-8").splitlines() if l.strip()][:N]
    print(f"표본 {len(rows)}건 · 베이스 {base}")

    tok = AutoTokenizer.from_pretrained(base)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    #: ★baseline 을 **먼저** 잰다(05D §7-1). 어댑터를 뗀 상태가 기준이다.
    b = measure(base, False, rows, tok, "baseline")
    print(json.dumps({k: v for k, v in b.items() if not k.startswith("_")},
                     ensure_ascii=False, indent=2), flush=True)
    a = measure(base, True, rows, tok, "adapter")
    print(json.dumps({k: v for k, v in a.items() if not k.startswith("_")},
                     ensure_ascii=False, indent=2), flush=True)

    gpu = torch.cuda.get_device_name(0)
    res = {
        "gpu": gpu, "base_model": base, "표본": len(rows), "max_new_tokens": MAX_NEW,
        "baseline": b, "adapter": a,
        "차이": {
            "지연_평균_초": round(a["지연_평균_초"] - b["지연_평균_초"], 3),
            "지연_증가율": round((a["지연_평균_초"] / b["지연_평균_초"] - 1) * 100, 1),
            "토큰당_증가율": round((a["토큰당_초"] / b["토큰당_초"] - 1) * 100, 1),
            "생성토큰_차이": round(a["생성토큰_평균"] - b["생성토큰_평균"], 1),
            "추론_peak_차이_GiB": round(a["추론_peak_GiB"] - b["추론_peak_GiB"], 2),
        },
        "★주의": "학습 peak(16.39GiB)과 추론 peak 은 다른 값이다. 배포 판단은 추론 쪽을 본다.",
    }
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print()
    print(json.dumps(res["차이"], ensure_ascii=False, indent=2))
    print(f"작성: {OUT}")


if __name__ == "__main__":
    main()
