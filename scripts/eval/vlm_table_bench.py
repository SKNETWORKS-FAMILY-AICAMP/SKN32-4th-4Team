"""VLM 표 인식을 **우리 정답셋으로** 좌표 복원과 맞붙인다.

    (GPU 상자에서) python vlm_table_bench.py --pdf 원본.pdf --page 108 --out 결과.json

★왜 재는가

    OmniDocBench 리더보드(2026-05-21)에서 PaddleOCR-VL-1.6 이 96.33 으로 1위다.
    그런데 그 벤치마크는 **한국어 보험약관을 재지 않는다.** 우리 좌표 복원은
    같은 정답셋 66레코드에서 1.000 이다. 남의 벤치마크로 갈아타지 않는다 —
    우리 문서로 재보고 이기면 갈아탄다.

★무엇을 재는가

    표 이미지를 넣고 나온 결과에서 **질병명↔KCD 코드 짝**을 뽑아 정답과 맞춘다.
    표 전체 구조가 아니라 **짝**을 재는 이유: 이 서비스에서 가장 위험한 실패가
    "심장질환 → I60~I69(뇌혈관 것)" 같은 오짝이기 때문이다.

★한계를 미리 적는다

    · 표본 66레코드. 이걸로 전체 정확도를 말하지 않는다.
    · VLM 은 좌표를 안 준다. 이기더라도 조 경계·페이지 추적은 따로 만들어야 한다.
    · 페이지 통째로 넣는다 — 표 영역 검출까지 포함한 end-to-end 다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time

MODEL = "PaddlePaddle/PaddleOCR-VL-1.6"
#: ★모델 카드가 지정한 표 인식 프롬프트. 임의로 바꾸면 모델을 불리하게 만든다.
PROMPT = "Table Recognition:"
DPI = 200


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True, help="0-based")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dpi", type=int, default=DPI)
    a = ap.parse_args()

    import fitz
    import torch
    from PIL import Image
    from transformers import AutoModelForImageTextToText, AutoProcessor

    if not torch.cuda.is_available():
        #: ★CPU 로 조용히 떨어지지 않는다. 0.9B VLM 을 CPU 로 돌리면
        #:   몇 분이 걸리고, 그 시간을 "모델이 느리다"로 잘못 기록하게 된다.
        raise SystemExit("CUDA 를 찾지 못했습니다. CPU 로 대신 돌리지 않습니다.")

    doc = fitz.open(a.pdf)
    page = doc[a.page]
    pm = page.get_pixmap(dpi=a.dpi)
    img_path = a.out + ".png"
    pm.save(img_path)
    doc.close()

    t0 = time.time()
    proc = AutoProcessor.from_pretrained(MODEL)
    model = AutoModelForImageTextToText.from_pretrained(
        MODEL, dtype=torch.float16, device_map="cuda:0")
    load_sec = time.time() - t0

    img = Image.open(img_path).convert("RGB")
    msgs = [{"role": "user", "content": [
        {"type": "image", "image": img},
        {"type": "text", "text": PROMPT},
    ]}]
    inputs = proc.apply_chat_template(
        msgs, add_generation_prompt=True, tokenize=True,
        return_dict=True, return_tensors="pt").to(model.device)

    t1 = time.time()
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=4096, do_sample=False)
    gen_sec = time.time() - t1
    text = proc.batch_decode(
        out[:, inputs["input_ids"].shape[1]:], skip_special_tokens=True)[0]

    json.dump({
        "model": MODEL, "prompt": PROMPT, "pdf": a.pdf, "page_0based": a.page,
        "dpi": a.dpi, "load_sec": round(load_sec, 1), "gen_sec": round(gen_sec, 1),
        "gpu": torch.cuda.get_device_name(0),
        "raw": text,
    }, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[완료] {a.out} · 생성 {gen_sec:.1f}s · {len(text):,}자")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
