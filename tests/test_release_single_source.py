"""세대는 **한 곳에서만** 정해진다 — 승인 릴리스.

★왜 이 테스트가 있나

    세대를 정하는 곳이 셋이었고 서로 어긋났다(실측 2026-08-03):

        config/accepted_extraction.json  tag='s5_…'  → 파일 저장소(기본 serving)
        build_clause_index.py            _CLAUSE_TAG='s6_'
        pgvector_clause_index.py         CURRENT_GENERATION='s6'

    판정이 읽는 파일 저장소는 s5, 벡터 검색은 s6 였다.
    **같은 질문에 두 경로가 다른 조항을 준다.** 근거가 갈리면 판정을 못 믿는다.
"""

from __future__ import annotations

import json
import re

import pytest

from app.core import release
from app.core.errors import ConfigError


def test_세대는_clause_tag_에서_파생된다():
    """★설정에 `index_generation` 을 따로 두지 않는다. 중복은 다시 어긋난다."""
    r = release.load()
    assert r.index_generation == r.clause_tag.split("_")[0]
    #: 설정 파일에 세대를 직접 적어 두면 안 된다.
    assert "index_generation" not in r.raw


def test_릴리스를_바꾸면_모든_경로가_따라간다(tmp_path, monkeypatch):
    """★한 곳만 안 따라가면 그 경로가 옛 세대를 판정 근거로 쓴다."""
    from app.adapters import pgvector_clause_index as ix

    cfg = json.loads((release._FILE).read_text(encoding="utf-8"))
    cfg["clause_tag"] = "s99_fake-1.0"
    cfg["tag"] = "s99_fake-1.0"
    cfg["document_count"] = 0          # 완전성 검사는 이 테스트의 관심이 아니다
    p = tmp_path / "accepted.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(release, "_FILE", p)

    assert release.load().clause_tag == "s99_fake-1.0"
    assert ix.current_generation() == "s99"

    from app.adapters import file_clause_store as fs

    assert fs._accepted_tag.__module__          # 존재 확인
    #: 파일 저장소는 `ensure_ready()` 때문에 실패해야 한다 — 그 폴더가 없으니까.
    with pytest.raises(Exception):
        fs._accepted_tag()


def test_읽는_쪽에_세대_문자열_상수가_없다():
    """★`_CLAUSE_TAG = "s6_"` 같은 상수가 **읽는 쪽**에 다시 생기면 여기서 잡는다.

    ★★**만드는 쪽은 예외다.** 새 세대를 산출하는 빌더는 자기가 만드는 태그를
      이름 지어야 한다(`build_s7_hybrid.py` 의 `CLAUSE_TAG = "s7_…"`).
      가드의 취지는 "**어느 세대를 읽을지**를 코드가 정하지 마라"이지
      "세대 이름을 쓰지 마라"가 아니다.

      가르는 기준: **승인 릴리스를 읽는 파일**(`app/core/release` 를 import)과
      `app/` 전체가 대상이다. 그 밖의 빌더는 자기 산출물을 명명할 수 있다.
    """
    import re
    from pathlib import Path

    root = Path(release._ROOT)
    bad = []
    #: 주석·독스트링은 검사하지 않는다 — 사고 기록에 옛 값이 등장한다.
    assign = re.compile(r"^\s*[A-Z_]*(TAG|GENERATION)[A-Z_]*\s*=\s*[\"']s\d+", re.M)
    for f in list((root / "app").rglob("*.py")) + list((root / "scripts").rglob("*.py")):
        src = f.read_text(encoding="utf-8")
        under_app = "app" in f.relative_to(root).parts[:1]
        reads_release = "app.core.release" in src or "from app.core import release" in src
        if not (under_app or reads_release):
            continue          # 산출물을 만드는 빌더 — 자기 태그를 명명할 수 있다
        if assign.search(src):
            bad.append(str(f.relative_to(root)))
    assert not bad, f"읽는 쪽에 세대 문자열을 박았습니다: {bad}"


def test_승인_임베딩_프로필이_없으면_pg_경로를_고르지_않는다(monkeypatch):
    """★벡터가 없는데 PG 를 고르면 '근거 없음'과 '적재 안 함'을 구분할 수 없다.

    ★★**실제 설정에 기대지 않는다.** 처음엔 `config/accepted_extraction.json` 의
      `embed_profile` 이 비어 있다는 전제로 썼는데, 다른 세션이 모델을 승인해
      프로필을 채우자 **테스트가 깨졌다.** 계약을 재는 테스트가 그날의 설정값에
      따라 통과·실패하면 그건 계약을 재는 것이 아니다. 프로필을 여기서 비운다.
    """
    import app.composition as comp
    from app.core import release

    empty = release.AcceptedRelease(
        release_id="no-profile", page_tag="", clause_tag="s6_x",
        document_count=0, embed_profile=release.EmbedProfile(),
    )
    monkeypatch.setattr(release, "load", lambda *a, **k: empty)
    #: ★`reload` 하지 않는다 — 조립 지점이 **부를 때** 환경을 읽는다.
    monkeypatch.setenv("CLAUSE_STORE", "pg")
    with pytest.raises(ConfigError):
        comp.build_precheck()


def test_알_수_없는_저장소_값은_실패한다(monkeypatch):
    """★조용히 file 로 떨어지면 오타 하나로 다른 저장소를 쓰는 줄 모른다."""
    import app.composition as comp

    monkeypatch.setenv("CLAUSE_STORE", "postgre")
    with pytest.raises(ConfigError):
        comp.build_precheck()


def test_판정_한건_안에서_릴리스가_고정된다():
    """★도중에 승인 포인터가 바뀌면 **서로 다른 판의 근거가 한 답에 섞인다.**

    세대는 새 것, 임베딩 프로필은 옛 것 — 그런 조합이 나오면
    "어느 약관 판으로 판정했나"를 답할 수 없다.
    """
    from app.adapters import pgvector_clause_index as ix

    fake = release.AcceptedRelease(
        release_id="x", page_tag="", clause_tag="s99_t", document_count=0,
        embed_profile=release.EmbedProfile(model="m", max_seq_length=512),
    )
    outside = ix.current_generation()
    with release.pinned(fake):
        assert ix.current_generation() == "s99"
        assert ix.current_embed_model().startswith("m|")
    assert ix.current_generation() == outside, "블록을 나오면 원래대로 돌아와야 한다"


def test_판정_유스케이스가_릴리스를_고정한다(monkeypatch):
    """`precheck.run()` 안에서는 승인 포인터가 **매번 달라져도** 세대가 흔들리지 않는다.

    ★소스에 `pinned()` 글자가 있나를 보지 않는다 — **실제로 고정되는가**를 본다.
      문자열 검사는 구현을 조금만 바꿔도 거짓 실패를 내고,
      정작 중간 전환 경쟁은 못 잡는다(코덱스 라운드3 지적).
    """
    from app.adapters import pgvector_clause_index as ix
    from app.core.domain.precheck_result import PrecheckInput
    from app.core.usecases import precheck

    #: ★부를 때마다 **다른** 릴리스를 돌려준다. 고정이 안 돼 있으면 값이 흔들린다.
    box = {"n": 0}

    def _shifting(*a, **k):
        box["n"] += 1
        return release.AcceptedRelease(
            release_id=f"r{box['n']}", page_tag="",
            clause_tag=f"s{90 + box['n']}_t", document_count=0,
            embed_profile=release.EmbedProfile(model="m"),
        )

    monkeypatch.setattr(release, "load", _shifting)

    seen: list[str] = []

    class _Policies:
        def load_versions(self, *a, **k):
            seen.append(ix.current_generation())
            seen.append(ix.current_generation())
            raise RuntimeError("여기까지면 충분하다")

        def resolve(self, *a, **k):
            raise RuntimeError("여기까지면 충분하다")

    class _Clauses:
        def load_clauses(self, *a, **k):
            return []

        def find_by_number(self, *a, **k):
            return []

        def search(self, *a, **k):
            return []

        def stats(self, *a, **k):
            return {}

    try:
        precheck.run(
            PrecheckInput(insurer="x", enrolled_on="20200101", kcd_codes=("A00",)),
            policies=_Policies(), clauses=_Clauses(),
        )
    except Exception:
        pass

    assert len(seen) >= 2, "판정이 세대를 한 번도 읽지 않았습니다 — 테스트가 낡았습니다"
    assert seen[0] == seen[1], f"판정 도중에 세대가 바뀌었습니다: {seen}"
    #: ★밖에서는 다시 흔들려야 한다 — 프로세스 전역 캐시면 전환이 영영 안 먹는다.
    assert ix.current_generation() != seen[0]


def test_임베딩_프로필_이름이_설정_전체를_반영한다():
    """★이름이 `model@len` 뿐이면 차원·청킹이 바뀌어도 같은 이름이 나온다.

    그러면 옛 벡터를 "이미 있음"으로 보고 건너뛴다(코덱스 라운드2).
    """
    a = release.EmbedProfile(model="m", dim=768, max_seq_length=512,
                             chunk_budget=448, overlap=80)
    b = release.EmbedProfile(model="m", dim=1024, max_seq_length=512,
                             chunk_budget=448, overlap=80)
    c = release.EmbedProfile(model="m", dim=768, max_seq_length=512,
                             chunk_budget=256, overlap=80)
    assert a.key != b.key, "차원이 다르면 다른 이름이어야 한다"
    assert a.key != c.key, "청킹 예산이 다르면 다른 이름이어야 한다"
    assert release.EmbedProfile().key == "", "프로필이 없으면 빈 이름"


def test_적재가_읽은_태그에서_세대를_낸다():
    """★읽기와 쓰기가 세대를 **따로** 읽으면 그 사이 전환에 경쟁이 생긴다.

    `--clause-tag=s6…` 로 읽고 발생행을 `s5` 로 박으면 **그게 혼입이다.**
    """
    import inspect

    from scripts.index import build_clause_index as b

    src = inspect.getsource(b._load)
    assert "generation_of(args.resolved_tag)" in src, "읽은 태그에서 세대를 내야 한다"
    assert "ix.current_generation()" not in src, "쓰기에서 세대를 새로 읽으면 안 된다"


def test_그래프_전체가_릴리스를_고정한다():
    """★판정만 고정하고 **인용 검증이 밖**이면, 근거 실재 여부를 다른 판에 대고 묻는다."""
    import inspect

    from app.workflow.precheck_graph import PrecheckGraph

    assert "release.pinned()" in inspect.getsource(PrecheckGraph.invoke)


def test_중첩_pinned_는_바깥_스냅샷을_물려받는다():
    """`precheck.run()` 이 그래프 안에서 불려도 같은 릴리스를 봐야 한다."""
    from app.adapters import pgvector_clause_index as ix

    outer = release.AcceptedRelease(
        release_id="o", page_tag="", clause_tag="s98_t", document_count=0,
        embed_profile=release.EmbedProfile(model="m"),
    )
    with release.pinned(outer):
        with release.pinned(release.current()):
            assert ix.current_generation() == "s98"


# ── 공통 eligibility 게이트: **모르면 못 쓴다** ──────────────────────


def test_게이트가_필드_부재를_통과시키지_않는다():
    """★★fail-open 을 막는 음성 검사.

    이 검사가 없었으면 못 잡았을 실수: `is_statute` 라는 **없는 키**를 보고 있었다.
    산출물의 실제 키는 `statute` 다. 211,131건 전부 부재로 나오는데
    코드는 "법령 조문을 막았다"고 보고했다(코덱스가 실측으로 잡았다).
    """
    from app.core.domain import eligibility as E

    ok = {"citation_eligible": True, "statute": False,
          "qualified_no": "제1조", "text": "본문"}
    assert E.check(ok, parse_status="ok").usable

    #: 필드 하나씩 빼면 **전부 거절**돼야 한다.
    for drop in ("citation_eligible", "statute", "qualified_no", "text"):
        bad = {k: v for k, v in ok.items() if k != drop}
        v = E.check(bad, parse_status="ok")
        assert not v.usable, f"`{drop}` 가 없는데 통과했습니다"
        assert v.reason, "왜 거절인지 이유가 없습니다"

    #: `parse_status` 를 모르면 거절
    assert not E.check(ok, parse_status=None).usable
    #: `citation_eligible` 이 `False` 도 아니고 `True` 도 아닌 값
    assert not E.check({**ok, "citation_eligible": "yes"}, parse_status="ok").usable
    #: 법령 조문
    assert not E.check({**ok, "statute": True}, parse_status="ok").usable
    #: 페이지 덩어리
    assert not E.check({**ok, "chunk_type": "page_fallback"}, parse_status="ok").usable


def test_occurrence_id_는_모르면_비운다():
    """★`release_id` 를 `'?'` 로 채우면 **서로 다른 릴리스가 같은 식별자**를 갖는다."""
    from app.core.ports.precheck import ClauseRow

    base = dict(sha256="a" * 64, qualified_no="제1조", clause_no="1", section="",
                title="", text="x", page_from=1, page_to=1, content_hash="h")
    assert ClauseRow(**base, ordinal=0, release_id="r1").occurrence_id.startswith("r1:")
    #: 릴리스를 모르면 빈 문자열
    assert ClauseRow(**base, ordinal=0).occurrence_id == ""
    #: ordinal 을 모르면 빈 문자열
    assert ClauseRow(**base, release_id="r1").occurrence_id == ""


def test_인용_검증이_공통_게이트를_다시_부른다():
    """★`ClauseRow.usable` 의 **기본값이 True** 라 그것만 믿으면 샌다."""
    import inspect

    from app.workflow import precheck_graph

    src = inspect.getsource(precheck_graph)
    assert "eligibility as _elig" in src or "eligibility.check_row" in src
    assert "_elig.check_row(row)" in src
