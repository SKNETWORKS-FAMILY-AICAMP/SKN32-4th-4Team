"""QLoRA 파일럿 학습 — 05D §4 설정을 그대로 쓴다.

목적은 **품질 개선이 아니라 실측**이다(05D §4-3·§5).
지금 문서에 비어 있는 값을 채운다.

    05D §5    VRAM 5~7 GiB          "추정" · 확실도 낮음
    05D §4-3  step 당 시간          "못 박을 수 없다"
    05D §4-3  안전한 max_seq_length "2048 이 OOM 없이 도는지는 측정해야 안다"
    registry  memory_peak_mb        null

★학습 데이터는 **완전 합성**이다(05D §6 이 허용한 파이프라인 시험).
  따라서 이 스크립트가 만든 어댑터의 **정확도를 품질 근거로 쓰지 않는다.**

사용:
    # §4-3 파일럿 — 200건 · 50 step 으로 VRAM·step/s 를 먼저 잰다
    python -m scripts.finetune.train_qlora_pilot --limit 200 --max-steps 50 --tag pilot50
    # 본 실행
    python -m scripts.finetune.train_qlora_pilot --tag full
"""

from __future__ import annotations

import argparse
import json
import platform
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

from scripts.finetune.prompt_format import build_target, build_user_message  # noqa: E402

BASE_MODEL = "google/gemma-4-E4B-it"
#: ★05D §2 와 model_registry.yaml 은 `bb3b92e6f031fa438b409f898dd9f14f499a0cb0` 을
#:   "고정 필수" 로 적어 두었는데, **그 revision 은 HF 에 없다**(2026-08-04 확인 —
#:   gemma-4-E4B-it · gemma-3n-E4B-it · gemma-3-4b-it · gemma-2-2b-it 전부 404).
#:   없는 것을 그대로 쓰면 재현이 불가능하므로(05D §8-1), **실재하는 sha 로 바꿔 기록**한다.
BASE_REVISION = "ee0ef6023621cff504d758262d4e04895a5af4a2"

SEED = 42


def _load_rows(path: Path, limit: int | None) -> list[dict]:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[:limit] if limit else rows


def _to_text(tok, row: dict) -> str:
    """05D §3-1 — **학습 형식과 서빙 형식을 같게 한다.**
    다르면 그 차이만큼 성능이 새어 나간다고 설계서가 못박고 있다.
    그래서 프롬프트 조립은 `prompt_format.py` **한 곳**에서만 한다."""
    user = build_user_message(row["prompt"])
    asst = build_target(row["target"])
    msgs = [{"role": "user", "content": user}, {"role": "assistant", "content": asst}]
    try:
        return tok.apply_chat_template(msgs, tokenize=False)
    except Exception:
        return f"<start_of_turn>user\n{user}<end_of_turn>\n<start_of_turn>model\n{asst}<end_of_turn>\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/finetune/pilot_synth")
    ap.add_argument("--limit", type=int, default=None, help="§4-3 파일럿은 200")
    ap.add_argument("--max-steps", type=int, default=-1, help="§4-3 파일럿은 50")
    ap.add_argument("--seq-len", type=int, default=2048, help="05D §4-2")
    ap.add_argument("--tag", default="pilot")
    ap.add_argument("--out", default="artifacts/finetune")
    a = ap.parse_args()

    #: ★`trl.SFTTrainer` 와 `datasets` 를 **쓰지 않는다.**
    #:   x600 의 Windows Application Control 정책이 `pandas._libs.groupby` DLL 을 막아
    #:   `datasets` 임포트가 실패한다("An Application Control policy has blocked this file").
    #:   보안 정책이므로 끄지 않고, pandas 를 타지 않는 경로로 간다.
    #:   §4-2 의 하이퍼파라미터는 아래 수동 루프에서 **그대로** 지킨다.
    import math

    import torch
    from bitsandbytes.optim import PagedAdamW8bit
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    torch.manual_seed(SEED)
    dev = torch.cuda.get_device_properties(0)

    tok = AutoTokenizer.from_pretrained(BASE_MODEL, revision=BASE_REVISION)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    rows = _load_rows(_ROOT / a.data / "train.jsonl", a.limit)
    texts = [_to_text(tok, r) for r in rows]
    lens = [len(tok(t).input_ids) for t in texts]

    #: ★프롬프트 토큰은 label 에서 제외한다(completion-only).
    #:   전체 문장에 손실을 걸면 모델이 **질문·근거를 외우는 데** 용량을 쓴다.
    #:   우리가 배우려는 것은 05D §1-1 의 "형식·태도"뿐이다.
    def _encode(row: dict) -> tuple[list[int], list[int]]:
        head = [{"role": "user", "content": build_user_message(row["prompt"])}]
        #: ★`tokenize=True` 는 transformers 5.x 에서 **BatchEncoding** 을 준다(텐서 아님).
        #:   평가 쪽에서 이걸로 한 번 죽었다 — 문자열로 받아 직접 토크나이즈한다.
        p_ids = tok(tok.apply_chat_template(head, add_generation_prompt=True,
                                            tokenize=False)).input_ids
        full = tok(_to_text(tok, row)).input_ids
        if len(full) <= len(p_ids):          # 안전장치 — 템플릿이 어긋나면 전체에 손실
            p_ids = []
        full = full[: a.seq_len]
        labels = [-100] * min(len(p_ids), len(full)) + full[min(len(p_ids), len(full)):]
        return full, labels

    encoded = [_encode(r) for r in rows]

    #: 05D §4-1 — nf4(fp4 아님) · double quant · compute dtype bfloat16(fp16 아님)
    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_use_double_quant=True,
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    #: 05D §4-1 — attention 4개로 **시작**한다. MLP 확장은 §7 ablation 으로 미룬다.
    ATTN = ("q_proj", "k_proj", "v_proj", "o_proj")

    def _find_targets(m) -> list[str]:
        """★설계서의 `target_modules: [q_proj, k_proj, v_proj, o_proj]` 는
        **이 모델에서 그대로는 안 먹는다**(실측 2026-08-04).

            ValueError: Target module Gemma4ClippableLinear(
              (linear): Linear4bit(in_features=768, out_features=768, bias=False)
            ) is not supported.

        gemma-4-E4B 는 q/k/v/o 를 `Gemma4ClippableLinear` 로 감싸고 있고
        peft 0.20.0 은 `nn.Linear` 계열만 감쌀 수 있다. 그래서 **안쪽 Linear**
        (`...q_proj.linear`)를 찾아 이름을 그대로 넘긴다.
        이름을 손으로 적지 않고 훑는 이유는, 모델 판이 바뀌면 또 달라지기 때문이다.
        """
        import torch.nn as nn
        try:
            from bitsandbytes.nn import Linear4bit
            leaf = (nn.Linear, Linear4bit)
        except Exception:
            leaf = (nn.Linear,)
        #: ★★비전·오디오 타워를 **뺀다**. 처음엔 안 뺐더니 대상이 100개가 되고
        #:   그중 앞자리가 `model.vision_tower.encoder.layers.0.self_attn.q_proj.linear`
        #:   였다(실측 2026-08-04) — gemma-4-E4B 는 멀티모달이라 이미지 인코더에도
        #:   같은 이름의 q/k/v/o 가 있다. 우리 과제는 텍스트 판정 설명이라
        #:   비전 타워를 학습할 이유가 없고, 그 상태로 seq 2048 을 돌리니 **OOM** 났다
        #:   (`Tried to allocate 1.48 GiB … 0 bytes free`).
        SKIP = ("vision_tower", "audio_tower", "vision_model", "audio_model")
        names = [n for n, mod in m.named_modules()
                 if isinstance(mod, leaf)
                 and any(f".{p}" in f".{n}" for p in ATTN)
                 and not any(s in n for s in SKIP)]
        #: 감싸개가 있으면 안쪽만 남긴다 — 바깥 이름을 같이 주면 같은 자리에 두 번 붙는다.
        inner = [n for n in names if n.rsplit(".", 1)[-1] not in ATTN]
        return inner or names

    out_dir = _ROOT / a.out / a.tag

    torch.cuda.reset_peak_memory_stats()
    t_load = time.perf_counter()
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, revision=BASE_REVISION, quantization_config=quant,
        dtype=torch.bfloat16, device_map={"": 0})
    #: ★★`prepare_model_for_kbit_training()` 을 **쓰지 않는다**(실측 2026-08-05).
    #:   이 함수는 비양자화 파라미터를 전부 `float32` 로 올린다
    #:   (`peft/utils/other.py:202  param.data = param.data.to(torch.float32)`).
    #:   E4B 는 per-layer embedding 구조라 그 업캐스트 하나가 **10.50 GiB** 를 요구했다.
    #:
    #:       torch.OutOfMemoryError: Tried to allocate 10.50 GiB
    #:
    #:   05D §5 의 VRAM 예산표(①베이스 ②LoRA ③optimizer ④활성값 ⑤여유)에는
    #:   **이 항목이 없다.** 표대로면 5~7 GiB 인데 실제로는 여기서만 10 GiB 가 더 든다.
    #:   compute dtype 이 이미 bf16 이므로 업캐스트 없이 아래 둘만 해 주면 된다.
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    targets = _find_targets(model)
    lora = LoraConfig(
        r=16, lora_alpha=32, lora_dropout=0.05, bias="none",
        task_type="CAUSAL_LM", target_modules=targets,
    )
    print(f"[LoRA] 대상 모듈 {len(targets)}개, 예: {targets[:3]}")
    model = get_peft_model(model, lora)
    model.config.use_cache = False          # gradient checkpointing 과 함께 못 쓴다
    model.train()
    load_s = time.perf_counter() - t_load
    vram_after_load = torch.cuda.max_memory_allocated() / 1024 ** 3

    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())

    #: ── 05D §4-2 를 그대로 옮긴 수동 루프 ─────────────────────────
    ACCUM, EPOCHS = 16, 2
    opt = PagedAdamW8bit([p for p in model.parameters() if p.requires_grad],
                         lr=2.0e-4, weight_decay=0.0)
    n_opt_steps = a.max_steps if a.max_steps > 0 else max(1, (len(encoded) * EPOCHS) // ACCUM)
    warmup = max(1, int(n_opt_steps * 0.03))

    def lr_at(step: int) -> float:          # warmup 3% → cosine
        if step < warmup:
            return 2.0e-4 * (step + 1) / warmup
        prog = (step - warmup) / max(1, n_opt_steps - warmup)
        return 2.0e-4 * 0.5 * (1.0 + math.cos(math.pi * min(1.0, prog)))

    rng = __import__("random").Random(SEED)
    losses, step_times = [], []
    step = 0
    t0 = time.perf_counter()
    t_step = t0
    done = False
    for _ep in range(EPOCHS):
        if done:
            break
        order = list(range(len(encoded)))
        rng.shuffle(order)
        for k, idx in enumerate(order):
            ids, labels = encoded[idx]
            x = torch.tensor([ids], device=model.device)
            y = torch.tensor([labels], device=model.device)
            loss = model(input_ids=x, labels=y).loss / ACCUM
            loss.backward()
            losses.append(float(loss.detach()) * ACCUM)
            if (k + 1) % ACCUM == 0:
                for g in opt.param_groups:
                    g["lr"] = lr_at(step)
                torch.nn.utils.clip_grad_norm_(
                    [p for p in model.parameters() if p.requires_grad], 0.3)
                opt.step()
                opt.zero_grad(set_to_none=True)
                step += 1
                now = time.perf_counter()
                step_times.append(now - t_step)
                t_step = now
                if a.max_steps > 0 and step >= a.max_steps:
                    done = True
                    break
    train_s = time.perf_counter() - t0

    peak = torch.cuda.max_memory_allocated() / 1024 ** 3
    reserved = torch.cuda.max_memory_reserved() / 1024 ** 3

    out_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(out_dir / "adapter"))
    tok.save_pretrained(str(out_dir / "adapter"))

    steps = step or 1
    report = {
        "★경고": "완전 합성 데이터 파일럿이다. 정확도를 품질 근거로 쓰지 않는다(05D §1-3·§6).",
        "tag": a.tag,
        "base_model": BASE_MODEL,
        "base_revision_사용": BASE_REVISION,
        "base_revision_설계서값": "bb3b92e6f031fa438b409f898dd9f14f499a0cb0 (HF 404 — 실재하지 않음)",
        "gpu": dev.name,
        "gpu_총메모리_GiB": round(dev.total_memory / 1024 ** 3, 2),
        "플랫폼": platform.platform(),
        "학습건수": len(rows),
        "토큰길이": {"평균": round(sum(lens) / len(lens), 1), "최대": max(lens), "최소": min(lens),
                     "seq_len_초과건수": sum(1 for x in lens if x > a.seq_len)},
        "max_seq_length": a.seq_len,
        "steps": steps,
        "모델적재_초": round(load_s, 1),
        "학습_초": round(train_s, 1),
        "step당_초": round(train_s / steps, 3),
        "VRAM_적재직후_GiB": round(vram_after_load, 3),
        "VRAM_peak_allocated_GiB": round(peak, 3),
        "VRAM_peak_reserved_GiB": round(reserved, 3),
        "memory_peak_mb": round(peak * 1024),
        "학습파라미터": trainable,
        "전체파라미터": total,
        "학습파라미터_비율": round(trainable / total * 100, 4),
        "train_loss_평균": round(sum(losses) / len(losses), 4) if losses else None,
        "train_loss_처음20": round(sum(losses[:20]) / max(1, len(losses[:20])), 4),
        "train_loss_마지막20": round(sum(losses[-20:]) / max(1, len(losses[-20:])), 4),
        "step당_초_중앙": round(sorted(step_times)[len(step_times) // 2], 3) if step_times else None,
        "옵티마이저": "PagedAdamW8bit (05D §4-2 paged_adamw_8bit)",
        "★루프": "trl.SFTTrainer 미사용 — x600 Application Control 이 pandas DLL 차단. §4-2 값은 동일",
    }
    (out_dir / "train_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
