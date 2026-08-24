"""승인된 산출물 릴리스 — **세대를 정하는 단 하나의 자리.**

★왜 이게 필요했나

    세대를 정하는 곳이 **셋**이었고 서로 어긋났다(실측 2026-08-03).

        config/accepted_extraction.json  tag='s5_pymupdf-1.28.0'  → 파일 저장소(기본 serving)
        build_clause_index.py            _CLAUSE_TAG='s6_'        → 적재
        pgvector_clause_index.py         CURRENT_GENERATION='s6'  → 검색

    판정이 읽는 파일 저장소는 **s5**(부록 분리 전)이고 벡터 경로는 **s6**(미승인)이었다.
    같은 질문에 두 경로가 **다른 조항**을 준다.

★설계 — 승인 포인터 하나 + 불변 릴리스 명세 + 모든 경로에 명시적 주입 (코덱스)

    · `index_generation` 을 설정에 **따로 저장하지 않는다.** `clause_tag` 에서 파생한다.
      중복 필드를 두면 **다시 어긋난다.**
    · 코드에 문자열 상수를 두지 않는다. 지금 문제의 원인이 정확히 그것이다.
    · `release_id` 를 둔다 — 페이지 스키마도 `s5` 로 올라가서
      `s5_pymupdf-1.28.0` 이 **어느 층 이야기인지 이름만으로는 알 수 없다.**

★승인 전에는 새 세대를 서빙하지 않는다

    후보 세대를 shadow 로 미리 적재하는 것은 된다. **serving 포인터는 승인 후에만** 바꾼다.
    그러면 지금은 검색이 0건이 된다 — **그게 정직한 상태다**(CLAUDE.md §0).
"""

from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass, field

from app.core.errors import ConfigError

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_FILE = _ROOT / "config" / "accepted_extraction.json"
_STRUCTURED = _ROOT / "data" / "structured"

#: `s6_pymupdf-1.28.0` → `s6`
_GEN = re.compile(r"^(s\d+)_")


@dataclass(frozen=True)
class EmbedProfile:
    """벡터를 만든 조건. **이름만으로는 부족하다.**

    같은 모델이라도 `max_seq_length` 나 청킹 예산이 다르면 벡터 공간이 달라진다.
    실측 사고: `jhgan/ko-sroberta-multitask` 는 `max_seq_length=128` 인데
    청킹 예산이 448 이라 조각의 89.1% 가 잘린 채 임베딩됐다.
    """

    model: str = ""
    revision: str = ""
    dim: int = 0
    max_seq_length: int = 0
    chunk_budget: int = 0
    overlap: int = 0

    @property
    def key(self) -> str:
        """벡터 행에 박는 이름. **설정이 다르면 다른 이름이 나와야 한다.**

        ★처음엔 `model@max_seq_length` 만 썼다. 그러면 revision·차원·청킹 예산이
          바뀌어도 **같은 이름**이라, 옛 벡터를 "이미 있음"으로 보고 건너뛴다
          (코덱스 라운드2 지적). 프로필을 이루는 값을 **전부** 넣는다.
        """
        if not self.model:
            return ""
        parts = [
            self.model,
            self.revision or "-",
            f"d{self.dim or 0}",
            f"L{self.max_seq_length or 0}",
            f"c{self.chunk_budget or 0}",
            f"o{self.overlap or 0}",
        ]
        return "|".join(parts)

    @property
    def is_set(self) -> bool:
        return bool(self.model)


@dataclass(frozen=True)
class AcceptedRelease:
    """판정에 쓸 산출물 한 벌. **불변**이다 — 읽는 쪽이 바꿀 수 없다."""

    release_id: str
    page_tag: str
    clause_tag: str
    document_count: int
    embed_profile: EmbedProfile
    accepted_at: str = ""
    accepted_by: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def index_generation(self) -> str:
        """`clause_tag` 에서 **파생**한다. 설정에 따로 적지 않는다."""
        m = _GEN.match(self.clause_tag)
        if not m:
            raise ConfigError(
                f"clause_tag 에서 세대를 읽을 수 없습니다: {self.clause_tag!r}. "
                "`s6_pymupdf-1.28.0` 처럼 `s<숫자>_` 로 시작해야 합니다."
            )
        return m.group(1)

    def clause_dir_glob(self) -> str:
        return f"*/{self.clause_tag}"

    def ensure_ready(self) -> None:
        """산출물이 **온전히** 있는지 본다. 없으면 실패한다.

        ★"폴더 하나 존재"로 판단하지 않는다(코덱스). 일부만 있는 상태가
          가장 위험하다 — 판정이 "그 약관엔 그런 조항이 없다"고 답한다.
        """
        dirs = list(_STRUCTURED.glob(self.clause_tag and self.clause_dir_glob() or ""))
        if not dirs:
            raise ConfigError(
                f"승인된 조항 산출물이 없습니다: {self.clause_tag}\n"
                "`python -m scripts.extract.run_all` 을 돌리거나 "
                f"{_FILE.name} 의 `clause_tag` 를 고치세요."
            )
        n = sum(1 for d in dirs for _ in d.glob("*.clauses.json"))
        if self.document_count and n != self.document_count:
            raise ConfigError(
                f"승인된 릴리스 {self.release_id!r} 의 문서 수가 맞지 않습니다: "
                f"설정 {self.document_count:,} · 디스크 {n:,}.\n"
                "★일부만 있는 상태로 판정하면 '그런 조항이 없다'고 잘못 답합니다."
            )


def load(path: pathlib.Path | None = None) -> AcceptedRelease:
    """승인 파일을 읽는다. **폴백하지 않는다.**

    ★'가장 최신'을 자동으로 고르지 않는다 — 최신이 더 낫다는 보장이 없다.
      실제로 s6 는 s5 보다 낫지만 **사람의 승인 절차를 안 거쳤다.**
    """
    p = path or _FILE
    if not p.exists():
        raise ConfigError(
            f"판정에 쓸 산출물 릴리스가 지정되지 않았습니다: {p}\n"
            '예: {"release_id": "...", "clause_tag": "s5_pymupdf-1.28.0", ...}'
        )
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"릴리스 설정을 읽을 수 없습니다: {e}") from e

    #: ★`tag` 는 옛 이름이다. 다른 세션이 아직 읽고 있을 수 있어 **별칭으로 남긴다.**
    clause_tag = (cfg.get("clause_tag") or cfg.get("tag") or "").strip()
    if not clause_tag:
        raise ConfigError(f"{p.name} 에 `clause_tag`(또는 옛 이름 `tag`)가 비어 있습니다.")

    ep = cfg.get("embed_profile") or {}
    return AcceptedRelease(
        release_id=(cfg.get("release_id") or f"unnamed-{clause_tag}").strip(),
        page_tag=(cfg.get("page_tag") or "").strip(),
        clause_tag=clause_tag,
        document_count=int(cfg.get("document_count") or 0),
        embed_profile=EmbedProfile(
            model=(ep.get("model") or "").strip(),
            revision=(ep.get("revision") or "").strip(),
            dim=int(ep.get("dim") or 0),
            max_seq_length=int(ep.get("max_seq_length") or 0),
            chunk_budget=int(ep.get("chunk_budget") or 0),
            overlap=int(ep.get("overlap") or 0),
        ),
        accepted_at=(cfg.get("accepted_at") or "").strip(),
        accepted_by=(cfg.get("accepted_by") or "").strip(),
        raw=cfg,
    )


#: ── 요청 단위 스냅샷 ──────────────────────────────────────────────
#:
#: ★★**한 요청 안에서는 릴리스를 한 번만 읽는다.**
#:
#:   `current_generation()` 과 `current_embed_model()` 이 각각 `load()` 를 부르면,
#:   그 사이에 승인 포인터가 바뀌었을 때 **서로 다른 릴리스가 조합된다**
#:   (세대는 새 것, 모델은 옛 것). 전환이 요청 단위로 원자적이지 않다(코덱스 라운드2).
#:
#:   ★프로세스 전역에 캐시하지 않는다. 그러면 전환이 **영영 반영되지 않는다.**
#:     범위를 명시적으로 여는 곳(요청 처리·적재 한 판)에서만 굳힌다.
import contextvars

_SNAPSHOT: contextvars.ContextVar["AcceptedRelease | None"] = contextvars.ContextVar(
    "accepted_release_snapshot", default=None
)


class pinned:
    """이 블록 안에서 릴리스를 **한 벌로 고정**한다.

        with release.pinned():
            ...   # 세대·프로필이 도중에 바뀌지 않는다
    """

    def __init__(self, rel: "AcceptedRelease | None" = None) -> None:
        self._rel = rel
        self._token = None

    def __enter__(self) -> "AcceptedRelease":
        rel = self._rel or load()
        self._token = _SNAPSHOT.set(rel)
        return rel

    def __exit__(self, *exc) -> None:
        if self._token is not None:
            _SNAPSHOT.reset(self._token)
        return None


def current() -> AcceptedRelease:
    """지금 볼 릴리스. `pinned()` 안이면 **그 스냅샷**을, 밖이면 새로 읽는다."""
    snap = _SNAPSHOT.get()
    return snap if snap is not None else load()


__all__ = ["AcceptedRelease", "EmbedProfile", "load", "current", "pinned"]
