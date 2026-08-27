# -*- coding: utf-8 -*-
"""QLoRA 학습 — 원격 GPU 박스에서 돈다. 05D §4.

원격에서:
    cd /workspace/ft && . .venv/bin/activate
    HF_HOME=/workspace/hf python train_qlora.py

★★이 스크립트는 **학습 데이터가 외부 GPU 로 나간 상태**에서 돈다.
  05D §6 은 그것을 금지하고 있었고, 2026-08-27 에 사용자가 그 제약을 두 번
  확인한 뒤 진행을 지시했다. **결정과 그 사실을 리포트에 남긴다** — 나중에
  「어쩌다 그렇게 됐는지」를 아무도 모르게 되는 것이 더 나쁘다.
  넘어간 것은 **약관 조항 원문 + 합성 질의**이고 진료비 내역서·개인정보는 없다.

★★학습 데이터의 **85%는 사람이 안 본 규칙 라벨(silver)** 이다.
  사람이 확정한 221건(gold)은 **학습에서 빼고 평가에만** 쓴다 —
  거기에 학습하면 좋아졌는지 잴 자가 없어진다(05D §7-1).
  그래서 결과를 말할 때는 「파인튜닝했다」가 아니라
  **「기계 라벨로 파인튜닝했다」**가 맞는 표현이다.

★§12-3 이 지난 시도를 기각한 사유 중 하나가 **peak 15.38GiB** 였다.
  원인은 `prepare_model_for_kbit_training()` 의 fp32 업캐스트다.
  여기서는 그 함수를 쓰지 않고 **필요한 것만 직접 켠다** — 12GB 진입 가능성을 본다.
"""

from __future__ import annotations

import collections
import json
import os
import pathlib
import time

import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model
from transformers import (AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig,
                          DataCollatorForSeq2Seq, Trainer, TrainingArguments)

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
OUT = HERE / "out"
MAX_LEN = 2048          #: 05D §4-2. 지난 실측 최대 1,788 토큰 · 초과 0건
SEED = 42


def load_jsonl(p):
    return [json.loads(l) for l in pathlib.Path(p).read_text(encoding="utf-8").splitlines()
            if l.strip()]


def main():
    #: 조각화로 인한 OOM 을 줄인다 — 위 OOM 메시지가 직접 권한 설정이다.
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    base = json.loads((HERE / "base_model.json").read_text(encoding="utf-8"))["model"]
    print(f"베이스: {base}")
    tok = AutoTokenizer.from_pretrained(base)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    def encode(rec):
        """★답변 토큰에만 loss 를 건다. 프롬프트까지 학습하면 질문을 외운다."""
        msgs = rec["messages"]
        prompt = tok.apply_chat_template(msgs[:-1], tokenize=False, add_generation_prompt=True)
        full = prompt + msgs[-1]["content"] + (tok.eos_token or "")
        pi = tok(prompt, add_special_tokens=False)["input_ids"]
        fi = tok(full, add_special_tokens=False, truncation=True, max_length=MAX_LEN)["input_ids"]
        labels = list(fi)
        for i in range(min(len(pi), len(labels))):
            labels[i] = -100
        return {"input_ids": fi, "attention_mask": [1] * len(fi), "labels": labels}

    train = [encode(r) for r in load_jsonl(DATA / "train.jsonl")]
    valid = [encode(r) for r in load_jsonl(DATA / "valid.jsonl")]
    lens = sorted(len(r["input_ids"]) for r in train)
    print(f"train {len(train)} · valid {len(valid)}")
    print(f"토큰 길이 — 중앙 {lens[len(lens)//2]} · 95% {lens[int(len(lens)*0.95)]} · 최대 {lens[-1]}"
          f" · {MAX_LEN} 초과 잘림 {sum(1 for x in lens if x >= MAX_LEN)}건")

    quant = BitsAndBytesConfig(
        load_in_4bit=True, bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=torch.bfloat16)
    model = AutoModelForCausalLM.from_pretrained(
        base, quantization_config=quant, dtype=torch.bfloat16, device_map={"": 0})

    #: ★`prepare_model_for_kbit_training()` 을 쓰지 않는다 — 그 안의 fp32 업캐스트가
    #:   지난번 peak 을 15.38GiB 로 올렸다(05D §12-1). 필요한 것만 직접 켠다.
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()

    #: ★★**이름이 아니라 모듈 타입으로 고른다.**
    #:   `q_proj` 같은 이름만 넘기면 peft 가 같은 이름의 **모든** 모듈을 잡는데,
    #:   Gemma-4 E4B 의 일부는 `Gemma4ClippableLinear` 라 peft 가 거부하고
    #:   **실행 전체가 죽는다**(05D §12-1 이 기록한 실패. 2026-08-26 재현했다).
    #:
    #:       ValueError: Target module Gemma4ClippableLinear(...) is not supported.
    #:
    #:   그래서 **감쌀 수 있는 타입만** 남기고 **전체 경로**로 넘긴다.
    OK_TYPES = {"Linear", "Linear4bit", "Linear8bitLt"}
    WANT = {"q_proj", "k_proj", "v_proj", "o_proj"}
    targets = [n for n, m in model.named_modules()
               if n.split(".")[-1] in WANT
               and type(m).__name__ in OK_TYPES
               and not any(s in n for s in ("vision", "visual", "audio"))]
    rejected = collections.Counter(
        type(m).__name__ for n, m in model.named_modules()
        if n.split(".")[-1] in WANT and type(m).__name__ not in OK_TYPES)
    print(f"LoRA 대상 {len(targets)}개 · 제외한 타입: {dict(rejected)}")
    if not targets:
        raise SystemExit("감쌀 수 있는 선형층을 못 찾았습니다 — target_modules 규칙을 다시 보세요")
    model = get_peft_model(model, LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05, bias="none",
        task_type="CAUSAL_LM", target_modules=targets))
    model.print_trainable_parameters()

    args = TrainingArguments(
        output_dir=str(OUT), num_train_epochs=2,
        per_device_train_batch_size=1, gradient_accumulation_steps=16,
        #: ★★평가 배치 기본값(8)이 학습 메모리 위에 얹혀 **50 step 에서 OOM** 났다
        #:   (2026-08-26 실측: cross_entropy 가 4.64GiB 요청, 여유 3.47GiB).
        #:   Gemma-4 는 어휘가 262k 라 로짓 텐서가 `vocab × seq × batch` 로 커진다.
        #:   학습은 batch 1 이라 버텼다 — 평가만 줄이면 된다.
        per_device_eval_batch_size=1, eval_accumulation_steps=1,
        #: ★transformers 5.x 에 `warmup_ratio` 가 없다(5.16 실측). `warmup_steps` 로 준다.
        #:   05D §4-2 의 3% 를 step 수로 환산한다 — 2,018건 / (1×16) × 2epoch ≈ 252 step.
        learning_rate=2e-4, lr_scheduler_type="cosine", warmup_steps=8,
        logging_steps=10, eval_strategy="steps", eval_steps=100, save_steps=100,
        save_total_limit=2, bf16=True, gradient_checkpointing=True,
        report_to=[], seed=SEED, dataloader_pin_memory=False,
    )
    trainer = Trainer(
        model=model, args=args,
        train_dataset=Dataset.from_list(train), eval_dataset=Dataset.from_list(valid),
        data_collator=DataCollatorForSeq2Seq(tok, padding=True, label_pad_token_id=-100),
    )

    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    result = trainer.train()
    peak = torch.cuda.max_memory_allocated() / 1024 ** 3
    took = time.time() - t0

    model.save_pretrained(str(OUT / "adapter"))
    tok.save_pretrained(str(OUT / "adapter"))

    #: ★12GB 에 들어가는지가 이번 실행의 핵심 질문이다. 숫자를 남긴다.
    summary = {
        "base": base, "train": len(train), "valid": len(valid),
        "epochs": args.num_train_epochs, "seed": SEED,
        "peak_vram_gib": round(peak, 2),
        "12GB 진입": peak < 11.5,
        "학습_시간_초": round(took, 1),
        "train_loss": result.metrics.get("train_loss"),
        "★라벨": "train/valid 는 전부 규칙 라벨(silver). 사람 확정 gold 는 평가 전용.",
    }
    (OUT / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
