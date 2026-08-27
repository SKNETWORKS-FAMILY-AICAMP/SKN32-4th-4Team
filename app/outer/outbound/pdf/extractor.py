"""PDF 근거 추출기 — 바깥 계층(기술 상세).

`app/core` 는 이 모듈을 모른다. 여기서 pypdf 를 쓰고, 안쪽에는
`ExtractedEvidence`(순수 값)만 돌려준다.

추출 실패를 조용히 넘기지 않는다:
- 암호화·손상 → `ExtractionQuality.UNREADABLE` 로 표시하고 격리 대상이 되게 한다
- 텍스트가 거의 없음 → `LOW_TEXT` (스캔본 가능성)
★빈 문자열을 정상 결과로 돌려주면 "근거가 없다"와 "근거를 못 읽었다"가 구분되지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader

from app.core.domain.document_identification import ExtractionQuality
from app.core.errors import InfraError
from app.core.usecases.identify_document import ExtractedEvidence

#: 표지로 볼 페이지 수 / 목차로 볼 페이지 수 / 본문 표본 페이지 수
COVER_PAGES = 2
TOC_PAGES = 6
BODY_SAMPLE_PAGES = 8
LOW_TEXT_THRESHOLD = 2_000


class PdfEvidenceExtractor:
    """파일 하나에서 표지·목차·본문 근거를 뽑는다."""

    def extract(self, *, path: Path, sha256: str) -> ExtractedEvidence:
        if not path.exists():
            raise InfraError(f"파일이 없습니다: {path}")
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                return _unreadable(sha256, 0, "암호화")
            pages = reader.pages
            n = len(pages)
        except Exception as e:  # noqa: BLE001
            raise InfraError(f"PDF 열기 실패({type(e).__name__}): {path.name}") from e

        def _text(idx_from: int, idx_to: int) -> str:
            out: list[str] = []
            for i in range(idx_from, min(idx_to, n)):
                try:
                    out.append(pages[i].extract_text() or "")
                except Exception:  # noqa: BLE001
                    # 한 페이지 실패는 전체 실패가 아니다. 다만 조용히 넘기지 않고
                    # 길이가 줄어들어 품질 판정에 반영되게 둔다.
                    continue
            return "\n".join(out)

        cover = _text(0, COVER_PAGES)
        toc = _text(COVER_PAGES, COVER_PAGES + TOC_PAGES)
        # 본문은 가운데에서 뽑는다. 앞쪽은 표지·목차라 문서종류 판별에 편향된다.
        mid = max(0, n // 2)
        body = _text(mid, mid + BODY_SAMPLE_PAGES)
        total = len(cover) + len(toc) + len(body)

        if total == 0:
            return _unreadable(sha256, n, "텍스트 0자")
        quality = (
            ExtractionQuality.LOW_TEXT if total < LOW_TEXT_THRESHOLD else ExtractionQuality.OK
        )
        return ExtractedEvidence(
            artifact_sha256=sha256,
            page_count=n,
            text_length=total,
            cover_text=cover,
            toc_text=toc,
            body_sample=body,
            quality=quality,
        )


def _unreadable(sha256: str, pages: int, why: str) -> ExtractedEvidence:
    return ExtractedEvidence(
        artifact_sha256=sha256,
        page_count=pages,
        text_length=0,
        cover_text="",
        toc_text="",
        body_sample=f"[추출 불가: {why}]",
        quality=ExtractionQuality.UNREADABLE,
    )
