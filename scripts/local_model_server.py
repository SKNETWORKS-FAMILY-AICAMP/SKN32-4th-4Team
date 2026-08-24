"""로컬 Gemma OpenAI 호환 서버 (개발용).

llama_cpp.server는 starlette_context(starlette 1.x)를 요구해 앱의 fastapi(starlette
0.47)와 충돌한다. 이를 피하기 위해 llama_cpp.Llama를 직접 감싼 최소 OpenAI 호환
엔드포인트를 제공한다. create_chat_completion이 이미 OpenAI 형식을 반환하므로
얇은 패스스루로 충분하다.

실행:
    python scripts/local_model_server.py

환경변수:
    GEMMA_GGUF_PATH  : GGUF 모델 경로 (기본: HF 캐시 경로)
    LOCAL_MODEL_PORT : 포트 (기본 8002 — 앱 개발 서버 8000과 분리)
    N_CTX            : 컨텍스트 (기본 4096)
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI, Request
from llama_cpp import Llama

#: HF 캐시의 모델 저장소 디렉터리 이름(리비전 해시는 여기 넣지 않는다).
_HF_REPO_DIR = "models--google--gemma-4-E4B-it-qat-q4_0-gguf"
_GGUF_NAME = "gemma-4-E4B_q4_0-it.gguf"


def _discover_gguf() -> str | None:
    """HF 캐시에서 GGUF를 **동적으로** 찾는다(리비전 해시 하드코딩 금지).

    이전 버전은 스냅샷 해시(`bb3b92e6…`)를 경로에 박아뒀는데, 모델을 다시 받으면 해시가
    달라져(`4b4a2c1d…`) 파일이 있어도 못 찾았다 — 실제로 겪은 문제라 탐색으로 바꿨다.
    여러 리비전이 있으면 가장 최근 수정본을 쓴다.
    """
    from pathlib import Path

    home = os.environ.get("HF_HOME")
    roots = [Path(home) / "hub"] if home else []
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    for root in roots:
        snaps = root / _HF_REPO_DIR / "snapshots"
        if not snaps.is_dir():
            continue
        found = sorted(snaps.glob(f"*/{_GGUF_NAME}"), key=lambda p: p.stat().st_mtime, reverse=True)
        if found:
            return str(found[0])
    return None


# LOCAL_GGUF_PATH(권장) 또는 GEMMA_GGUF_PATH(하위호환)로 명시 지정 가능. 없으면 캐시에서 탐색.
GGUF_PATH = (
    os.environ.get("LOCAL_GGUF_PATH")
    or os.environ.get("GEMMA_GGUF_PATH")
    or _discover_gguf()
    or ""  # 아래 존재 검사에서 명시적으로 실패한다(무폴백)
)
PORT = int(os.environ.get("LOCAL_MODEL_PORT", "8002"))
# 이 머신은 여유 RAM이 ~5GB로 빠듯하다. 컴퓨트 버퍼는 배치에 비례하므로 작게 잡는다
# (debug_notes 2026-07-12_2028 / RAM 제약 노트 참조).
N_CTX = int(os.environ.get("N_CTX", "1024"))
N_BATCH = int(os.environ.get("N_BATCH", "32"))
# tool-calling 지원 모델(Qwen 등)은 CHAT_FORMAT=chatml-function-calling으로 실행.
# Gemma는 tool-calling 미지원이라 미설정(빈값)으로 둔다.
CHAT_FORMAT = os.environ.get("CHAT_FORMAT") or None

app = FastAPI(title="Local Gemma (OpenAI-compatible)")
_llm: Llama | None = None


def get_llm() -> Llama:
    global _llm
    if _llm is None:
        kwargs = dict(
            model_path=GGUF_PATH,
            n_ctx=N_CTX,
            n_batch=N_BATCH,
            n_ubatch=N_BATCH,
            use_mmap=True,
            verbose=False,
        )
        if CHAT_FORMAT:
            kwargs["chat_format"] = CHAT_FORMAT
        _llm = Llama(**kwargs)
    return _llm


@app.get("/v1/models")
def list_models() -> dict:
    return {"object": "list", "data": [{"id": "gemma-4-e4b", "object": "model"}]}


@app.get("/healthz")
def healthz() -> dict:
    """프로세스와 모델 로드가 끝났음을 확인한다."""
    return {"ready": _llm is not None, "model": "gemma-4-e4b"}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> dict:
    body = await request.json()
    llm = get_llm()
    # tool calling 포함 OpenAI 파라미터를 그대로 전달
    kwargs = {"messages": body["messages"]}
    for key in ("temperature", "max_tokens", "top_p", "tools", "tool_choice", "stop"):
        if key in body and body[key] is not None:
            kwargs[key] = body[key]
    return llm.create_chat_completion(**kwargs)


if __name__ == "__main__":
    # 모델이 없으면 llama_cpp 내부 오류로 흐리게 실패하지 않고, 무엇을 해야 하는지 알려주고 멈춘다.
    if not GGUF_PATH or not os.path.isfile(GGUF_PATH):
        raise SystemExit(
            "GGUF 모델을 찾을 수 없습니다.\n"
            f"  탐색 대상: HF 캐시의 {_HF_REPO_DIR}/snapshots/*/{_GGUF_NAME}\n"
            f"  현재 값  : {GGUF_PATH or '(없음)'}\n"
            "해결: (1) 모델 다운로드\n"
            "        python -c \"from huggingface_hub import hf_hub_download as d; "
            "d(repo_id='google/gemma-4-E4B-it-qat-q4_0-gguf', filename='gemma-4-E4B_q4_0-it.gguf')\"\n"
            "      (2) 또는 LOCAL_GGUF_PATH 환경변수로 직접 지정"
        )
    print(f"[local_model_server] loading {GGUF_PATH}")
    get_llm()
    print(f"[local_model_server] ready on http://127.0.0.1:{PORT}/v1")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
