"""확정된 한 약관 안에서만 판정 근거를 묶는다.

검색 계약: ``docs/handoff/04_계약_AI1_검색.md``.

이 모듈은 결론을 만들지 않는다. KCD 규칙이 실제로 나온 조항, 사용자의 질문과 맞는
조항, 그 조항이 명시적으로 가리킨 조항을 모아 ``EvidenceBundleV1``으로 반환한다.
다른 약관·불완전 문서·인용 불가 조항은 비슷해 보여도 사용하지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

from app.core.domain import kcd_ranges as kcd
from app.core.errors import ValidationErr
from app.core.ports.precheck import ClauseRow


RETRIEVER_VERSION = "evidence-v1-lexical-semantic-rrf-ref3"
_REFERENCE = re.compile(r"제\s*(\d{1,3})\s*조(?:\s*의\s*(\d{1,2}))?")
_RRF_K = 60
_MAX_REFERENCE_DEPTH = 3


@dataclass(frozen=True)
class ReferencePath:
    """한 조항에서 다른 조항을 따라간 기록.

    후보가 0개이거나 둘 이상이면 ``resolved``가 거짓이다. 임의로 하나를 고르지 않는다.
    """

    source_clause_id: str
    cited_number: str
    matched_clause_ids: tuple[str, ...] = ()
    depth: int = 1
    resolved: bool = False


@dataclass(frozen=True)
class EvidenceBundleV1:
    policy_version_sha: str
    clauses: list[ClauseRow] = field(default_factory=list)
    code_rules: list[kcd.CodeMention] = field(default_factory=list)
    reference_paths: list[ReferencePath] = field(default_factory=list)
    unresolved_references: list[str] = field(default_factory=list)
    retriever_version: str = RETRIEVER_VERSION
    truncated: list[str] = field(default_factory=list)


def _norm_number(value: str) -> str:
    tail = (value or "").rsplit("/", 1)[-1]
    match = _REFERENCE.search(tail)
    if not match:
        return re.sub(r"\s+", "", tail)
    return f"제{match.group(1)}조" + (f"의{match.group(2)}" if match.group(2) else "")


def _eligible(row: ClauseRow, sha256: str) -> bool:
    """문서와 조항의 두 안전문을 모두 통과한 행만 허용한다."""

    return bool(
        row.sha256 == sha256
        and row.usable
        and row.parse_status == "ok"
        and row.citation_eligible is True
        and row.chunk_type != "page_fallback"
    )


def _semantic_rows(result, by_key: dict[tuple[str, str], ClauseRow]) -> list[ClauseRow]:
    """의미검색 결과를 독립 원문 행으로 다시 해소한다.

    의미검색 hit 자체를 근거로 믿지 않는다. 현재 약관 저장소에서 같은 내용 해시와 조항
    번호를 가진 행을 다시 찾아야 한다.
    """

    hits = getattr(result, "hits", result) or []
    out: list[ClauseRow] = []
    seen: set[str] = set()
    for hit in hits:
        key = (str(getattr(hit, "content_hash", "")), str(getattr(hit, "qualified_no", "")))
        row = by_key.get(key)
        # 같은 원문을 중복 반환한 검색 어댑터 때문에 RRF 점수가 두 번 올라가면
        # 순위가 조작된다. 저장소에서 다시 찾은 조항 ID 기준으로 한 번만 받는다.
        if row is not None and row.clause_id not in seen:
            out.append(row)
            seen.add(row.clause_id)
    return out


def _rrf(*rankings: Iterable[ClauseRow]) -> list[ClauseRow]:
    """두 검색 순위를 RRF로 합친다. 점수가 아니라 순위만 사용한다."""

    scores: dict[str, float] = {}
    rows: dict[str, ClauseRow] = {}
    first_seen: dict[str, int] = {}
    seen_ix = 0
    for ranking in rankings:
        for rank, row in enumerate(ranking, 1):
            key = row.clause_id
            if key not in first_seen:
                first_seen[key] = seen_ix
                seen_ix += 1
            rows[key] = row
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
    return [
        rows[key]
        for key in sorted(scores, key=lambda item: (-scores[item], first_seen[item]))
    ]


def retrieve(
    *,
    policy_version_sha: str,
    kcd_codes: list[str],
    question: str | None = None,
    top_k: int = 8,
    clauses,
    semantic_search: Callable[..., object] | None = None,
) -> EvidenceBundleV1:
    """확정된 약관 한 벌에서만 근거를 찾는다.

    ``semantic_search``는 선택 주입점이다. 제공되지 않으면 낱말 검색만 사용하며, 제공된
    경우에도 결과를 약관 저장소 원문으로 다시 해소한 뒤 RRF로 합친다. 검색 실패를 다른
    약관이나 전체 조항으로 메우지 않는다.
    """

    sha = (policy_version_sha or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValidationErr("policy_version_sha는 64자리 SHA-256이어야 합니다")
    if top_k < 1:
        raise ValidationErr("top_k는 1 이상이어야 합니다")

    parsed_codes: list[kcd.CodeRef] = []
    for raw in kcd_codes:
        parsed = kcd.CodeRef.parse(raw)
        if parsed is None:
            raise ValidationErr(f"올바르지 않은 KCD 코드입니다: {raw}")
        parsed_codes.append(parsed)

    stats = clauses.stats(sha)
    if stats.get("parse_status") != "ok":
        raise ValidationErr(
            f"약관 문서 경계를 신뢰할 수 없습니다: parse_status={stats.get('parse_status', 'unknown')}"
        )

    loaded = list(clauses.load_clauses(sha, usable_only=True))
    usable = [row for row in loaded if _eligible(row, sha)]
    truncated: list[str] = []
    rejected = len(loaded) - len(usable)
    if rejected:
        truncated.append(f"안전 관문을 통과하지 못한 조항 {rejected}개 제외")

    by_key = {(row.content_hash, row.qualified_no): row for row in usable}
    by_number: dict[str, list[ClauseRow]] = {}
    for row in usable:
        by_number.setdefault(_norm_number(row.qualified_no), []).append(row)

    code_rules: list[kcd.CodeMention] = []
    rule_rows: list[ClauseRow] = []
    for row in usable:
        relevant = [
            mention
            for mention in kcd.scan_clause(row.text)
            if any(mention.range.contains(code) for code in parsed_codes)
        ]
        if relevant:
            code_rules.extend(relevant)
            rule_rows.append(row)

    query = (question or "").strip()
    lexical: list[ClauseRow] = []
    semantic: list[ClauseRow] = []
    if query:
        lexical = [
            row for row in clauses.search(sha, query, limit=max(top_k * 2, 20))
            if _eligible(row, sha)
        ]
        if semantic_search is not None:
            semantic_result = semantic_search(
                policy_version_sha=sha,
                question=query,
                top_k=max(top_k * 2, 20),
            )
            semantic = [
                row for row in _semantic_rows(semantic_result, by_key)
                if _eligible(row, sha)
            ]

    ranked = _rrf(lexical, semantic) if query else []
    if len(ranked) > top_k:
        truncated.append(f"검색 후보 {len(ranked) - top_k}개를 top_k={top_k} 뒤에서 잘랐습니다")
    ranked = ranked[:top_k]

    selected: dict[str, ClauseRow] = {}
    for row in [*rule_rows, *ranked]:
        selected.setdefault(row.clause_id, row)

    reference_paths: list[ReferencePath] = []
    unresolved: list[str] = []
    queue: list[tuple[ClauseRow, int]] = [(row, 1) for row in selected.values()]
    visited_sources: set[tuple[str, int]] = set()
    while queue:
        source, depth = queue.pop(0)
        if depth > _MAX_REFERENCE_DEPTH or (source.clause_id, depth) in visited_sources:
            continue
        visited_sources.add((source.clause_id, depth))
        own = _norm_number(source.qualified_no)
        for match in _REFERENCE.finditer(source.text or ""):
            cited = f"제{match.group(1)}조" + (f"의{match.group(2)}" if match.group(2) else "")
            if cited == own:
                continue
            candidates = by_number.get(cited, [])
            resolved = len(candidates) == 1
            path = ReferencePath(
                source_clause_id=source.clause_id,
                cited_number=cited,
                matched_clause_ids=tuple(row.clause_id for row in candidates),
                depth=depth,
                resolved=resolved,
            )
            reference_paths.append(path)
            if not resolved:
                why = "없음" if not candidates else f"후보 {len(candidates)}개"
                unresolved.append(f"{source.qualified_no} → {cited} ({why})")
                continue
            target = candidates[0]
            if target.clause_id not in selected:
                selected[target.clause_id] = target
                queue.append((target, depth + 1))

    return EvidenceBundleV1(
        policy_version_sha=sha,
        clauses=list(selected.values()),
        code_rules=code_rules,
        reference_paths=reference_paths,
        unresolved_references=list(dict.fromkeys(unresolved)),
        retriever_version=RETRIEVER_VERSION,
        truncated=truncated,
    )


__all__ = ["EvidenceBundleV1", "ReferencePath", "RETRIEVER_VERSION", "retrieve"]
