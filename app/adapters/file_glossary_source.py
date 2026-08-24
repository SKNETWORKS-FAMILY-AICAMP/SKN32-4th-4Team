"""용어 정의 구절 — 파일 색인에서 읽는 어댑터.

색인은 `scripts/extract/build_glossary.py` 가 만든다.

    data/glossary/passages.jsonl   구절 2,739개(조항 1,621 · 붙임 1,118)
    data/glossary/meta.json        무엇으로 언제 만들었나

★색인이 없으면 **없다고 말한다.**

    조용히 빈 결과를 돌려주면 "약관에 그 용어가 없다"로 읽힌다.
    없는 것은 색인이지 용어가 아니다. `InfraError` 로 올려 503 이 되게 한다
    (무폴백 원칙 — CLAUDE.md §0).

★색인 파일은 **약관 원문 조각이다.** 저작물이므로 커밋하지 않는다(`.gitignore`).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from app.core.errors import InfraError
from app.core.ports.glossary import TermPassage

_ROOT = Path(__file__).resolve().parents[2]
_DIR = _ROOT / "data" / "glossary"
_PASSAGES = _DIR / "passages.jsonl"
_META = _DIR / "meta.json"
_S7_DEFAULT_DIR = _ROOT / "data" / "work" / "s7_1_approved_facts"
_ACCEPTED_RELEASE = _ROOT / "config" / "accepted_extraction.json"
#: S7.1 승인 사실은 용어 정의가 아니라 자기부담금 표에서 복원한 금액 사실이다.
#: 서비스명(통원·외래·처방조제)이 본문에 있다는 이유만으로 정의 질문에 섞지 않는다.
_S7_FACT_TERMS = ("자기부담금", "공제금액", "공제액")

_lock = threading.Lock()
_cache: list[TermPassage] | None = None
_meta_cache: dict | None = None


def _load() -> list[TermPassage]:
    """색인 전체를 한 번만 읽어 둔다(16MB · 구절 2,739개).

    ★요청마다 1,367문서를 훑지 않는다. 훑으면 한 번에 수십 초가 걸린다.
    """
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is not None:
            return _cache
        s7_present = any(path.exists() for path in _s7_paths())
        expected_s7_occurrences = _s7_expected_occurrences()
        s7_required = expected_s7_occurrences is not None
        if s7_required and not s7_present:
            raise InfraError(
                "현재 승인 릴리스에 필요한 S7 승인 사실 산출물이 없습니다."
            )
        if not _PASSAGES.exists() and not s7_present:
            raise InfraError("용어 색인과 S7 승인 사실 산출물이 모두 없습니다.")

        #: S7 금액 사실은 먼저 두되 `find()`에서 금액 사실 질문에만 노출한다.
        #: 통원·외래 같은 용어 정의 질문에 금액표가 앞서는 것을 막는다.
        s7_rows = _load_s7()
        if (
            expected_s7_occurrences is not None
            and len(s7_rows) != expected_s7_occurrences
        ):
            raise InfraError(
                "S7 승인 사실 산출물 건수가 승인 릴리스와 일치하지 않습니다."
            )
        rows: list[TermPassage] = s7_rows
        if _PASSAGES.exists():
            with _PASSAGES.open(encoding="utf-8") as f:
                for ln, line in enumerate(f, 1):
                    if not line.strip():
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError as e:
                        #: ★조용히 건너뛰지 않는다. 분모가 줄면 커버리지가 좋아 보인다.
                        raise InfraError(f"용어 색인 {ln}행이 깨졌습니다: {e}") from e
                    rows.append(_to_passage(d))
        _cache = rows
        return rows


def _to_passage(d: dict) -> TermPassage:
    return TermPassage(
        kind=d.get("kind") or "", sha256=d.get("sha256") or "",
        insurer=d.get("insurer") or "", qualified_no=d.get("qualified_no") or "",
        section=d.get("section") or "", title=d.get("title") or "",
        page_from=int(d.get("page_from") or 0), page_to=int(d.get("page_to") or 0),
        content_hash=d.get("content_hash") or "", text=d.get("text") or "",
    )


def _s7_dir() -> Path:
    configured = (os.getenv("S7_FACT_ROOT") or "").strip()
    return Path(configured) if configured else _S7_DEFAULT_DIR


def _s7_paths() -> tuple[Path, Path, Path]:
    root = _s7_dir()
    return tuple(root / name for name in (
        "approved_facts.jsonl", "chunks.jsonl", "occurrences.jsonl"
    ))


def _s7_expected_occurrences() -> int | None:
    if not _ACCEPTED_RELEASE.exists():
        #: 승인 설정 자체가 빠진 것을 과거 릴리스라고 추측해 레거시만 쓰지 않는다.
        #: 현재 배포가 어느 릴리스인지 모르는 상태이므로 명시적 장애다.
        raise InfraError("승인 추출 릴리스 설정이 없습니다.")
    try:
        config = json.loads(_ACCEPTED_RELEASE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InfraError("승인 추출 릴리스 설정을 읽을 수 없습니다.") from exc
    supplemental_ref = str(config.get("supplemental_facts") or "").strip()
    if not supplemental_ref:
        return None
    supplemental_path = Path(supplemental_ref)
    if not supplemental_path.is_absolute():
        supplemental_path = _ROOT / supplemental_path
    try:
        supplemental = json.loads(supplemental_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InfraError("S7 승인 사실 릴리스 설정을 읽을 수 없습니다.") from exc
    if (
        supplemental.get("release_state") != "accepted"
        or supplemental.get("serving_eligible") is not True
        or supplemental.get("citation_eligible") is not True
    ):
        raise InfraError("S7 승인 사실 릴리스가 serving/citation 승인 상태가 아닙니다.")
    expected = int((supplemental.get("materialized") or {}).get("occurrences") or 0)
    if expected < 1:
        raise InfraError("S7 승인 사실 릴리스의 기대 occurrence 수가 비어 있습니다.")
    return expected


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise InfraError(f"S7 승인 사실 산출물을 읽을 수 없습니다: {path.name}") from exc
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InfraError(
                f"S7 승인 사실 산출물이 깨졌습니다: {path.name}:{line_no}"
            ) from exc
        if not isinstance(row, dict):
            raise InfraError(
                f"S7 승인 사실 산출물 행이 객체가 아닙니다: {path.name}:{line_no}"
            )
        rows.append(row)
    return rows


def _load_s7() -> list[TermPassage]:
    """S7.1 승인 OCR 사실을 챗봇의 인용 가능한 구절로 연결한다."""
    facts_path, chunks_path, occurrences_path = _s7_paths()
    existing = [path.exists() for path in (facts_path, chunks_path, occurrences_path)]
    if any(existing) and not all(existing):
        missing = ", ".join(path.name for path, exists in zip(
            (facts_path, chunks_path, occurrences_path), existing
        ) if not exists)
        raise InfraError(f"S7 승인 사실 산출물이 일부만 배포됐습니다: {missing}")
    if not all(existing):
        return []

    facts: dict[str, dict] = {}
    for fact in _read_jsonl(facts_path):
        if fact.get("serving_eligible") and fact.get("citation_eligible"):
            facts[fact.get("content_hash")] = fact
    chunks: dict[str, str] = {}
    for chunk in _read_jsonl(chunks_path):
        chunks[chunk.get("content_hash")] = chunk.get("text") or ""

    rows: list[TermPassage] = []
    for occurrence in _read_jsonl(occurrences_path):
        fact = facts.get(occurrence.get("content_hash"))
        text = chunks.get(occurrence.get("content_hash"), "")
        if not fact or not text:
            continue
        services = fact.get("service") or []
        rows.append(
            TermPassage(
                kind="s7_approved_fact",
                sha256=(
                    fact.get("document_sha256") or fact.get("document_sha12") or ""
                ),
                insurer=occurrence.get("insurer") or fact.get("insurer") or "",
                qualified_no=str(
                    services[0] if services else fact.get("category") or "S7 fact"
                ),
                section="S7.1 승인 OCR 사실",
                title=fact.get("plan") or fact.get("category") or "승인 OCR 사실",
                page_from=int(
                    occurrence.get("page_from") or fact.get("page_1based") or 0
                ),
                page_to=int(
                    occurrence.get("page_to")
                    or occurrence.get("page_from")
                    or fact.get("page_1based")
                    or 0
                ),
                content_hash=occurrence.get("content_hash") or "",
                text=text,
            )
        )
    return rows


def find(term: str, *, insurer: str | None = None, limit: int = 20) -> list[TermPassage]:
    """용어가 들어 있는 정의 구절.

    ★부분 문자열로 찾는다. 형태소 분석이나 임베딩을 쓰지 않는다 —
      지금 필요한 것은 "약관에 이 낱말이 정의돼 있나"이고,
      그건 문자열 일치로 충분하며 **틀릴 여지가 없다.**
      의미 검색이 필요해지면 인덱스 A(pgvector)로 간다.

    ★`limit` 에 닿아도 **끝까지 센다.**

        처음엔 `limit` 에서 멈췄더니 `total_passages` 가 항상 200 이 나왔다.
        그건 개수가 아니라 상한인데 응답에는 "구절 200개"로 실렸고,
        `insurers` 도 훑다 만 순서에 따라 2개만 나왔다.
        구절이 2,739개뿐이라 전부 훑어도 문자열 검사 2,739번이다 — 셀 수 있으면 센다.
    """
    t = (term or "").strip()
    if not t:
        return []
    ins = (insurer or "").strip()
    out: list[TermPassage] = []
    for p in _load():
        if ins and p.insurer != ins:
            continue
        if p.kind == "s7_approved_fact":
            if not any(fact_term in t for fact_term in _S7_FACT_TERMS):
                continue
            #: "통원 자기부담금"처럼 범위를 함께 준 경우 두 낱말이 모두 있는
            #: 승인 사실만 남긴다. 일반 용어 경로의 정확 일치 규칙은 그대로 둔다.
            needles = [part for part in t.split() if len(part) >= 2]
            if needles and all(part in p.text for part in needles):
                out.append(p)
            continue
        if t in p.text:
            out.append(p)
    return out[:limit] if limit and len(out) > limit else out


def meta() -> dict:
    """색인을 무엇으로 언제 만들었나. **응답에 실어 나간다.**"""
    global _meta_cache
    if _meta_cache is None:
        _meta_cache = json.loads(_META.read_text(encoding="utf-8")) if _META.exists() else {}
        s7_count = sum(row.kind == "s7_approved_fact" for row in _load())
        if s7_count:
            _meta_cache = {**_meta_cache, "s7_approved_fact_passages": s7_count, "s7_serving": True}
        if not _meta_cache:
            raise InfraError("용어 색인 메타가 없습니다: data/glossary/meta.json 또는 S7_FACT_ROOT")
    return _meta_cache


def _reset_for_tests() -> None:
    """테스트에서 색인을 갈아 끼울 때만 쓴다."""
    global _cache, _meta_cache
    _cache = None
    _meta_cache = None


__all__ = ["find", "meta"]
