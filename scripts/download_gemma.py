"""프로젝트 기본 Gemma GGUF를 Hugging Face 캐시에 내려받는다."""

from __future__ import annotations

from huggingface_hub import hf_hub_download


def main() -> None:
    path = hf_hub_download(
        repo_id="google/gemma-4-E4B-it-qat-q4_0-gguf",
        filename="gemma-4-E4B_q4_0-it.gguf",
    )
    print(path)


if __name__ == "__main__":
    main()
