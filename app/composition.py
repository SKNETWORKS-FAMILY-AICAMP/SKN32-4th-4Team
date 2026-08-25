"""보험 유스케이스의 어댑터 조립 지점.

구형 커머스 FAISS·GraphRAG·지식 바운티 조립은 활성 트리에서 격리했다. 보험 조항 검색은
``build_clause_search_deps``와 ``app.core.usecases.clause_search``를 사용한다.
"""

from __future__ import annotations

import os


#: 조항을 어디서 읽을 것인가. `file`(추출 산출물) | `pg`(인덱스 A).
#:
#: ★기본은 `file` 이다. 인덱스 A 적재는 CPU 로 4~5시간 걸리는 긴 작업이라
#:   기계마다 상태가 다르다. **없는 것을 있는 척하지 않는다** —
#:   PG 를 쓰려면 `CLAUSE_STORE=pg` 로 **명시**한다.
#: ★★**부를 때 읽는다.** import 시점에 굳히면 두 가지가 깨진다 —
#:   ① 환경을 바꿔도 같은 프로세스가 옛 선택을 붙들고 있다
#:   ② 테스트가 `importlib.reload` 로 우회하다가 **뒤 테스트를 오염시킨다**(실제로 그랬다)
#:
#: ★★★**2026-08-25 정정 — `.env` 에 적어도 먹지 않았다.**
#:   여기서 `os.getenv` 로만 읽었는데, 이 저장소에는 `load_dotenv()` 호출이 없고
#:   pydantic-settings 는 `.env` 를 **Settings 필드 해석에만** 쓰고 `os.environ` 에 넣지 않는다.
#:   그래서 `.env` 에 `CLAUSE_STORE=pg` 라고 적으면 **조용히 무시되고 파일 어댑터로 떨어졌다.**
#:   다른 6개 저장소 설정(`AUTH_PERSISTENCE` 등)은 Settings 필드라 `.env` 로 먹는데
#:   이것만 안 먹었다 → 「pgvector 로 판정한다」고 믿는 상태와 실제 동작이 어긋난다.
#:   → `docs/reports/debugs/2026-08-24_1815_운영템플릿에_CLAUSE_STORE가_없다.md`
#:
#: ★`get_settings()`(lru_cache) 를 쓰지 않고 `Settings()` 를 새로 만든다.
#:   캐시를 쓰면 위 ①이 되살아난다 — 환경을 바꿔도 옛 선택을 붙들고 있다.
#:   호출 지점은 조립 1회(`build_precheck` 이 `_DEPS` 로 캐시)와 readiness 조회뿐이라
#:   판정 핫패스가 아니다.
def _clause_store_kind() -> str:
    from pydantic import ValidationError

    from app.core.config import Settings
    from app.core.errors import ConfigError

    try:
        return Settings().CLAUSE_STORE.strip().lower()
    except ValidationError as e:
        #: ★조용히 file 로 떨어뜨리지 않는다. 오타 하나로 다른 저장소를 쓰는 줄 모르게 된다.
        #:   pydantic 의 ValidationError 를 이 프로젝트의 ConfigError 로 바꿔
        #:   아래 `kind != "file"` 분기와 **같은 문구로** 실패시킨다.
        raw = os.getenv("CLAUSE_STORE")
        shown = raw if raw is not None else "(.env 값)"
        raise ConfigError(
            f"CLAUSE_STORE 값을 모르겠습니다: {shown!r}. `file` 또는 `pg` 여야 합니다."
        ) from e


def build_precheck():
    """보장 사전판정에 쓸 어댑터 묶음.

    ★구체 구현을 고르는 것은 **조립 지점의 일**이다.
      라우터가 어댑터를 직접 import 하면 "어느 저장소를 쓰는가"가
      HTTP 계층에 흩어진다.

    ★조항 저장소는 두 구현이 **같은 포트**를 만족한다.
        file  data/structured/…  추출 산출물을 직접 읽는다(불변·재생성 근거)
        pg    인덱스 A            내용 한 벌 + 발생 여러 벌 (중복 65.4% 해소)
      통합 저장소를 파일로 또 만들지 않는다 — 같은 본문이 세 곳에 생기면
      어긋났을 때 무엇이 맞는지 판단할 근거가 없어진다.
    """
    from app.adapters import manifest_policy_resolver
    from app.core import release
    from app.core.errors import ConfigError

    #: ★★**어댑터를 만드는 시점에 fail-closed 검사를 한다.**
    #:
    #:   임포트 시점에 하면 테스트·CLI·부분 실행이 깨진다(코덱스).
    #:   여기서 하면 "판정을 하려는 순간"에만 검사가 걸린다.
    #:
    #:   ★산출물이 반쪽이면 판정이 **"그 약관엔 그런 조항이 없다"** 고 답한다.
    #:     그건 근거 없음이 아니라 **틀린 답**이다.
    rel = release.current()
    kind = _clause_store_kind()

    #: 배포 환경의 PG 저장소는 조항 본문을 인덱스에 보관하므로
    #: 로컬 `data/structured/*/<clause_tag>/*.clauses.json`를 포함하지 않는다.
    #: 파일 저장소에서만 산출물 존재·개수 검사를 수행한다.
    if kind == "file":
        rel.ensure_ready()

    if kind == "pg":
        from app.adapters import pg_clause_store

        #: ★승인된 임베딩 프로필이 없으면 PG 경로를 **고르지 않는다.**
        #:   벡터가 없으면 검색이 0건인데, 그걸 "근거 없음"으로 내보내면
        #:   적재를 안 한 것과 근거가 정말 없는 것을 구분할 수 없다.
        if not rel.embed_profile.is_set:
            raise ConfigError(
                f"CLAUSE_STORE=pg 인데 승인된 임베딩 프로필이 없습니다"
                f"(릴리스 {rel.release_id}).\n"
                "★모델이 확정되지 않았습니다 — "
                "`docs/reports/debugs/2026-08-03_임베딩_128토큰_절단_적재중단.md` 참조.\n"
                "지금은 `CLAUSE_STORE=file` 로 두세요."
            )
        return {"policies": manifest_policy_resolver, "clauses": pg_clause_store}

    if kind != "file":
        #: ★알 수 없는 값을 조용히 file 로 떨어뜨리지 않는다(코덱스).
        #:   오타 하나로 다른 저장소를 쓰고 있는 줄 모르게 된다.
        raise ConfigError(
            f"CLAUSE_STORE 값을 모르겠습니다: {kind!r}. `file` 또는 `pg` 여야 합니다."
        )

    from app.adapters import file_clause_store

    return {"policies": manifest_policy_resolver, "clauses": file_clause_store}


def build_cohort():
    """코호트 조회 유스케이스.

    ★어느 저장소를 볼지는 여기서 정한다. DB 적재 후엔 이 줄만 바꾼다.
    """
    from app.adapters import cohort_stats
    from app.core.usecases.cohort import CohortQuery

    return CohortQuery(cohort_stats)


def build_glossary():
    """용어 설명이 볼 구절 색인.

    ★판정용 어댑터와 **다른 것을 준다.** 용어 경로는 전역·완화 필터라
      같은 것을 나눠 쓰면 완화된 필터가 판정으로 샌다
      (`docs/handoff/11_AI_구조_지도.md` §2).
    """
    from app.adapters import file_glossary_source

    return file_glossary_source


def build_clause_search_deps() -> dict:
    """조항 의미검색이 쓸 어댑터를 묶어 준다.

    ★유스케이스(`app/core/usecases/clause_search.py`)는 어댑터를 직접 부르지 않는다
      (ARCH-002·003 — 의존 방향). 그래서 조립은 여기서 한다.
    """
    from app.adapters import pgvector_clause_index
    from app.adapters.clause_rerank import rerank_hits

    return {"index": pgvector_clause_index, "rerank_fn": rerank_hits}
