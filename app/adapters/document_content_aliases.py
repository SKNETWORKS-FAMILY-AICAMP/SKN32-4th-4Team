"""사람이 확정한 동일 원문 재출력본을 대표 문서에 연결한다.

파일 SHA가 다르면 조항 ``content_hash``만으로는 레이아웃·줄바꿈이 다른 재출력본을
안정적으로 합칠 수 없다. 이 원장은 사람 검토가 끝난 문서 쌍만 담고, 서비스·색인
경로는 별칭 문서의 occurrence를 노출하지 않는다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping


_ROOT = Path(__file__).resolve().parents[2]
_ALIAS_LEDGER = _ROOT / "config" / "document_content_aliases.jsonl"
_CONFIRMED_LEDGER = _ROOT / "config" / "confirmed_documents.jsonl"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ContentAliasError(RuntimeError):
    """콘텐츠 별칭 원장이 불완전하거나 확정 원장과 충돌한다."""


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        raise ContentAliasError(f"문서 별칭 원장을 찾지 못했습니다: {path}")
    out: list[dict] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContentAliasError(
                f"문서 별칭 원장 JSON 오류: {path}:{line_no}: {exc}"
            ) from exc
        if not isinstance(row, dict):
            raise ContentAliasError(f"문서 별칭 원장 행이 객체가 아닙니다: {path}:{line_no}")
        out.append(row)
    return out


def load(
    path: Path = _ALIAS_LEDGER,
    *,
    confirmed_path: Path = _CONFIRMED_LEDGER,
    validate_confirmed: bool = True,
) -> dict[str, str]:
    """``alias_sha256 -> canonical_sha256``를 검증해 돌려준다.

    색인 생성기는 대표본이 확정 원장에 있고 별칭은 없다는 조건까지 검사한다.
    런타임 파일 로더는 확정 원장이 배포되지 않는 구성도 있으므로 구조 검증만 수행하되,
    별칭 원장 자체가 없거나 깨졌으면 실패한다.
    """

    aliases: dict[str, str] = {}
    for line_no, row in enumerate(_rows(path), 1):
        alias = str(row.get("alias_sha256") or "")
        canonical = str(row.get("canonical_sha256") or "")
        if not _SHA256.fullmatch(alias) or not _SHA256.fullmatch(canonical):
            raise ContentAliasError(
                f"문서 별칭 SHA-256은 소문자 64자리여야 합니다: {path}:{line_no}"
            )
        if alias == canonical:
            raise ContentAliasError(f"대표본과 별칭이 같습니다: {path}:{line_no}")
        if row.get("status") != "confirmed":
            raise ContentAliasError(
                f"사람 확정이 아닌 별칭은 적용할 수 없습니다: {path}:{line_no}"
            )
        if row.get("relation") != "same_legal_content_reprint":
            raise ContentAliasError(f"지원하지 않는 별칭 관계입니다: {path}:{line_no}")
        if alias in aliases:
            raise ContentAliasError(f"별칭 SHA가 중복되었습니다: {alias}")
        aliases[alias] = canonical

    chained = sorted(set(aliases.values()) & set(aliases))
    if chained:
        raise ContentAliasError(
            "대표본이 다시 별칭인 연쇄 관계는 허용하지 않습니다: " + ", ".join(chained)
        )
    if not validate_confirmed:
        return aliases

    confirmed: set[str] = set()
    if not confirmed_path.is_file():
        raise ContentAliasError(f"확정 문서 원장을 찾지 못했습니다: {confirmed_path}")
    for line_no, line in enumerate(
        confirmed_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContentAliasError(
                f"확정 문서 원장 JSON 오류: {confirmed_path}:{line_no}: {exc}"
            ) from exc
        sha = str(row.get("sha256") or "")
        if row.get("identification") == "confirmed" and _SHA256.fullmatch(sha):
            confirmed.add(sha)

    missing = sorted(set(aliases.values()) - confirmed)
    if missing:
        raise ContentAliasError("대표본이 확정 문서 원장에 없습니다: " + ", ".join(missing))
    wrongly_confirmed = sorted(set(aliases) & confirmed)
    if wrongly_confirmed:
        raise ContentAliasError(
            "별칭 문서가 확정 문서 원장에도 등록되어 있습니다: "
            + ", ".join(wrongly_confirmed)
        )
    return aliases


def is_alias_sha(sha256: str, aliases: Mapping[str, str]) -> bool:
    """64자 SHA와 S7 fact의 12자 SHA 모두 별칭인지 판별한다."""

    value = str(sha256 or "").lower()
    if len(value) == 64:
        return value in aliases
    if len(value) == 12:
        return any(alias.startswith(value) for alias in aliases)
    return False


def prune_occurrences(
    conn,
    aliases: Mapping[str, str],
    *,
    generation: str,
) -> int:
    """현재 색인 세대에서 별칭 문서 occurrence를 제거한다."""

    alias_shas = sorted(aliases)
    if not alias_shas:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM policy_clause_occurrence "
            "WHERE sha256 = ANY(%s) AND index_generation = %s",
            (alias_shas, generation),
        )
        removed = cur.rowcount
    conn.commit()
    return removed


def ensure_canonicals_present(
    aliases: Mapping[str, str],
    available_shas,
    *,
    context: str,
) -> None:
    """별칭 산출물이 있는 세대에는 대표본 산출물도 반드시 있어야 한다."""

    available = set(available_shas)
    missing = sorted(
        canonical
        for alias, canonical in aliases.items()
        if alias in available and canonical not in available
    )
    if missing:
        raise ContentAliasError(
            f"{context}에 별칭 대표본 산출물이 없습니다: " + ", ".join(missing)
        )


__all__ = [
    "ContentAliasError",
    "ensure_canonicals_present",
    "is_alias_sha",
    "load",
    "prune_occurrences",
]
