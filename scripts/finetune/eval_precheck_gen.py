"""생성 baseline / 어댑터 평가 — 05D §7 프로토콜.

★**검색 경로를 함께 바꾸지 않는다**(§7-1). 같은 test 셋·같은 프롬프트로 두 번 잰다.

측정하는 것과 **측정하지 않는 것**을 구분한다 — 이게 이 스크립트의 핵심이다.

  기계로 검증 가능        schema validity · abstention precision/recall · citation precision · 속도
  ★사람 검수가 필요       groundedness(§7-2) — 각 주장이 근거로 검증되는지는
                          사람이 봐야 한다. 자동으로 매기면 그 수치가 거짓이 된다.
                          → `groundedness: null` 로 두고 "미측정" 이라고 적는다.

사용:
    python -m scripts.finetune.eval_precheck_gen --tag baseline
    python -m scripts.finetune.eval_precheck_gen --tag ft_pilot --adapter artifacts/finetune/full/adapter
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

from scripts.finetune.prompt_format import build_user_message  # noqa: E402

BASE_MODEL = "google/gemma-4-E4B-it"
BASE_REVISION = "ee0ef6023621cff504d758262d4e04895a5af4a2"
SEED = 42

VERDICTS = {"covered", "not_covered", "needs_documents", "needs_expert"}
REQUIRED = {"verdict", "abstained", "reason_code", "message", "citations"}


def _parse(out: str) -> dict | None:
    """모델 출력에서 JSON 을 꺼낸다. ★후처리로 고치지 않는다 —
    설계서 §3-1 은 제약 디코딩을 쓰라고 했고, 지금은 그게 없다.
    파싱이 안 되면 **schema 위반으로 센다.** 조용히 복구하면 위반율이 감춰진다."""
    s = out.strip()
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        v = json.loads(s[i:j + 1])
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _valid(obj: dict | None) -> bool:
    return bool(obj) and REQUIRED <= set(obj) and obj.get("verdict") in VERDICTS \
        and isinstance(obj.get("abstained"), bool) and isinstance(obj.get("citations"), list)


def _bootstrap(pairs: list[tuple[float, float]], n: int = 10_000) -> tuple[float, float]:
    """05D §7-4 — paired bootstrap 10,000회 · 95% 구간.
    구간이 0 을 걸치면 '차이를 확인하지 못했다'이지 '같다'가 아니다."""
    rng = random.Random(SEED)
    m = len(pairs)
    if not m:
        return (0.0, 0.0)
    ds = []
    for _ in range(n):
        s = [pairs[rng.randrange(m)] for _ in range(m)]
        ds.append(sum(b - a for a, b in s) / m)
    ds.sort()
    return (round(ds[int(n * 0.025)], 4), round(ds[int(n * 0.975)], 4))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/finetune/pilot_synth")
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--limit", type=int, default=120, help="시간 예산. 표본이 작으면 작다고 적는다")
    ap.add_argument("--max-new-tokens", type=int, default=192)
    ap.add_argument("--out", default="artifacts/finetune/eval")
    a = ap.parse_args()

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    rows = [json.loads(l) for l in
            (_ROOT / a.data / "test.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()][:a.limit]

    tok = AutoTokenizer.from_pretrained(BASE_MODEL, revision=BASE_REVISION)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                               bnb_4bit_use_double_quant=True,
                               bnb_4bit_compute_dtype=torch.bfloat16)
    torch.cuda.reset_peak_memory_stats()
    model = AutoModelForCausalLM.from_pretrained(
        BASE_MODEL, revision=BASE_REVISION, quantization_config=quant,
        dtype=torch.bfloat16, device_map={"": 0})
    if a.adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, str(_ROOT / a.adapter))
    model.eval()

    per, lat, gen_toks = [], [], []
    for r in rows:
        msgs = [{"role": "user", "content": build_user_message(r["prompt"])}]
        #: ★transformers 5.x 의 `apply_chat_template(tokenize=True)` 은 **텐서가 아니라
        #:   BatchEncoding** 을 돌려준다. 그대로 `generate()` 에 넣으면
        #:   `AttributeError` 로 죽는다(`inputs_tensor.shape` — 실측 2026-08-05).
        #:   문자열로 받아 직접 토크나이즈해 모호함을 없앤다.
        text_in = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
        ids = tok(text_in, return_tensors="pt").input_ids.to(model.device)
        t0 = time.perf_counter()
        with torch.no_grad():
            o = model.generate(ids, max_new_tokens=a.max_new_tokens, do_sample=False,
                               pad_token_id=tok.pad_token_id)
        dt = time.perf_counter() - t0
        new = o[0][ids.shape[-1]:]
        text = tok.decode(new, skip_special_tokens=True)
        obj = _parse(text)
        gold = r["target"]
        allowed = {e["clause_id"] for e in r["prompt"]["evidence"]}
        cites = [c for c in (obj or {}).get("citations", []) if isinstance(c, str)]
        per.append({
            "schema_ok": _valid(obj),
            "gold_abstain": bool(gold["abstained"]),
            "pred_abstain": bool((obj or {}).get("abstained")) if obj else False,
            #: 인용 정확도 — 제공한 근거 밖의 clause_id 를 대면 **지어낸 것**이다.
            "cite_ok": (sum(c in allowed for c in cites) / len(cites)) if cites else None,
            "latency_s": dt,
            "new_tokens": int(new.shape[-1]),
        })
        lat.append(dt)
        gen_toks.append(int(new.shape[-1]))

    n = len(per)
    tp = sum(p["gold_abstain"] and p["pred_abstain"] for p in per)
    fp = sum((not p["gold_abstain"]) and p["pred_abstain"] for p in per)
    fn = sum(p["gold_abstain"] and (not p["pred_abstain"]) for p in per)
    cites_scored = [p["cite_ok"] for p in per if p["cite_ok"] is not None]

    report = {
        "tag": a.tag,
        "adapter": a.adapter,
        "★표본": f"test {n}건 (전체 test 447 중). 표본이 작으므로 점추정을 단정하지 않는다(§7-4)",
        "★데이터": "완전 합성. 정확도는 파이프라인 동작 확인용이며 품질 근거가 아니다",
        "schema_validity": round(sum(p["schema_ok"] for p in per) / n, 4),
        "abstention_precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
        "abstention_recall": round(tp / (tp + fn), 4) if (tp + fn) else None,
        "abstention_지원": {"gold_기권": tp + fn, "pred_기권": tp + fp},
        "citation_precision": round(sum(cites_scored) / len(cites_scored), 4) if cites_scored else None,
        "citation_인용한_응답수": len(cites_scored),
        "groundedness": None,
        "groundedness_비고": "★미측정 — 사람 검수가 필요하다(§7-2). 자동 채점하면 그 수치가 거짓이 된다",
        "지연_평균_초": round(sum(lat) / n, 3),
        "지연_중앙_초": round(sorted(lat)[n // 2], 3),
        "생성토큰_평균": round(sum(gen_toks) / n, 1),
        "생성속도_토큰_초": round(sum(gen_toks) / sum(lat), 2),
        "VRAM_peak_GiB": round(torch.cuda.max_memory_allocated() / 1024 ** 3, 3),
        "per_example": per,
    }
    d = _ROOT / a.out
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{a.tag}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    slim = {k: v for k, v in report.items() if k != "per_example"}
    print(json.dumps(slim, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
