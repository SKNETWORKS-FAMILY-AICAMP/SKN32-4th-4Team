"""질의를 조항 색인과 **같은 공간으로** 옮긴다.

★이 파일이 막으려는 사고 (2026-08-04)
  색인은 조항 본문을 접두사 없이(`doc_prefix=""`), 질의는 `"query: "` 를 붙여
  인코딩하도록 만들어졌다. 서비스가 그걸 모르고 맨 질의를 인코딩하면
  **오류 없이 틀린 조항이 올라온다.** 예외도 안 나고 로그도 안 남는다.
  근거를 잘못 대는 서비스에서 이건 가장 나쁜 실패다(CLAUDE.md §0).

★그래서 접두사를 **기본값으로 때우지 않는다.** 승인 릴리스에 없으면 멈춘다.
  릴리스 값은 `scripts/index/sync_embed_profile.py` 가 적재 매니페스트에서
  검증해 채운다 — 사람이 베끼지 않는다.

★모델은 첫 질의에서 받아온다. import 만으로 무게추를 내려받으면
  기동·테스트가 GPU 와 네트워크에 묶인다.
"""

from __future__ import annotations

import threading

from app.core.errors import InfraError

#: 채점·인코딩에 넣는 질의 길이 상한. 사람이 치는 질문은 이보다 짧다.
#: 길이를 안 막으면 토크나이저 시간이 요청 시간을 지배한다.
MAX_QUERY_CHARS = 512

_REQUIRED = ("model", "revision", "dim", "query_prefix", "max_seq_length")


class ClauseQueryEmbedder:
    """승인 릴리스의 `embed_profile` 그대로 질의를 인코딩한다."""

    def __init__(self, profile: dict, *, model=None) -> None:
        missing = [k for k in _REQUIRED if profile.get(k) in (None, "")]
        #: `doc_prefix`·`query_prefix` 는 빈 문자열이 **정상값**이라 따로 본다.
        if "query_prefix" not in profile:
            missing.append("query_prefix")
        if missing:
            raise InfraError(
                "승인 릴리스의 embed_profile 이 불완전하다: "
                f"{sorted(set(missing))}. 질의를 어떻게 인코딩할지 모르는 채로 검색하면 "
                "색인과 다른 공간에서 찾게 된다. "
                "`python -m scripts.index.sync_embed_profile` 로 채운 뒤 다시 시도한다."
            )
        self._profile = dict(profile)
        self._model = model
        self._lock = threading.Lock()

    @property
    def profile_key(self) -> str:
        """관측·재현용 식별자. 응답에 실어 어느 프로필로 찾았는지 남긴다."""
        p = self._profile
        return f"{p['model']}@{p['revision'][:12]}|d{p['dim']}|L{p['max_seq_length']}"

    def _get_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                m = SentenceTransformer(
                    self._profile["model"],
                    revision=self._profile["revision"],
                )
                m.max_seq_length = int(self._profile["max_seq_length"])
                self._model = m
        return self._model

    def encode(self, query: str):
        q = (query or "").strip()
        if not q:
            raise InfraError("빈 질의는 인코딩하지 않는다")
        if len(q) > MAX_QUERY_CHARS:
            raise InfraError(f"질의가 너무 길다({len(q)}자 > {MAX_QUERY_CHARS})")

        import numpy as np

        vec = self._get_model().encode(
            [self._profile["query_prefix"] + q],
            convert_to_numpy=True,
            normalize_embeddings=bool(self._profile.get("normalized", True)),
        )
        vec = np.asarray(vec).reshape(-1)

        #: ★차원이 다르면 **다른 색인**이다. 조용히 넘기면 DB 가 이상한 오류를 낸다.
        if vec.shape[0] != int(self._profile["dim"]):
            raise InfraError(
                f"질의 벡터 차원이 프로필과 다르다: {vec.shape[0]} != {self._profile['dim']}"
            )
        if not np.isfinite(vec).all():
            raise InfraError("질의 벡터에 유한하지 않은 값이 있다")
        return vec


def build() -> ClauseQueryEmbedder:
    """승인 릴리스에서 프로필을 읽어 임베더를 만든다."""
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    cfg = json.loads((root / "config" / "accepted_extraction.json").read_text(encoding="utf-8"))
    return ClauseQueryEmbedder(cfg.get("embed_profile") or {})
