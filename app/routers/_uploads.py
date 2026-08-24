"""업로드 크기 상한 헬퍼(Interface 계층) — 모델 연산 DoS 표면 축소.

`UploadFile`을 통째로 메모리에 읽기 전에 청크 단위로 누적하며 상한을 검사한다. 초과하면
조용히 자르지 않고(무폴백) `ValidationErr`로 명시적으로 거부한다. 상한값은 config에서 온다
(하드코딩 금지).
"""

from __future__ import annotations

from fastapi import UploadFile

from app.core.errors import ValidationErr

_CHUNK = 1 << 16  # 64KB


async def read_capped(upload: UploadFile, max_bytes: int, *, field: str = "파일") -> bytes:
    """`upload`를 최대 `max_bytes`까지만 읽는다. 초과하면 ValidationErr(무폴백)."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            limit_mb = max_bytes / (1024 * 1024)
            raise ValidationErr(f"{field} 크기가 허용 한도({limit_mb:.0f}MB)를 초과했습니다.")
        chunks.append(chunk)
    return b"".join(chunks)
