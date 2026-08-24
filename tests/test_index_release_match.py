"""색인이 승인 릴리스와 어긋날 때 **조용히 0건을 돌려주지 않는가.**

★왜 필요한가 — 실제로 그러고 있었다(실측 2026-08-03).

    승인 릴리스가 가리키는 값과 DB 에 든 값이 이랬다.

        승인 index_generation = 's5'
        DB 실제              = 's5-mixed' 158,186 · 's6' 195,617   ← 's5' 는 0건
        승인 embed_model     = ''        (embed_profile 이 비어 있음)
        DB 실제              = 'jhgan/ko-sroberta-multitask@128' 46,385

    두 필터가 **동시에** 아무것도 안 맞았다. 그런데 `search()` 는 빈 목록을
    돌려줬다 — 호출자는 "그런 조항이 없다"로 읽는다. 근거가 없는 것과
    필터가 안 맞는 것이 **같은 모양**으로 나갔다.

    기본 저장소가 `file` 이라 판정이 당장 깨지진 않았다. 하지만
    `CLAUSE_STORE=pg` 는 **환경변수 하나 차이**다.
"""

from __future__ import annotations

import pytest

from app.adapters import pgvector_clause_index as ix
from app.core.errors import InfraError


class _Cur:
    """`index_state()` 가 던지는 두 질의에만 답하는 최소 커서."""

    def __init__(self, gens: dict, models: dict, bad_sha: int = 0,
                 with_vec: int | None = None) -> None:
        self._gens, self._models, self._rows = gens, models, []
        self._bad_sha = bad_sha
        #: ★기본값은 **승인 세대 발생 전부에 벡터가 있다**로 둔다. 그래야
        #:   벡터 유무를 다루지 않는 기존 시험이 이 축을 신경 쓰지 않아도 된다.
        self._with_vec = with_vec

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, *args):
        #: ★깨진 sha 개수 질의는 스칼라 하나를 돌려준다.
        if "length(sha256)" in sql:
            self._rows = [(self._bad_sha,)]
        #: ★★**이 분기가 아래 `policy_clause_occurrence` 보다 먼저 와야 한다.**
        #:   벡터 보유 수 질의도 그 테이블을 읽으므로, 순서를 뒤집으면
        #:   세대별 집계 결과가 스칼라 자리로 돌아와 조용히 튜플이 섞인다.
        elif "EXISTS" in sql:
            gen = args[0][0] if args and args[0] else ""
            total = self._gens.get(gen, 0)
            self._rows = [(total if self._with_vec is None else self._with_vec,)]
        elif "policy_clause_occurrence" in sql:
            self._rows = list(self._gens.items())
        else:
            self._rows = list(self._models.items())

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, gens: dict, models: dict, bad_sha: int = 0,
                 with_vec: int | None = None) -> None:
        self._gens, self._models, self._bad_sha = gens, models, bad_sha
        self._with_vec = with_vec

    def cursor(self):
        return _Cur(self._gens, self._models, self._bad_sha, self._with_vec)


@pytest.fixture
def _release(monkeypatch):
    """승인 릴리스가 무엇을 가리키는지 시험마다 정한다."""

    def _set(gen: str, model: str):
        monkeypatch.setattr(ix, "current_generation", lambda: gen)
        monkeypatch.setattr(ix, "current_embed_model", lambda: model)

    return _set


def test_실측_상태를_그대로_재현하면_막힌다(_release):
    """2026-08-03 실측값 그대로. **예외가 나야 한다.**"""
    _release("s5", "")
    conn = _Conn({"s6": 195617, "s5-mixed": 158186},
                 {"jhgan/ko-sroberta-multitask@128": 46385})
    with pytest.raises(InfraError) as e:
        ix.ensure_index_matches_release(conn)
    msg = str(e.value)
    #: ★메시지가 **실제 숫자**를 말해야 한다. "색인 오류"만으로는 못 고친다.
    assert "s5" in msg and "s6" in msg and "195617" in msg
    assert "embed_profile" in msg


def test_s5_mixed_를_s5_로_읽지_않는다(_release):
    """★`s5-mixed` 는 **컬럼 기본값**이다 — 세대 불명이라는 뜻이다.

    's5' 로 갈아 끼우면 세대 불명 158,186행이 승인 세대로 둔갑한다.
    """
    _release("s5", "m")
    conn = _Conn({"s5-mixed": 158186}, {"m": 100})
    with pytest.raises(InfraError) as e:
        ix.ensure_index_matches_release(conn)
    assert "기본값" in str(e.value)
    assert ix.index_state(conn)["occurrences_for_wanted"] == 0


def test_세대는_맞는데_모델이_없으면_막힌다(_release):
    """한쪽만 맞아도 검색은 0건이다."""
    _release("s6", "새모델")
    conn = _Conn({"s6": 195617}, {"jhgan/ko-sroberta-multitask@128": 46385})
    with pytest.raises(InfraError) as e:
        ix.ensure_index_matches_release(conn)
    assert "새모델" in str(e.value) and "0건" in str(e.value)


def test_모델은_맞는데_세대가_없으면_막힌다(_release):
    _release("s7", "m")
    conn = _Conn({"s6": 10}, {"m": 10})
    with pytest.raises(InfraError):
        ix.ensure_index_matches_release(conn)


def test_둘_다_맞으면_통과한다(_release):
    """★막기만 하고 통과를 못 하면 그것도 고장이다."""
    _release("s6", "m")
    conn = _Conn({"s6": 195617}, {"m": 46385})
    ix.ensure_index_matches_release(conn)  # 예외 없음
    assert ix.index_state(conn)["ready"] is True


def test_현황이_설정이_아니라_DB_를_센다(_release):
    """`index_state()` 는 **DB 에 실제로 든 것**을 그대로 보여준다."""
    _release("s5", "")
    conn = _Conn({"s6": 3, "s5-mixed": 2}, {"x": 1})
    st = ix.index_state(conn)
    assert st["generations_in_db"] == {"s6": 3, "s5-mixed": 2}
    assert st["embed_models_in_db"] == {"x": 1}
    assert st["wanted_generation"] == "s5"
    assert st["ready"] is False


def test_검색_경로가_이_검사를_실제로_부른다():
    """★검사를 만들어 두고 **안 부르면** 아무것도 안 막힌다.

    앞서 정적 UI 차단 목록이 그랬다 — 목록만 있고 파일이 없었다.
    """
    import inspect

    from app.adapters import pg_clause_store as st

    for fn in (ix.search, st.search):
        assert "ensure_index_matches_release" in inspect.getsource(fn), (
            f"{fn.__module__}.{fn.__name__} 이 색인-릴리스 검사를 부르지 않습니다"
        )


def test_sha256_이_64자가_아니면_준비됐다고_하지_않는다(_release):
    """★세대·모델만 보면 `ready:true` 인데 조회가 전부 실패하는 상태가 있었다.

    실측 2026-08-03 — 적재가 `p.stem` 을 써서 `"…clauses"`(20자)를 넣었다.
    파일 저장소는 64자 sha 를 받아 앞 12자로 찾으므로 짝이 안 맞았고,
    PG 경로가 통째로 죽었는데도 **준비됐다고 말하고 있었다.**
    """
    _release("s6", "m")
    conn = _Conn({"s6": 100}, {"m": 100}, bad_sha=186_094)
    st = ix.index_state(conn)
    assert st["occurrences_with_bad_sha"] == 186_094
    assert st["ready"] is False
    with pytest.raises(InfraError) as e:
        ix.ensure_index_matches_release(conn)
    assert "64자" in str(e.value) and "186,094" in str(e.value)


def test_sha256_이_전부_64자면_통과한다(_release):
    _release("s6", "m")
    conn = _Conn({"s6": 100}, {"m": 100}, bad_sha=0)
    assert ix.index_state(conn)["ready"] is True
    ix.ensure_index_matches_release(conn)


def test_벡터_없는_발생을_발생_수에_섞어_보고하지_않는다(_release):
    """★**검색 가능 범위가 부풀어 보이던 것.**

    실측 2026-08-04 — `occurrences_for_wanted` 가 209,883 이라 20만 건이 다
    찾아지는 것처럼 읽혔다. 실제로 벡터가 있는 것은 **189,306** 이고
    나머지 20,577행은 이전 s6 shadow 적재분이라 **검색에 안 걸린다.**

    누수는 아니다 — 그 행들은 게이트 값이 `NULL` 이라 `eligibility` 가 막는다.
    그래도 수를 **갈라서** 내보낸다. 상태 보고가 실제와 어긋나는 것이
    이 프로젝트에서 되풀이된 사고 유형이다(CLAUDE.md §0).
    """
    _release("s6", "m")
    conn = _Conn({"s6": 209_883}, {"m": 122_697}, with_vec=189_306)
    st = ix.index_state(conn)
    assert st["occurrences_for_wanted"] == 209_883
    assert st["occurrences_with_vector"] == 189_306
    assert st["occurrences_without_vector"] == 20_577
    #: ★막지는 않는다. 게이트가 이미 거르므로 **미준비가 아니다** —
    #:   여기서 false 로 만들면 안전한 상태를 고장으로 보고하는 것이 된다.
    assert st["ready"] is True


def test_전부_벡터가_있으면_사각지대가_0이다(_release):
    _release("s6", "m")
    st = ix.index_state(_Conn({"s6": 100}, {"m": 100}))
    assert st["occurrences_with_vector"] == 100
    assert st["occurrences_without_vector"] == 0
