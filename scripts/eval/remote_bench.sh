#!/usr/bin/env bash
# GPU 머신에서 임베딩 후보를 **하나씩** 재고 지운다.
#
#   bash remote_bench.sh            # 8GB 로 도는 후보 전부
#   bash remote_bench.sh --big      # 큰 것까지 (VRAM 여유 있을 때)
#
# ★하나 받아서 → 재고 → 지운다. 후보를 다 받으면 수십 GB 다.
# ★한 모델이 실패해도 멈추지 않는다. 대신 **실패를 센다** —
#   조용히 건너뛰면 "다 재봤다"고 잘못 말하게 된다.

set -u
cd "$(dirname "$0")/../.." || exit 1

SMALL=(
  "ibm-granite/granite-embedding-311m-multilingual-r2"
  "Snowflake/snowflake-arctic-embed-l-v2.0"
  "dragonkue/snowflake-arctic-embed-l-v2.0-ko"
  "nlpai-lab/KURE-v1"
  "dragonkue/BGE-m3-ko"
  "BAAI/bge-m3"
  "intfloat/multilingual-e5-large"
  "nlpai-lab/KoE5"
  "Qwen/Qwen3-Embedding-0.6B"
  "nvidia/Nemotron-3-Embed-1B-BF16"
  "jhgan/ko-sroberta-multitask"
)
BIG=(
  "Qwen/Qwen3-Embedding-4B"
  "Qwen/Qwen3-Embedding-8B"
)

MODELS=("${SMALL[@]}")
if [ "${1:-}" = "--big" ]; then MODELS+=("${BIG[@]}"); fi

ok=0; fail=0; failed=()
for m in "${MODELS[@]}"; do
  echo "=============================================================="
  echo "[$((ok+fail+1))/${#MODELS[@]}] $m"
  echo "=============================================================="
  if python -m scripts.eval.bench_embedders --model "$m" --batch 32 --purge; then
    ok=$((ok+1))
  else
    fail=$((fail+1)); failed+=("$m")
    #: ★실패해도 캐시는 지운다. 안 그러면 디스크가 찬다.
    python - <<PY
from huggingface_hub import constants
import pathlib, shutil
d = pathlib.Path(constants.HF_HUB_CACHE) / "models--$(echo "$m" | tr '/' '-' | sed 's/-/--/')"
shutil.rmtree(d, ignore_errors=True)
PY
  fi
  df -h . | tail -1
done

echo
echo "=============================================================="
echo "성공 $ok · 실패 $fail"
if [ ${#failed[@]} -gt 0 ]; then
  printf '  실패: %s\n' "${failed[@]}"
  echo "  ★실패를 숨기지 않는다. 아래 표에 없는 모델은 재지 못한 것이다."
fi
echo
python -m scripts.eval.bench_embedders --report
