"""GPU 상자에서 **임베딩만** 한다. 이 파일 하나만 원격에 복사하면 된다.

    python gpu_embed.py --jsonl shard1.jsonl --out shard1.f32

★DB 를 모른다. 자격증명을 원격에 두지 않기 위해서다.
  들어오는 것은 조각 텍스트, 나가는 것은 float32 벡터뿐이다.

★조각내기를 여기서 하지 않는다. `shard_embed.py` 가 이미 잘라서 보낸다 —
  양쪽에서 자르면 transformers 판 차이로 경계가 어긋난다.

★★**줄 순서를 지킨다.** 출력 `.f32` 는 입력 JSONL 과 **같은 순서**의
  `(N, 768)` float32 연속 블록이다. 정렬하거나 건너뛰면 벡터가 엉뚱한
  조항에 박히고, 그건 아무 오류도 내지 않는다. 가장 위험한 실패다.
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import torch
from sentence_transformers import SentenceTransformer

MODEL = "jhgan/ko-sroberta-multitask"
DIM = 768


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch", type=int, default=128)
    a = ap.parse_args()

    rows = [json.loads(x) for x in open(a.jsonl, encoding="utf-8")]
    texts = [r["t"] for r in rows]
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    #: ★CPU 로 조용히 떨어지지 않는다. 그러면 "GPU 로 돌렸다"는 보고가 거짓이 된다.
    if dev != "cuda":
        raise SystemExit("CUDA 를 찾지 못했습니다. CPU 로 대신 돌리지 않습니다.")
    print(f"{len(texts):,}조각 · {torch.cuda.get_device_name(0)}", flush=True)

    m = SentenceTransformer(MODEL, device=dev)
    v = m.encode(
        texts,
        batch_size=a.batch,
        convert_to_numpy=True,
        show_progress_bar=True,
        normalize_embeddings=False,   #: ★로컬 경로와 같아야 한다. 여기서만 정규화하면 거리가 달라진다.
    ).astype(np.float32)

    if v.shape != (len(texts), DIM):
        raise SystemExit(f"모양이 {v.shape} 입니다. ({len(texts)}, {DIM}) 이어야 합니다.")
    v.tofile(a.out)
    print(f"[완료] {a.out} · {v.shape}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
