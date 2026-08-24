"""확정된 약관 버전의 **조항**을 꺼낸다 (어댑터).

이 모듈은 **파일을 읽는다** — 그래서 `app/adapters/` 에 있다.
도메인 타입은 `app/core/ports/precheck.py` 것을 쓴다.

★검색 범위를 약관 버전으로 **가둔다**

    이게 이 모듈의 존재 이유다. 전체 조항에서 검색하면
    **2019년 가입자에게 2024년 약관 조항**이 근거로 붙는다.
    `policy_version.resolve()` 로 확정한 `sha256` 안에서만 찾는다.

★판정에 쓸 수 없는 것은 내주지 않는다

    · `parse_status != "ok"`      조항 구조화가 실패한 문서다. `page_fallback`
                                  청크는 "제N조"를 댈 수 없으므로 근거가 못 된다.
    · `chunk_type == "page_fallback"`  위와 같은 이유.

    검색(RAG)에는 쓸 수 있어도 **판정 근거로는 못 쓴다.** 둘을 구분해 내준다.

★조 번호만으로는 유일하지 않다

    특별약관이 여러 개면 조 번호가 1부터 다시 시작한다.
    실측: 한 문서에서 `제2조` 가 51번 나왔다(부 구분 실패 시).
    그래서 `qualified_no`(`부/제N조`)를 식별자로 쓰고,
    같은 번호가 여럿이면 **모두 돌려준다** — 하나를 골라 주지 않는다.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from app.core.errors import InfraError
from app.core.ports.precheck import ClauseRow

_ROOT = Path(__file__).resolve().parents[2]
_STRUCTURED = _ROOT / "data" / "structured"

#: 판정 근거로 쓸 수 없는 청크.
_NON_CLAUSE = {"page_fallback"}


#: ★판정에 쓸 추출 버전. **설정으로 못박는다.**
#:
#:   이전에는 `가장 큰 sN 을 자동 선택`했다. 그게 위험한 이유:
#:
#:     · 전처리를 새로 돌리는 **도중에** 판정 결과가 바뀐다. s5 가 200건만
#:       만들어진 상태에서 어떤 문서는 s5, 어떤 문서는 없어서 실패한다
#:     · **새 버전이 더 낫다는 보장이 없다.** 실측으로 v5 는 본문 24,511쪽을
#:       되찾았지만 `parse_status=ok` 가 1,240 → 1,108 로 줄었다.
#:       "최신"과 "쓸 만함"은 다른 말이다
#:     · 같은 질문에 다른 답이 나오는데 **아무 기록도 남지 않는다**
#:
#:   ERD 설계도 같은 결론이다 — *"가장 큰 sN 폴더를 자동 선택하지 않는다.
#:   문서별로 `accepted` 인 extraction 하나를 지정한다."*
#:   DB 적재 전까지는 **전역 고정값**으로 대신한다.
_ACCEPTED_SCHEMA_FILE = _ROOT / "config" / "accepted_extraction.json"


def _accepted_tag() -> str:
    """판정에 쓸 조항 산출 폴더 이름.

    ★★**세대는 `app/core/release.py` 한 곳에서만 정한다.**
      여기·적재·검색 세 곳에 각각 상수를 두었더니 서로 어긋났다(실측 2026-08-03):
      이 저장소는 `s5` 를, 벡터 검색은 `s6` 를 보고 있었다.
      같은 질문에 두 경로가 **다른 조항**을 준다.

    ★자동으로 고르지 않는다. 설정에 적힌 것만 쓴다.
      설정이 없으면 **실패한다** — 아무거나 골라 쓰느니 멈추는 편이 낫다.
    """
    from app.core import release

    try:
        rel = release.current()
        rel.ensure_ready()
    except Exception as exc:  # noqa: BLE001
        raise InfraError(str(exc)) from exc
    return rel.clause_tag


def available_tags() -> list[str]:
    """디스크에 있는 산출 버전들. 무엇을 고를 수 있는지 보여 줄 때 쓴다."""
    dirs = {p.name for p in _STRUCTURED.glob("*/s*_*") if p.is_dir()}

    def key(name: str) -> tuple[int, str]:
        m = re.match(r"^s(\d+)_(.*)$", name)
        return (int(m.group(1)), m.group(2)) if m else (0, name)

    return sorted(dirs, key=key)


@lru_cache(maxsize=256)
def _load_doc_cached(sha256: str, tag: str) -> dict:
    """조항 JSON 하나. sha 로 찾는다.

    ★★**캐시 키에 `tag` 가 들어간다.** 안 넣으면 승인 릴리스를 바꿔도
      같은 프로세스가 **옛 세대 문서를 계속 돌려준다**(코덱스 지적).
      전환은 원자적이어야 하는데 캐시가 그걸 깬다.
    """
    hits = list(_STRUCTURED.glob(f"*/{tag}/{sha256[:12]}.clauses.json"))
    if not hits:
        raise InfraError(
            f"조항 산출물을 찾지 못했습니다: {sha256[:12]} (추출기 {tag})\n"
            "전처리가 아직 안 돌았거나 판정 대상에서 제외된 문서입니다."
        )
    return json.loads(hits[0].read_text(encoding="utf-8"))


def _load_doc(sha256: str) -> dict:
    """승인 릴리스를 매번 확인하고 캐시를 태운다."""
    return _load_doc_cached(sha256, _accepted_tag())


def _to_clause(sha256: str, c: dict, parse_status: str) -> ClauseRow:
    #: ★★판정은 **공통 게이트 하나**로 한다(`app/core/domain/eligibility.py`).
    #:   전에는 여기서 `parse_status` 와 `chunk_type` 만 봤고
    #:   조항별 `citation_eligible` 은 **아예 보지 않았다**(코덱스 지적).
    #:   저장소마다 규칙이 다르면 어느 것이 맞는지 아무도 모른다.
    from app.core.domain import eligibility

    v = eligibility.check(c, parse_status=parse_status)
    reason = "" if v.usable else v.reason
    loc = c.get("locator", {})
    return ClauseRow(
        sha256=sha256,
        qualified_no=c.get("qualified_no", ""),
        clause_no=c.get("clause_no", ""),
        section=c.get("section", ""),
        title=c.get("title", ""),
        text=c.get("text", ""),
        page_from=loc.get("page_from", 0),
        page_to=loc.get("page_to", 0),
        content_hash=c.get("content_hash", ""),
        usable=v.usable,
        unusable_reason=reason,
        #: ★게이트가 본 값을 **그대로 실어 보낸다.** 하류(인용 검증)가
        #:   같은 규칙으로 다시 검사할 수 있어야 한다.
        citation_eligible=c.get("citation_eligible"),
        chunk_type=c.get("chunk_type"),
        is_statute=c.get("statute", c.get("is_statute")),
        parse_status=parse_status,
        ordinal=c.get("ordinal"),
        source_kind=c.get("source_kind") or "clause",
        release_id=_release_id(),
    )


def _release_id() -> str:
    from app.core import release

    try:
        return release.current().release_id
    except Exception:  # noqa: BLE001
        return ""


def load_clauses(sha256: str, *, usable_only: bool = True) -> list[ClauseRow]:
    """한 약관 버전의 조항 전부.

    Args:
        sha256: `policy_version.resolve()` 가 확정한 문서.
        usable_only: 판정 근거로 쓸 수 있는 것만. `False` 면 전부(검색용).
    """
    doc = _load_doc(sha256)
    #: ★기본값이 `"ok"` 였다 — 필드가 없으면 **통과시켰다**(fail-open).
    #:   옛 스키마 산출물이나 깨진 파일이 조용히 판정 근거가 된다.
    #:   모르면 못 믿는 것으로 본다.
    status = doc.get("parse_status") or "unknown"
    out = [_to_clause(sha256, c, status) for c in doc.get("clauses", [])]
    return [c for c in out if c.usable] if usable_only else out


def find_by_number(sha256: str, number: str) -> list[ClauseRow]:
    """조 번호로 찾는다. `"제9조"` 또는 `"보통약관/제9조"`.

    ★같은 번호가 여럿이면 **전부** 돌려준다. 하나를 골라 주지 않는다 —
      특별약관마다 조 번호가 1부터 다시 시작하므로 어느 것인지 우리가 정할 수 없다.
    """
    want = _norm_no(number)
    return [c for c in load_clauses(sha256) if _norm_no(c.qualified_no) == want]


def _norm_no(s: str) -> str:
    """`보통약관/제 9 조` → `제9조`."""
    tail = (s or "").rsplit("/", 1)[-1]
    m = re.search(r"제\s*(\d{1,3})\s*조(?:\s*의\s*(\d{1,2}))?", tail)
    if not m:
        return re.sub(r"\s+", "", tail)
    return f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")


def search(sha256: str, query: str, *, limit: int = 8) -> list[ClauseRow]:
    """낱말 기준 조항 검색.

    ★임베딩 검색은 아직 없다. 여기서는 **낱말 포함**만 본다.
      이 단계의 목적은 "조항 단위로 근거를 뽑아 낼 수 있는가"를 세우는 것이고,
      의미 검색은 pgvector 색인이 선 뒤에 붙인다.

      ★"검색이 안 되면 판정하지 않는다"가 원칙이므로,
        낱말이 안 걸리면 **빈 목록**을 돌려준다. 비슷한 걸 끌어오지 않는다.
    """
    terms = [t for t in re.split(r"\s+", (query or "").strip()) if len(t) >= 2]
    if not terms:
        return []
    scored: list[tuple[int, ClauseRow]] = []
    for c in load_clauses(sha256):
        body = c.text
        hit = sum(body.count(t) for t in terms)
        if hit:
            #: 제목에 걸리면 가점 — 조항 제목은 그 조항의 주제다.
            hit += sum(3 for t in terms if t in c.title)
            scored.append((hit, c))
    scored.sort(key=lambda x: (-x[0], x[1].page_from))
    return [c for _, c in scored[:limit]]


def stats(sha256: str) -> dict:
    """문서 요약 — 판정 전에 쓸 만한 문서인지 본다."""
    doc = _load_doc(sha256)
    st = doc.get("stats", {})
    total = len(doc.get("clauses", []))
    usable = sum(1 for c in load_clauses(sha256, usable_only=False) if c.usable)
    return {
        #: ★fail-closed. 없으면 "ok" 가 아니라 "unknown" 이다.
        "parse_status": doc.get("parse_status") or "unknown",
        "numbering": doc.get("numbering", ""),
        "pages": st.get("pages", 0),
        "clauses_total": total,
        "clauses_usable": usable,
        "sections": doc.get("sections", []),
        "extractor": doc.get("extractor", ""),
    }
