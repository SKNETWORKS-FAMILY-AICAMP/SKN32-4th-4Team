"""임베딩 모델을 **하나씩** 재고, 다 재면 지운다.

    python -m scripts.eval.bench_embedders --model BAAI/bge-m3
    python -m scripts.eval.bench_embedders --model ... --purge      # 측정 후 캐시 삭제
    python -m scripts.eval.bench_embedders --list                   # 후보 목록
    python -m scripts.eval.bench_embedders --report                 # 지금까지 결과 표

★한 번에 하나만 받는다. 후보 10개를 다 받으면 수십 GB 다.
  `--purge` 를 주면 측정 직후 HuggingFace 캐시에서 지운다.

★측정하는 것

    recall@1/5/10 · MRR@10       조항 제목 → 본문 검색
    proviso_delta                뒷부분 민감도. **1 - cos(앞부분, 앞부분+단서)**
                                 ★0 에 가까우면 **뒷부분이 최종 표현을 바꾸지 못했다.**
                                   "모델이 그 문장을 입력으로 못 받았다"는 **해석**이지
                                   측정값이 아니다 — 여기서 세는 것은 거리뿐이다.
                                   현재 모델(ko-sroberta 128토큰)이 60건 중 **46건** 그랬다.
                                 ★표시 이름은 「벡터무변화」, 저장 키는
                                   `proviso_blind_count` 그대로다(옛 결과 호환).
    truncated_ratio              코퍼스 중 모델 최대 길이를 넘어 잘린 비율
    max_seq_length               ★모델이 **실제로** 쓰는 값. 모델카드 숫자가 아니다.
    sec_per_1k                   처리량

★모델카드를 믿지 않고 실제 값을 찍는다.
  `ko-sroberta` 는 "512토큰"으로 알려져 있었지만 실제 설정은 **128** 이었다.

결과는 `data/eval/embed_bench_results/{모델}.json` 에 쌓인다(누적).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import time

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SET = _ROOT / "data" / "eval" / "embed_bench.json"
#: 「벡터무변화」 문턱. ★이건 **분해능이 아니라 임의 허용오차**다(코덱스 지적).
#:
#:   앞서 쓰던 1e-6 은 fp16 내적의 분해능(2⁻¹¹≈4.9e-4) 아래라 **못 재는 문턱**이었다.
#:   float64 로 계산하면 1e-9 은 잴 수 있지만, "여기 아래는 같다"는 **선택**이지
#:   계산 한계가 아니다.
#:
#:   ★실측(2026-08-03): 이 값 위의 **가장 작은 비영 거리가 1.15e-4** 라
#:     1e-9 과 다섯 자릿수 떨어져 있다 — 이 데이터에서는 어디를 잘라도 같다.
#:     다만 fp16 가중치의 **1 ULP 변화가 코사인 거리 4.65e-10** 로 계산되므로,
#:     "정말 한 비트만 다른" 경우는 무변화로 분류된다. 그건 의도한 동작이다 —
#:     재려는 것은 **뒷부분이 표현을 의미 있게 바꿨는가**이지 비트 동일성이 아니다.
_BLIND_EPS = 1e-9

_OUT = _ROOT / "data" / "eval" / "embed_bench_results"

#: 후보. 순서는 우선순위가 아니라 **작은 것부터**다 — 디스크가 적을 때 먼저 끝난다.
CANDIDATES = [
    # (모델 ID, 대략 크기GB, 질의 접두, 문서 접두, 비고)
    ("ibm-granite/granite-embedding-311m-multilingual-r2", 1.3, "", "", "768d 유지"),
    ("Snowflake/snowflake-arctic-embed-l-v2.0", 2.3, "query: ", "", ""),
    ("dragonkue/snowflake-arctic-embed-l-v2.0-ko", 2.3, "query: ", "", "한국어 추가학습"),
    ("nlpai-lab/KURE-v1", 2.3, "", "", "한국어 추가학습"),
    ("dragonkue/BGE-m3-ko", 2.3, "", "", "한국어 추가학습"),
    ("BAAI/bge-m3", 2.3, "", "", ""),
    ("intfloat/multilingual-e5-large", 2.2, "query: ", "passage: ", "모델팀 선정 후보"),
    ("nlpai-lab/KoE5", 2.2, "query: ", "passage: ", ""),
    #: ★Qwen3 계열은 질의에 instruction 을 붙인다(문서는 안 붙인다).
    #:   앞서 빈 접두어로 재서 MRR 0.455 가 나왔는데, 그건 **모델이 아니라 우리 설정**이다.
    #:   모델카드: `model.encode(queries, prompt_name="query")` · 문서는 접두어 없음.
    ("Qwen/Qwen3-Embedding-0.6B", 2.4, "@qwen", "", "instruction 필요"),
    ("jinaai/jina-embeddings-v5-text-small", 2.7, "", "", "★CC BY-NC 상업불가"),
    #: ★Nemotron 은 접두어가 **필수**다("Add the query:/passage: prefix").
    #:   빈 접두어로 재서 MRR 0.088 이 나왔다 — 모델이 나쁜 게 아니라 우리가 틀렸다.
    ("nvidia/Nemotron-3-Embed-1B-BF16", 4.0, "query: ", "passage: ", "접두어 필수"),
    ("google/embeddinggemma-300m", 1.2, "task: search result | query: ", "title: none | text: ", "게이트"),
    ("Qwen/Qwen3-Embedding-4B", 8.0, "@qwen", "", "instruction 필요"),
    ("Qwen/Qwen3-Embedding-8B", 16.0, "@qwen", "", "instruction 필요 · 4bit 필요"),
    #: ★Nemotron 계열은 접두어가 필수다. 1B 에서 이미 확인했다(0.088 → 0.548).
    #:   8B 를 접두어 없이 재서 MRR 0.260 이 나왔는데, 같은 실수를 반복한 것이다.
    ("nvidia/llama-embed-nemotron-8b", 16.0, "query: ", "passage: ", "접두어 필수 · 4bit"),
    ("sionic-ai/comsat-embed-ko-8b-preview", 16.0, "", "", "★CC BY-NC · 4bit"),
    ("jhgan/ko-sroberta-multitask", 0.5, "", "", "★현재 모델. 회귀 기준선"),
    #: ── 파인튜닝 10선 중 미측정분 ──────────────────────────────────
    #: ★접두어를 **확신하지 못하는 것은 그렇게 적는다.** Nemotron 을 빈 접두어로
    #:   재서 MRR 0.088 을 만든 실수를 되풀이하지 않으려면, 점수가 유난히 낮을 때
    #:   모델을 탓하기 전에 접두어를 먼저 의심해야 한다.
    ("upskyy/bge-m3-korean", 2.3, "", "", "KorNLI·KorSTS 튜닝(검색 아님)"),
    ("upskyy/gte-base-korean", 1.3, "", "", "768d 유지 · NLI/STS 튜닝"),
    ("jjp97/laal-embedding-v0", 2.2, "query: ", "passage: ", "법률 검색 · 접두어 미확인"),
    ("upskyy/kf-deberta-multitask", 0.9, "", "", "금융 도메인 · 배포설정 128토큰"),
    #: Gemini 계열의 **오픈 웨이트**. `gemini-embedding-001` 은 API 전용이라
    #: 약관 원문을 구글로 보내야 해서 우리 원칙상 못 쓴다.
    ("google/embeddinggemma-300m", 1.2, "task: search result | query: ",
     "title: none | text: ", "Gemma3 기반 · 게이트"),
    ("jinaai/jina-embeddings-v5-text-small", 2.7, "", "", "★CC BY-NC 상업불가(참고용)"),
]

#: ★측정하지 **않기로 한 것**과 그 이유. 조용히 빼면 "다 재봤다"가 거짓이 된다.
#: ★"VRAM 초과라 못 잰다"고 8B·12B 를 통째로 뺐던 것은 **성급했다.**
#:   4bit 로 재면 8B 는 약 5GB, 12B 는 약 7GB 로 12GB 안에 들어간다.
#:   못 재는 것과 안 재는 것은 다르다. 이제 `--quant 4bit` 로 잰다.
NOT_MEASURED = {
    "gemini-embedding-001": (
        "★API 전용이라 오픈 웨이트가 없다. 재려면 약관 원문 2,000건을 "
        "구글 서버로 보내야 하는데, 약관은 저작물이고 우리 원칙은 외부 API 금지다. "
        #: ★"대신했다"고 쓰면 안 된다 — 대체하려던 embeddinggemma-300m 도
        #:   게이트 모델이라 못 쟀다. 구글 계열은 **한 개도 측정하지 못했다.**
        "같은 계열 오픈 모델 google/embeddinggemma-300m 로 대신하려 했으나 "
        "그것도 게이트라 못 쟀다 — 구글 계열은 한 개도 측정하지 못했다."
    ),
}


def _slug(model_id: str) -> str:
    return model_id.replace("/", "__")


def _load_set() -> dict:
    if not _SET.exists():
        raise SystemExit(
            "평가셋이 없습니다. 먼저 만드세요: python -m scripts.eval.build_retrieval_set"
        )
    return json.loads(_SET.read_text(encoding="utf-8"))


def _purge(model_id: str) -> str:
    """HuggingFace 캐시에서 이 모델을 지운다."""
    from huggingface_hub import constants

    d = pathlib.Path(constants.HF_HUB_CACHE) / f"models--{model_id.replace('/', '--')}"
    if not d.exists():
        return "캐시 없음"
    size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
    shutil.rmtree(d, ignore_errors=True)
    return f"{size/1e9:.1f}GB 삭제"


def run(model_id: str, *, q_prefix: str, d_prefix: str, batch: int, device: str,
        no_fp16: bool = False, quant: str = "", max_seq: int = 0,
        probes_only: bool = False) -> dict:
    """한 모델을 잰다. `probes_only` 면 **탐침만** 다시 잰다.

    ★탐침 재측정 전용 경로가 필요한 이유 — 「벡터무변화」 계산이 틀려(§5-10)
      21건을 다시 재야 하는데, 코퍼스 2,000건까지 다시 넣을 이유가 없다.
      코퍼스 2,000건 대 탐침 120문장이라 **인코딩 대상이 약 17배 적다.**
      (실행 시간은 재지 않았다 — 내려받는 비용이 지배적이다.)
      결과는 기존 JSON 에 **덮어쓰지 않고 합친다** — 순위 지표는 유효하므로.
    """
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer

    data = _load_set()
    corpus = data["corpus"]
    queries = data["queries"]
    probes = data["proviso_probes"]

    t0 = time.time()
    kw = {"trust_remote_code": True}
    if device:
        kw["device"] = device
    #: ★가중치를 **fp16 으로 바로 GPU 에** 올린다.
    #:
    #:   fp32 로 받으면 CPU 에 전체 사본이 한 벌 생겼다가 GPU 로 복사된다.
    #:   측정 기계는 RAM 15.6GB 중 **여유 2.5GB** 인 작업용 PC 라
    #:   2.2GB 모델에서 `memory allocation of 57462376 bytes failed` 로 죽었다.
    #:   fp16 은 그 절반이고, 임베딩 추론에서 순위가 뒤집힐 만한 차이는 아니다.
    #:   ★그래도 **결과에 dtype 을 적는다** — fp32 와 섞어 비교하면 안 되기 때문이다.
    dtype = "float32"
    if device.startswith("cuda") and not no_fp16:
        import torch as _t

        kw["model_kwargs"] = {"torch_dtype": _t.float16}
        dtype = "float16"

    #: ★4bit/8bit 양자화. **8B·12B 를 12GB GPU 에서 재기 위한 것**이다.
    #:
    #:   앞서 "VRAM 초과라 못 잰다"고 후보에서 빼려 했는데 성급했다 —
    #:   8B fp16 은 16GB 지만 4bit 면 약 5GB, 12B 도 약 7GB 로 들어간다.
    #:   **못 재는 것과 안 재는 것은 다르다.**
    #:
    #:   ★단 숫자가 달라진다. 4bit 결과를 fp16 결과와 **같은 표에서 1위 다툼**
    #:     시키면 안 된다. 그래서 `dtype` 을 결과에 박고 보고표에 표시한다.
    if quant:
        from transformers import BitsAndBytesConfig
        import torch as _t

        if quant == "4bit":
            cfg = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=_t.float16,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )
        elif quant == "8bit":
            cfg = BitsAndBytesConfig(load_in_8bit=True)
        else:
            raise SystemExit(f"모르는 양자화: {quant} (4bit|8bit)")
        kw.setdefault("model_kwargs", {})
        kw["model_kwargs"] = {**kw["model_kwargs"], "quantization_config": cfg}
        kw["model_kwargs"].pop("torch_dtype", None)
        dtype = quant
    model = SentenceTransformer(model_id, **kw)

    #: ★`max_seq_length` 는 **모델의 한계가 아니라 배포 설정**이다(코덱스 지적).
    #:   실측: `jhgan/ko-sroberta-multitask` 는 ST 설정 128 인데
    #:   백본 `max_position_embeddings=514` · 토크나이저 512 다.
    #:   즉 **모델을 안 바꾸고도 올릴 수 있다.** 그 효과를 따로 잰다.
    if max_seq:
        #: ★한계는 **백본 설정**에서 읽는다.
        #:   `model.tokenizer.model_max_length` 를 봤더니 ST 가 배포 설정(128)으로
        #:   덮어써 놓아서, 백본이 512 를 지원하는데도 가드가 막았다.
        #:   RoBERTa 계열은 `max_position_embeddings` 에 특수토큰 여유 2가 붙어 있다.
        try:
            cfg = model[0].auto_model.config
            cap = int(getattr(cfg, "max_position_embeddings", 0)) or 10**9
            if getattr(cfg, "model_type", "") in ("roberta", "xlm-roberta"):
                cap -= 2
        except Exception:  # noqa: BLE001
            cap = 10**9
        if max_seq > cap:
            raise SystemExit(f"백본 한계 {cap} 를 넘겨 요청했습니다: {max_seq}")
        model.max_seq_length = max_seq
        model.tokenizer.model_max_length = max_seq
    load_s = time.time() - t0

    #: ★모델카드가 아니라 **실제 설정값**을 찍는다.
    max_len = int(getattr(model, "max_seq_length", 0) or 0)
    dim = int(model.get_sentence_embedding_dimension())
    dev = str(model.device)

    tok = model.tokenizer
    #: ★실제로 인코더에 들어가는 것은 **접두어가 붙은 문자열**이다.
    #:   본문만 세면 접두어가 있는 모델은 잘림이 **과소계산**된다(코덱스 지적).
    body_tokens = [len(tok.encode(d_prefix + c["body"])) for c in corpus]
    truncated = sum(1 for n in body_tokens if max_len and n > max_len)

    #: ★`@qwen` 은 접두어가 아니라 **prompt_name 을 쓰라는 표시**다.
    #:   Qwen3 의 instruction 템플릿은 모델 설정에 들어 있으므로
    #:   문자열을 우리가 지어내지 않고 라이브러리가 붙이게 한다.
    use_prompt = q_prefix == "@qwen"
    qp = "" if use_prompt else q_prefix

    def enc(texts: list[str], *, as_query: bool = False) -> "np.ndarray":
        kw2 = {}
        if as_query and use_prompt and "query" in (getattr(model, "prompts", {}) or {}):
            kw2["prompt_name"] = "query"
        return model.encode(
            texts, batch_size=batch, normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=False, **kw2,
        )

    #: ★첫 인코딩에는 CUDA 초기화 비용이 섞인다. 워밍업 후에 잰다(코덱스 지적).
    enc([d_prefix + c["body"] for c in corpus[:32]])
    if probes_only:
        #: ★코퍼스 2,000건을 넣지 않는다 — 탐침 재측정에는 필요 없다.
        doc_vecs = None
        enc_s = 0.0
    else:
        t1 = time.time()
        doc_vecs = enc([d_prefix + c["body"] for c in corpus])
        enc_s = time.time() - t1

    ids = [c["id"] for c in corpus]
    pos = {cid: i for i, cid in enumerate(ids)}

    def score(qs: list[dict], *, keep_ranks: bool = False) -> dict:
        """질의 묶음 하나를 채점한다.

        ★`keep_ranks` 면 **질의별 등수**도 남긴다.
          부분집합(진짜 면책만 등)을 나중에 다시 채점할 때 재측정이 필요 없다 —
          코덱스 지적으로 면책 부분집합을 만들었는데, 등수를 안 남겨 뒀으면
          17개 모델을 전부 다시 돌려야 했다.
        """
        if not qs:
            return {"n": 0, "recall@1": None, "recall@5": None,
                    "recall@10": None, "mrr@10": None}
        v = enc([qp + q["query"] for q in qs], as_query=True)
        order = np.argsort(-(v @ doc_vecs.T), axis=1)
        r1 = r5 = r10 = 0
        mrr = 0.0
        ranks: list[int] = []
        for i, q in enumerate(qs):
            #: ★정답이 여럿일 수 있다(코덱스 지적). 그중 **가장 높은 등수**를 쓴다.
            #:   중복률 65% 코퍼스에서 하나만 정답으로 두면
            #:   맞힌 것을 틀렸다고 세게 된다.
            golds = [pos[g] for g in (q.get("gold_ids") or [q["gold_id"]]) if g in pos]
            if not golds:
                ranks.append(0)
                continue
            rank = min(int(np.where(order[i] == g)[0][0]) + 1 for g in golds)
            ranks.append(rank)
            r1 += rank <= 1
            r5 += rank <= 5
            r10 += rank <= 10
            if rank <= 10:
                mrr += 1.0 / rank
        m = len(qs)
        out = {"n": m, "recall@1": round(r1 / m, 4), "recall@5": round(r5 / m, 4),
               "recall@10": round(r10 / m, 4), "mrr@10": round(mrr / m, 4)}
        if keep_ranks:
            out["ranks"] = ranks
        return out

    title = score([] if probes_only else queries, keep_ranks=True)
    #: ★면책 조항 검색 — 우리 서비스의 급소.
    #:   조항 **뒤쪽**에 있는 단서를 질의로 쓴다. 앞부분만 임베딩하는 모델은
    #:   그 문장이 임베딩에 안 들어가므로 **불리하다**.
    #:   ★"못 찾는다"고 단정하지 않는다 — 앞부분 단서만으로 맞히는 경우가 있다.
    #: ★이름을 바로잡는다 — 「면책」이 아니라 **「뒷부분」** 검색이다.
    #:   60개 중 실제 부정·면책 표현이 있는 것은 16개뿐이었다(코덱스 지적).
    tail_qs = data.get("proviso_queries") or []
    proviso = score([] if probes_only else tail_qs, keep_ranks=True)
    #: 진짜 면책만 모은 부분집합. ★표본 16개 — 작다.
    excl_qs = [q for q in tail_qs if q.get("is_exclusion")]
    exclusion = score([] if probes_only else excl_qs, keep_ranks=True)
    r1, r5, r10 = title["recall@1"], title["recall@5"], title["recall@10"]
    mrr_v = title["mrr@10"]
    n = title["n"]

    #: ★뒷부분 민감도. 0 이면 **뒷부분이 최종 표현을 바꾸지 못했다**는 뜻이다.
    #:   "모델이 그 문장을 입력으로 못 받았다"는 **해석**이지 측정값이 아니다 —
    #:   여기서는 벡터가 안 움직였다는 사실만 센다.
    #: ★★**코사인을 float64 로, 노름까지 직접 나눠 계산한다.**
    #:
    #:   앞서는 `1 - np.sum(h*w)` 로 잰 뒤 `< 1e-6` 으로 셌는데, 두 군데가 틀렸다.
    #:
    #:   ① 모델이 돌려준 벡터가 **단위 노름이 아니다.** fp16 정규화 뒤 노름이
    #:      fp16 0.999579~1.000488, 4bit 0.998076~1.003902 다(재측정 실측).
    #:      그래서 내적이 1을 넘고 delta 가
    #:      **음수**가 된다 — 실측 최소 −0.00498. 각도가 실제로 벌어졌는데도
    #:      `< 1e-6` 에 걸려 「안 움직였다」로 세어졌다.
    #:   ② fp16 으로 더하면 1 근처 분해능이 **2⁻¹¹ ≈ 4.9e-4** 다.
    #:      `1e-6` 이라는 문턱은 이 분해능 아래라 **잴 수 없는 값**이었다.
    #:      실제로 저장된 delta 가 전부 2⁻¹¹ 의 배수였다(코덱스 지적 2026-08-03).
    #:
    #:   숫자를 못 잰 것보다 **못 재는 문턱을 적어 둔 것**이 더 나빴다.
    h = np.asarray(enc([p["head"] for p in probes]), dtype=np.float64)
    w = np.asarray(enc([p["with_proviso"] for p in probes]), dtype=np.float64)
    hn = np.linalg.norm(h, axis=1)
    wn = np.linalg.norm(w, axis=1)
    cos = np.sum(h * w, axis=1) / (hn * wn)
    deltas = 1.0 - np.clip(cos, -1.0, 1.0)
    #: ★문턱을 **실제 분해능 위**에 둔다. float64 로 재므로 1e-9 는 잴 수 있다.
    blind = int(np.sum(deltas < _BLIND_EPS))

    res = {
        "model": model_id + (f" (max_seq={max_seq})" if max_seq else ""),
        "device": dev,
        "cuda": torch.cuda.is_available(),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
        "dim": dim,
        "dtype": dtype,
        "max_seq_length": max_len,
        "load_sec": round(load_s, 1),
        "corpus": len(corpus),
        "queries": n,
        "truncated_ratio": round(truncated / len(corpus), 4),
        "body_tokens_p50": int(np.median(body_tokens)),
        "body_tokens_p90": int(np.percentile(body_tokens, 90)),
        "recall@1": r1,
        "recall@5": r5,
        "recall@10": r10,
        "mrr@10": mrr_v,
        #: ★면책 조항 검색 성적. 제목→본문 성적과 **따로** 본다.
        #: ★제목 과제의 등수도 남긴다. 안 남겨서 짝 비교(`--compare title`)가
        #:   "기준 모델이 결과에 없습니다"로 막혔다.
        "title": title,
        #: ★「뒷부분 검색」. 앞서 「면책」이라 불렀는데 실제로는 접속 문장이 많았다.
        "tail": proviso,
        "proviso": proviso,   # 옛 이름 — 기존 결과 파일과 호환
        #: 진짜 면책만(부정 표현 포함). 표본이 작다.
        "exclusion": exclusion,
        "proviso_delta_mean": round(float(np.mean(deltas)), 6),
        "proviso_delta_min": round(float(np.min(deltas)), 6),
        #: ★필드명을 **바꾸지 않는다.** 이미 저장된 측정 21건이 이 이름으로 쓰였고,
        #:   이름만 바꾸면 옛 결과와 새 결과를 한 표에서 못 읽는다.
        #:   표시 이름(「벡터무변화」)과 저장 이름이 다르다는 사실은 브리핑 §0-3 에 적었다.
        #: ★벡터 노름. 1 에서 벗어나면 코사인 계산이 어긋난다(위 주석 ①).
        "probe_norm_min": round(float(min(hn.min(), wn.min())), 6),
        "probe_norm_max": round(float(max(hn.max(), wn.max())), 6),
        "blind_eps": _BLIND_EPS,
        "proviso_blind_count": blind,
        "proviso_probes": len(probes),
        "sec_per_1k": round(enc_s / len(corpus) * 1000, 1),
        "q_prefix": q_prefix,
        "d_prefix": d_prefix,
    }
    _OUT.mkdir(parents=True, exist_ok=True)
    tag = _slug(model_id) + (f"@{max_seq}" if max_seq else "") + (f"@{quant}" if quant else "")
    dst = _OUT / f"{tag}.json"
    if probes_only and dst.exists():
        #: ★**덮어쓰지 않는다.** 순위 지표(MRR·R@k·ranks)는 유효하므로 그대로 두고,
        #:   탐침 관련 값만 갈아 끼운다. 통째로 쓰면 멀쩡한 측정을 날린다.
        old = json.loads(dst.read_text(encoding="utf-8"))
        #: ★★**조건이 다르면 합치지 않는다 — 거부한다.**
        #:
        #:   앞서는 어긋난 사실을 `probes_dtype_matches_original` 에 **적어만 두고
        #:   덮어썼다.** 기록은 남지만 표에 든 값은 이미 섞인 뒤다 —
        #:   읽는 사람은 그 필드를 보지 않는다(코덱스 지적 2026-08-03).
        #:   별도 병합 스크립트(`merge_probe_remeasure.py`)는 거부하는데
        #:   **여기만 안 막혀 있었다.** 막는 곳이 하나 빠지면 막은 게 아니다.
        if old.get("dtype") and dtype != old["dtype"]:
            raise SystemExit(
                f"""★정밀도가 다릅니다: 저장본 {old['dtype']} · 이번 측정 {dtype}.
  탐침만 갈아 끼우면 표의 값이 무엇으로 잰 것인지 알 수 없게 됩니다.
  같은 조건으로 다시 재세요 — GPU fp16 이면 `--device cuda` 를,
  4bit 면 `--quant 4bit` 도 함께 주세요."""
            )
        keys = ("proviso_delta_mean", "proviso_delta_min", "proviso_blind_count",
                "proviso_probes", "probe_norm_min", "probe_norm_max", "blind_eps")
        old.update({k: res[k] for k in keys if k in res})
        #: 재측정 사실을 남긴다 — 언제 무엇이 바뀌었는지 모르면 추적이 안 된다.
        #: ★★**어떤 조건에서 다시 쟀는지 남긴다.**
        #:   시험 삼아 CPU(float32)로 다시 쟀더니 34→47 로 늘었는데,
        #:   계산을 고쳐서인지 **정밀도가 달라서인지 가릴 수 없었다**
        #:   (원래 측정은 GPU fp16). 조건을 안 남기면 다음 사람도 못 가린다.
        old["probes_remeasured"] = True
        #: ★**GPU 이름을 남긴다.** `cuda:0` 만으로는 어느 기계인지 모른다 —
        #:   4bit 원 측정은 RunPod(RTX 2000 Ada), 재측정은 랩(RTX 4070 SUPER)이라
        #:   조건이 달라졌는데 `probes_device` 로는 안 드러났다(코덱스 지적).
        old["probes_gpu"] = res.get("gpu", "")
        old["probes_gpu_matches_original"] = (res.get("gpu") == old.get("gpu"))
        old["probes_device"] = dev
        old["probes_dtype"] = dtype
        old["probes_dtype_matches_original"] = (dtype == old.get("dtype"))
        res = old
    (dst).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return res


def compare(baseline: str, task: str = "title", n_boot: int = 2000,
            dtype: str = "float16") -> None:
    """기준 모델 대비 **같은 질의에서의 차이**와 신뢰구간.

    ★모델별 독립 점수만 보면 "0.557 이 0.549 보다 높다"를 우열로 읽게 된다.
      실제로는 질의 145개에서 **한두 문항 차이**일 수 있다(코덱스 지적).
      같은 질의를 짝지어 차이를 재고, 부트스트랩으로 구간을 준다.

    ★구간이 0 을 걸치면 **"차이를 확인하지 못했다"** 이지
      "같다"가 아니다. 표본이 작아 못 가린 것일 수 있다.
    """
    import numpy as np

    key = {"title": "title", "tail": "tail", "exclusion": "exclusion"}[task]
    rows = {}
    for f in sorted(_OUT.glob("*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        #: ★정밀도가 다른 것을 **같은 구간표에 두지 않는다**(코덱스 지적).
        #:   4bit 와 fp16 을 섞으면 "이 차이가 모델 때문인지 정밀도 때문인지"를
        #:   읽는 사람이 가릴 수 없다.
        if dtype and r.get("dtype") != dtype:
            continue
        rk = (r.get(key) or {}).get("ranks")
        if rk:
            rows[r["model"]] = np.array(rk, dtype=float)
    if baseline not in rows:
        print(f"기준 모델이 결과에 없습니다: {baseline}")
        print("있는 것:")
        for k in sorted(rows):
            print("  ", k)
        return

    def rr(ranks):
        return np.where((ranks >= 1) & (ranks <= 10), 1.0 / np.maximum(ranks, 1), 0.0)

    base = rr(rows[baseline])
    rng = np.random.default_rng(20260803)
    idx = rng.integers(0, len(base), size=(n_boot, len(base)))

    n_cmp = sum(1 for m, rk in rows.items() if m != baseline and len(rk) == len(base))
    print(f"[{task}] 기준: {baseline}  ({dtype or '전체'} · 질의 {len(base)}개 · "
          f"비교 {n_cmp}개 · 부트스트랩 {n_boot}회)")
    print(f"{'모델':50} {'ΔMRR':>8} {'95% 구간':>18}  판정")
    print("-" * 92)
    out = []
    for m, ranks in rows.items():
        if m == baseline or len(ranks) != len(base):
            continue
        d = rr(ranks) - base
        lo, hi = np.percentile(d[idx].mean(axis=1), [2.5, 97.5])
        out.append((d.mean(), lo, hi, m))
    for mean, lo, hi, m in sorted(out, reverse=True):
        verdict = "★차이 확인" if (lo > 0 or hi < 0) else "구간이 0 을 걸침"
        print(f"{m:50} {mean:>+8.3f} [{lo:>+7.3f}, {hi:>+7.3f}]  {verdict}")
    print()
    print("★구간이 0 을 걸치면 **차이를 확인하지 못한 것**이지 '같다'가 아니다.")
    print("  여러 개를 비교했으므로 다중비교 보정 없이 단일 비교로 읽으면 안 된다.")


_MD_COLS = ("모델", "차원", "최대", "MRR", "R@10", "뒷MRR", "뒷R@10",
            "면책MRR", "벡터무변화", "잘림", "정밀도")


def _report_md(rows: list) -> None:
    """마크다운 표. ★열 순서·의미는 고정폭 판과 **같다.**"""
    print("| " + " | ".join(_MD_COLS) + " |")
    print("|---|" + "---:|" * (len(_MD_COLS) - 2) + "---|")
    for r in rows:
        stale = "blind_eps" not in r
        pv = r.get("tail") or r.get("proviso") or {}
        pm, pr = pv.get("mrr@10"), pv.get("recall@10")
        ex = (r.get("exclusion") or {}).get("mrr@10")
        f3 = lambda v: f"{v:.3f}" if v is not None else "—"
        blind = ("☠재측정" if stale
                 else f"**{r['proviso_blind_count']}**/{r['proviso_probes']}"
                 if r.get("proviso_blind_count") else f"0/{r['proviso_probes']}")
        print("| `{}` | {} | {} | {} | {} | {} | {} | {} | {} | {:.1%} | {} |".format(
            r["model"], r["dim"], r["max_seq_length"],
            f3(r["mrr@10"]), f3(r["recall@10"]), f3(pm), f3(pr), f3(ex),
            blind, r["truncated_ratio"], r.get("dtype", "?")))


def repro_commands(md: bool = False) -> None:
    """**저장된 결과에서** 그것을 만든 명령을 되짚어 낸다.

    ★손으로 관리하던 목록(`remote_bench.sh`)이 실제 측정과 어긋나 있었다 —
      21회를 쟀는데 목록에는 13개뿐이었고, `Qwen3-8B` 는 `--quant 4bit` 없이
      적혀 있어 그대로 돌리면 12GB 에서 죽는다(코덱스 지적 2026-08-03).
      "재현 방법"이라고 적어 놓고 재현이 안 되면 그건 재현 방법이 아니다.

    ★그래서 **목록을 따로 두지 않는다.** 결과 JSON 이 곧 명령의 원본이다.
      측정을 하나 더 하면 이 출력이 저절로 따라온다.
    """
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(_OUT.glob("*.json"))]
    #: 큰 것을 뒤로 — 디스크가 좁을 때 작은 것부터 끝난다.
    rows.sort(key=lambda d: (d.get("dtype") != "float16", d.get("dim", 0), d["model"]))
    lines = []
    for d in rows:
        model = d["model"].split(" ")[0]
        cmd = f"python -m scripts.eval.bench_embedders --model {model} --device cuda"
        if d.get("dtype") == "4bit":
            cmd += " --quant 4bit"
        #: ★배포 설정을 바꿔 잰 것은 `--max-seq` 가 있어야 같은 값이 나온다.
        if " (max_seq=" in d["model"]:
            cmd += f" --max-seq {d['max_seq_length']}"
        lines.append((cmd + " --purge", d["model"], d.get("dtype")))
    if md:
        print("```bash")
    for cmd, _m, _t in lines:
        print(cmd)
    if md:
        print("```")
    print()
    print(f"# 총 {len(lines)}회 — fp16 {sum(1 for _, _, t in lines if t == 'float16')}"
          f" · 4bit {sum(1 for _, _, t in lines if t == '4bit')}")
    print("# ★탐침 재측정(§5-10)은 위 명령에 `--probes-only` 를 붙여 돌린 뒤")
    print("#   `python -m scripts.eval.merge_probe_remeasure <폴더>` 로 합쳤다.")


def prefix_table(md: bool = True) -> None:
    """**실제로 넣은 접두어**를 모델별로 낸다. ★측정한 모델만.

    ★손으로 쓴 표가 코드와 어긋나 있었다(코덱스 지적 2026-08-03) —
      `arctic`·`arctic-ko`·`laal` 을 「없음」으로 적어 두었는데 실제로는 넣고 쟀다.
      표가 틀리면 "권장 설정으로 쟀다"가 거짓이 된다. 그래서 **코드에서 뽑는다.**

    ★이 표는 **우리가 넣은 것**이지 **모델이 요구하는 것**이 아니다.
      둘을 대조하는 일은 아직 안 했다 — 문서에 그렇게 적어 둔다.
    """
    measured = {json.loads(p.read_text(encoding="utf-8"))["model"].split(" ")[0]
                for p in _OUT.glob("*.json")}
    seen: set[str] = set()
    rows = []
    for m, _, q, d, _n in CANDIDATES:
        if m in seen or m not in measured:
            continue
        seen.add(m)
        how = ('`prompt_name="query"`' if q == "@qwen"
               else f"질의 `{q}` · 문서 `{d}`" if q and d
               else f"질의 `{q}` · 문서 없음" if q else "없음")
        rows.append((how, m))
    #: ★**측정했는데 후보 목록에 없는 모델**을 조용히 빠뜨리지 않는다.
    #:   `Frony` 가 그랬다 — 표에서 사라져 접두어를 무엇으로 넣었는지 알 수 없었다.
    orphan = sorted(measured - seen)
    order = sorted({h for h, _ in rows}, key=lambda h: (h == "없음", h))
    if md:
        print("| 접두어 | 적용한 모델 |")
        print("|---|---|")
        for h in order:
            names = " · ".join(f"`{m}`" for hh, m in rows if hh == h)
            print(f"| {h} | {names} |")
        if orphan:
            print(f"| ★**목록에 없음 — 확인 필요** | "
                  + " · ".join(f"`{m}`" for m in orphan) + " |")
    else:
        for h in order:
            print(h, [m for hh, m in rows if hh == h])


def report(dtype: str = "", md: bool = False) -> None:
    """측정 결과 표. `dtype` 을 주면 그 정밀도만. `md=True` 면 **마크다운 표**.

    ★`--md` 는 문서에 붙이려고 있다. 고정폭 표를 마크다운 문서에 붙이면
      `|` 가 없어 표로 안 보이고, 사람이 손으로 `|` 를 넣다가 데이터와 어긋난다.
      **붙일 형식 그대로 뽑는다** — 손이 닿을 자리를 없앤다.

    ★정밀도를 **섞어 한 표에 두지 않는다.** 브리핑 §3 은 `--dtype float16`,
      §5-9 는 `--dtype 4bit` 로 각각 뽑아 붙인다.
      손으로 행을 지워 표를 만들면 머리글·구분선이 데이터와 어긋난다
      (실제로 어긋난 채 팀에 나갈 뻔했다 — 2026-08-03).
    """
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(_OUT.glob("*.json"))]
    if dtype:
        rows = [r for r in rows if r.get("dtype") == dtype]
    if not rows:
        print("아직 결과가 없습니다.")
        return
    rows.sort(key=lambda r: -r.get("mrr@10", 0))
    if md:
        _report_md(rows)
        return
    head = (f"{'모델':50} {'차원':>5} {'최대':>6} {'MRR':>6} {'R@10':>6} "
            f"{'뒷MRR':>7} {'뒷R@10':>8} {'면책MRR':>8} {'벡터무변화':>9} {'잘림':>6} {'정밀도':>7}")
    print(head)
    print("-" * len(head))
    for r in rows:
        #: ★옛 공식(fp16 내적 · 문턱 1e-6)으로 잰 결과는 **무효 표시**한다.
        #:   `blind_eps` 가 없으면 옛 것이다. 값을 지우지 않고 남기되,
        #:   "재측정 전" 이라고 밝힌다 — 조용히 지우면 왜 없는지 알 수 없다.
        stale = "blind_eps" not in r
        flag = " ☠무효(옛공식)" if stale else (
            " ★벡터무변화" if r.get("proviso_blind_count", 0) > 0 else "")
        pv = r.get("tail") or r.get("proviso") or {}
        pm = pv.get("mrr@10")
        pr = pv.get("recall@10")
        ex = (r.get("exclusion") or {}).get("mrr@10")
        print(
            f"{r['model']:50} {r['dim']:>5} {r['max_seq_length']:>6} "
            f"{r['mrr@10']:>6.3f} {r['recall@10']:>6.3f} "
            f"{(f'{pm:.3f}' if pm is not None else '—'):>8} "
            f"{(f'{pr:.3f}' if pr is not None else '—'):>8} "
            f"{(f'{ex:.3f}' if ex is not None else '—'):>8} "
            f"{(str(r['proviso_blind_count']) + '/' + str(r['proviso_probes'])) if not stale else '재측정':>8} "
            f"{r['truncated_ratio']:>6.1%} {r.get('dtype','?'):>7}{flag}"
        )
    print()
    print("뒷MRR/뒷R@10 = 조항 **뒤쪽** 문장(다만·단·그러나·이 경우…)을 질의로 준 성적.")
    print("면책MRR = 그중 **부정·면책 표현이 실제로 있는** 16개만. ★표본이 작다.")
    print("  ★앞서 60개 전부를 「면책」이라 불렀는데 실제 면책은 16개뿐이었다.")
    #: ★"못 찾는다"고 단정하지 않는다 — 앞부분 단서만으로 맞히는 경우가 있다.
    print("  ★앞부분만 임베딩하는 모델은 그 문장이 임베딩에 안 들어가 **불리하다**.")
    print("    (앞부분 단서만으로 맞히는 경우가 있으므로 못 찾는다고 단정하지 않는다.)")
    print(f"벡터무변화 = 앞부분과 「앞부분+단서」의 코사인 거리가 {_BLIND_EPS:g} 미만인 탐침 수.")
    print("  ☠**무효(옛공식)** = 코사인을 fp16 으로 재고 문턱을 1e-6 으로 뒀던 측정.")
    print("    벡터가 단위 노름이 아니라 거리가 **음수**로 나왔고(최소 -0.00498),")
    print("    fp16 분해능은 2^-11≈4.9e-4 라 1e-6 은 **잴 수 없는 문턱**이었다.")
    print("    → 그 열은 재측정 전까지 **쓰지 않는다.** 순위·MRR 은 영향 없다.")
    #: ★「면책못봄」이라 부르던 것이다. 재는 것은 벡터가 안 움직였다는 사실뿐이고,
    #:   "모델이 면책 문장을 못 봤다"는 **해석**은 여기서 하지 않는다.
    print("  ★이전 이름은 「면책못봄」이었다 — 재는 것과 이름이 달라 바꿨다.")
    print("잘림 = 코퍼스 중 모델 최대 길이를 넘은 비율.")
    print("★정밀도가 다르면(fp16 vs 4bit) **같은 표에서 순위를 매기지 않는다.**")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="임베딩 모델 벤치")
    ap.add_argument("--model")
    #: ★64 로 두었더니 32K 문맥 모델(granite)이 어텐션 마스크에서 CUDA OOM 났다.
    #:   조각이 448토큰이라 배치를 키워도 얻는 게 적다.
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--no-fp16", action="store_true", help="fp32 로 잰다(대조용)")
    #: ★8B·12B 를 12GB GPU 에서 재려면 이것이 필요하다.
    #: ★배포 설정의 길이 제한을 올려 본다. 모델 교체 없이 고쳐지는지 확인용.
    ap.add_argument("--max-seq", type=int, default=0,
                    help="max_seq_length 를 이 값으로 올린다(토크나이저 한계 안에서)")
    ap.add_argument("--quant", default="", choices=["", "4bit", "8bit"],
                    help="양자화. ★결과에 dtype 이 박히고 보고표에 표시된다")
    ap.add_argument("--device", default="")
    ap.add_argument("--purge", action="store_true", help="측정 후 캐시 삭제")
    ap.add_argument("--list", action="store_true")
    #: ★문서에 붙일 거면 `--md`. 손으로 `|` 를 넣지 않는다.
    ap.add_argument("--md", action="store_true", help="마크다운 표로 출력")
    #: ★탐침만 재측정(§5-10 결함 수정분). 순위 지표는 기존 값을 유지한다.
    ap.add_argument("--probes-only", action="store_true",
                    help="탐침만 다시 재고 기존 결과에 합친다")
    ap.add_argument("--report", action="store_true")
    #: ★접두어 표도 **코드에서 뽑는다.** 손으로 쓰면 어긋난다.
    ap.add_argument("--prefixes", action="store_true", help="실제로 넣은 접두어 표")
    #: ★재현 명령도 **결과에서 되짚는다.** 손 목록은 어긋난다.
    ap.add_argument("--repro", action="store_true", help="저장된 결과를 만든 명령")
    ap.add_argument("--compare", default="", help="이 모델을 기준으로 짝지어 비교")
    ap.add_argument("--task", default="title", choices=["title", "tail", "exclusion"])
    #: ★정밀도를 섞지 않는다. 빈 값이면 전부.
    #: ★`--compare` 와 `--report` 가 함께 쓴다. 빈 문자열이면 전부(섞인다 — 권장 안 함).
    ap.add_argument("--dtype", default="float16",
                    help="대상 정밀도. float16(기본) | 4bit | \"\"(전부·섞임)")
    a = ap.parse_args(argv)

    if a.list:
        print("★측정하지 않는 것:")
        for m, why in NOT_MEASURED.items():
            print(f"  {m:52} {why}")
        print()
        print(f"{'모델':52} {'GB':>5}  비고")
        for m, gb, *_rest in CANDIDATES:
            print(f"{m:52} {gb:>5.1f}  {_rest[2]}")
        return 0
    if a.compare:
        compare(a.compare, a.task, dtype=a.dtype)
        return 0
    if a.report:
        report(a.dtype, md=a.md)
        return 0
    elif a.prefixes:
        prefix_table(md=a.md or True)
        return 0
    elif a.repro:
        repro_commands(md=a.md)
        return 0
    if not a.model:
        ap.error("--model 이 필요합니다 (--list 로 후보 확인)")

    known = {c[0]: c for c in CANDIDATES}
    _, _, qp, dp, note = known.get(a.model, (a.model, 0, "", "", ""))
    print(f"[{a.model}] {note}", flush=True)

    res = run(a.model, q_prefix=qp, d_prefix=dp, batch=a.batch, device=a.device,
              no_fp16=a.no_fp16, quant=a.quant, max_seq=a.max_seq,
              probes_only=a.probes_only)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if res["proviso_blind_count"]:
        print(
            f"\n★경고: 탐침 {res['proviso_blind_count']}/{res['proviso_probes']} 건에서 "
            f"앞부분과의 코사인 거리가 {res.get('blind_eps', 0):g} 미만이었습니다 — 뒷부분이 표현을 "
            "바꾸지 못했다는 강한 증상입니다(모델이 못 봤다는 증거는 아닙니다)."
        )
    if a.purge:
        print("[정리]", _purge(a.model))
    return 0


def _utf8_stdout() -> None:
    """★기본 cp949 콘솔에서 `—` 하나 때문에 죽지 않게 한다.

    실측 2026-08-03 — `PYTHONIOENCODING` 없이 `--report --dtype 4bit` 를 돌리면
    `UnicodeEncodeError` 로 **표가 안 나온다.** 팀원이 재현 명령을 그대로
    복사해 붙이면 여기서 막힌다. 도구가 환경을 탓하지 않게 스스로 맞춘다.
    """
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — 리다이렉트 등 재설정 불가한 경우
            pass


if __name__ == "__main__":
    _utf8_stdout()
    raise SystemExit(main())
