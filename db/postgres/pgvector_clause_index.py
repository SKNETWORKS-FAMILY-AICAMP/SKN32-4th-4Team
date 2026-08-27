"""인덱스 A — 약관 조항 벡터 색인 (pgvector).

★인덱스 B(외부 청구결과)와 **테이블이 다르다.** 필터로 나누지 않는다.

    나누는 기준은 "데이터가 다른가"가 아니라 **"판정 근거로 인용할 수 있는가"** 다.
    한 테이블에 두고 `WHERE` 로 거르면 두 가지가 무너진다 —

      1. 인용이 섞인다. "제9조에 따르면"과 "어떤 사용자 보고에 따르면"이 한 답에 들어간다.
      2. 순위가 오염된다. 사례 보고는 **구어체라 질문과 문장이 비슷하고**
         약관 조항은 법률체 문어다. 사후 필터로 사례를 빼도
         **필요한 조항이 이미 top-k 밖으로 밀린 뒤**라 되살릴 수 없다.

    이 테이블에는 약관 조항만 들어간다. 외부 보고는 여기 넣지 않는다.

★정체성과 발생을 나눈다 (CLAUDE.md §1)

    실측(s5 전량 1,367문서): 조항 등장 **211,131** / 고유 내용 **73,031** — 중복 **65.4%**.
    본문을 등장마다 넣으면 임베딩을 3배 계산하고 3배 저장한다.

        policy_clause_chunk       내용 한 벌 (`content_hash` 로 식별) + 임베딩
        policy_clause_occurrence  그 내용이 **어느 문서 어디에** 실렸는가

    검색은 내용에서 하고, 근거를 댈 때 발생으로 되돌린다.

★적재 대상은 **`parse_status == "ok"` 문서의 조항**이다

    추출이 의심스러운 문서(`suspect` 250 · `no_clause_heads` 9)의 조항은
    판정 근거가 될 수 없다. 넣어 두면 언젠가 필터를 빠뜨린다.
    고유 조항 73,031 중 **52,899** 가 대상이다.

★검색 필터는 **명시 인자로 받는다**

    기본값을 느슨하게 두면 용어 경로(전역)의 완화된 필터가 판정 경로로 샌다.
    판정은 약관 버전 하나로 가둬야 한다 — 2019년 가입자에게 2024년 조항이 붙으면 안 된다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from app.core.errors import InfraError

#: 임베딩 차원의 **되돌림 값**. ★진짜 값은 승인 릴리스의 `embed_profile.dim` 이다.
#:
#:   상수로 박아 두었더니 arctic-ko(1024d)를 적재하려는 순간 막혔다 —
#:   테이블이 `vector(768)` 로 이미 만들어져 있었다(2026-08-03).
#:   차원은 **모델이 정하는 것**이지 우리가 고르는 값이 아니다.
#:   그래서 설정에서 파생하고, 설정이 비었을 때만 이 값을 쓴다.
_EMBED_DIM_FALLBACK = 768


def embed_dim() -> int:
    """이 릴리스가 쓰는 벡터 차원. **승인 프로필에서 온다.**"""
    from app.core import release

    return release.current().embed_profile.dim or _EMBED_DIM_FALLBACK

#: ★쪼개는 단위를 **글자에서 토큰으로** 바꿨다.
#:
#:   처음엔 800자 고정으로 잘랐다. 두 가지가 틀렸다.
#:
#:   1. **모델 한계를 넘었다.** `ko-sroberta` 의 최대 입력은 **512토큰**인데
#:      800자 조각의 **1.4%가 512토큰을 넘어**(표본 1,500개 실측: 중앙값 363 ·
#:      90분위 446 · 최대 641) 뒷부분이 **조용히 잘린 채** 임베딩됐다.
#:      전량이면 약 1,800조각이다. 겹침이 120자뿐이라 잘린 구간이
#:      다음 조각에도 안 들어가 **아예 색인되지 않는 본문**이 생긴다.
#:   2. **문장 한가운데를 잘랐다.** 법률문은 부정어와 예외조건이 문장 끝에 온다 —
#:      "…보상합니다. 다만 … 경우에는 보상하지 않습니다" 에서 뒤를 자르면
#:      뜻이 **반대로** 남는다.
#:
#:   그래서 문단·문장 경계로 묶고 토큰 수로 센다.
#:   (코덱스 교차검증 2026-08-02)
MAX_TOKENS = 448
OVERLAP_TOKENS = 80
# Historical names are kept for callers that imported the adapter directly.
CHUNK_SIZE = MAX_TOKENS
CHUNK_OVERLAP = OVERLAP_TOKENS

#: 조항 길이(고유 내용 52,899개 실측): 중앙값 **613자** · 90분위 2,905 ·
#: 99분위 14,200 · 최대 29,977. 800자 초과 21,311개.
#: ★앞서 주석에 적어 둔 "중앙값 356자"는 **등장 기준**이었다.
#:   임베딩 대상은 고유 조항이므로 분모가 다르다 — 숫자를 인용할 때 분모를 적는다.

#: 문단 → 문장 순으로 끊는다. 여기서도 안 끊기면 토큰 창으로 자른다.
_PARA = re.compile(r"\n+")
#: ★뒤돌아보기는 **길이가 고정**이어야 한다. `다\.` 같은 두 글자 패턴을 넣었다가
#:   `look-behind requires fixed-width pattern` 으로 임포트가 죽었다.
#:   한국어 종결은 어차피 마침표로 끝나므로 한 글자 종결부호만 본다.
_SENT = re.compile(r"(?<=[.。」』\)])\s+")


def _segments(text: str) -> list[str]:
    """문단 → 문장 순으로 끊는다. 조각의 **자연스러운 경계**를 만든다."""
    out: list[str] = []
    for para in _PARA.split(text):
        para = para.strip()
        if not para:
            continue
        parts = [s for s in _SENT.split(para) if s.strip()]
        out.extend(parts or [para])
    return out or ([text] if text.strip() else [])


def chunk_clause(text: str, count_tokens: Callable[[str], int]) -> list[str]:
    """조항 하나를 **토큰 예산 안에서** 조각낸다.

    ★한 조각도 `MAX_TOKENS` 를 넘지 않는다. 넘으면 모델이 조용히 잘라 버린다.
    ★문장 경계로 묶는다. 법률문은 예외가 문장 끝에 오므로
      한가운데를 자르면 뜻이 반대로 남는다.
    ★그래도 한 문장이 예산을 넘으면 **그 문장만** 토큰 창으로 자른다.
      이때도 잘렸다는 사실이 감춰지지 않게 겹쳐 둔다.
    """
    if not text.strip():
        return []
    if count_tokens(text) <= MAX_TOKENS:
        return [text]

    #: 예산을 넘는 한 문장은 미리 쪼개 둔다. 이분 탐색으로 글자 경계를 찾는다.
    flat: list[str] = []
    for seg in _segments(text):
        if count_tokens(seg) <= MAX_TOKENS:
            flat.append(seg)
            continue
        i = 0
        while i < len(seg):
            lo, hi = 1, len(seg) - i
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if count_tokens(seg[i : i + mid]) <= MAX_TOKENS:
                    lo = mid
                else:
                    hi = mid - 1
            flat.append(seg[i : i + lo])
            i += lo

    chunks: list[str] = []
    cur: list[str] = []
    cur_tokens = 0
    for seg in flat:
        n = count_tokens(seg)
        if cur and cur_tokens + n > MAX_TOKENS:
            chunks.append(" ".join(cur))
            #: ★뒤에서부터 `OVERLAP_TOKENS` 만큼을 다음 조각 앞에 남긴다.
            back: list[str] = []
            back_tokens = 0
            for s in reversed(cur):
                t = count_tokens(s)
                if back_tokens + t > OVERLAP_TOKENS:
                    break
                back.insert(0, s)
                back_tokens += t
            cur, cur_tokens = back, back_tokens
        cur.append(seg)
        cur_tokens += n
    if cur:
        chunks.append(" ".join(cur))
    return chunks


@dataclass(frozen=True)
class ClauseHit:
    """검색 결과 한 건. **어느 문서 어디인지**가 항상 붙는다."""

    content_hash: str
    chunk_ix: int
    text: str
    distance: float
    sha256: str
    insurer: str
    qualified_no: str
    section: str
    title: str
    page_from: int
    page_to: int
    #: ★★**부모 문서 회수(parent-document retrieval).**
    #:
    #:   검색은 **조각**으로 하고(정밀도), LLM 에는 **조 전체**를 준다(문맥).
    #:   법률문은 예외가 뒤에 온다 —
    #:     "…보상합니다. 다만 … 경우에는 보상하지 않습니다"
    #:   조각만 주면 앞 절만 남아 **뜻이 반대가 된다.**
    #:
    #:   실측 근거 둘:
    #:     · KCD 코드 스캔이 조각 밖 코드를 **못 본다**(`precheck.scan_clause`)
    #:     · 인용 검증이 조 안의 문장을 "근거에 없다"고 **버린다**(`citation_guard`)
    #:
    #:   본문 한 벌은 `policy_clause_content` 에 이미 있다. 조인 한 번이면 된다 —
    #:   **데이터를 다 갖춰 두고 안 쓰고 있었다.**
    full_text: str = ""

    @property
    def citable_text(self) -> str:
        """인용·판정에 쓸 본문. **조 전체**를 준다.

        ★`full_text` 가 비면 조각으로 떨어진다. 조용히 그러지 않도록
          비는 경우는 `policy_clause_content` 에 본문이 없을 때뿐이고,
          그건 적재가 반쪽이라는 뜻이라 `drop_incomplete` 가 지운다.
        """
        return self.full_text or self.text

    @property
    def clause_id(self) -> str:
        tail = f"#{self.content_hash[:8]}" if self.content_hash else ""
        return f"{self.sha256[:12]}/{self.qualified_no}{tail}"


#: ★★**세대·임베딩 프로필을 여기서 정하지 않는다.**
#:
#:   전에는 `CURRENT_GENERATION = "s6"` 처럼 상수를 박아 두었다. 그런데 세대를
#:   정하는 곳이 파일 저장소·적재·검색 **셋**이었고 서로 어긋났다(실측 2026-08-03) —
#:   파일 저장소는 `s5`, 검색은 `s6`. 같은 질문에 두 경로가 다른 조항을 준다.
#:
#:   이제 `app/core/release.py` 한 곳에서 읽는다.
#:   ★`index_generation` 은 설정에 따로 없다. `clause_tag` 에서 **파생**한다 —
#:     중복 필드를 두면 다시 어긋난다(코덱스).
#:
#:   ★import 시점에 읽지 않는다. 부를 때마다 읽는다 —
#:     승인 릴리스를 바꿨는데 프로세스가 옛 값을 붙들고 있으면 전환이 안 된다.
LEGACY_EMBED_MODEL = "legacy-truncated-128"

#: ★★**거리 하한 — "못 찾았다"고 말할 수 있어야 한다.**
#:
#:   하한이 없으면 아무리 안 맞아도 상위 k 개를 돌려준다. 그러면 호출자는
#:   **무관한 조항을 근거로** 받는다. 판정이 그걸 인용하면 사람이 손해를 본다.
#:   "확인 불가"가 정답인 경우가 있다(CLAUDE.md §0).
#:
#: ★값은 **추측이 아니라 실측**으로 정했다(2026-08-03 · arctic-ko · s6 12만 조각).
#:   세 종류 질의의 최근접 거리 분포:
#:
#:     A 원문 그대로 (조항에서 떼어 온 문장)   0.168 ~ 0.825   n=9
#:     B 구어체      (사람이 물을 법한 말)     0.850 ~ 1.084   n=8
#:     C 무관        (약관과 상관없는 문장)     1.171 ~ 1.283   n=8
#:
#:   B 최대 1.084 와 C 최소 1.171 사이에 **겹침이 없다.** 그 사이에 둔다.
#:   ★표본이 8~9개씩이라 **작다.** 진짜 분포는 더 겹칠 수 있다.
#:   그래서 두 오류 중 **덜 나쁜 쪽**으로 기울였다 —
#:   진짜 질문을 물리치면 "확인 불가"가 나오지만(설계된 안전한 답),
#:   무관한 것을 통과시키면 **엉뚱한 근거가 판정에 들어간다.**
#:
#: ★이 값은 **모델·정규화·거리 연산자에 묶여 있다.** `<->`(L2)와 단위 벡터 기준이다.
#:   모델이나 정규화가 바뀌면 **다시 재야 한다.**
#:   `scripts/eval/` 의 거리 분포 측정을 다시 돌려서 정한다.
MAX_DISTANCE = 1.13


def generation_of(clause_tag: str) -> str:
    """조항 태그에서 세대를 파생한다. shadow 적재가 쓴다."""
    import re as _re

    m = _re.match(r"^(s\d+)_", clause_tag or "")
    if not m:
        raise ValueError(f"세대를 읽을 수 없는 조항 태그입니다: {clause_tag!r}")
    return m.group(1)


def current_generation() -> str:
    """검색·적재가 볼 세대. 승인 릴리스에서 파생한다."""
    from app.core import release

    #: ★`current()` 다. `pinned()` 블록 안이면 그 스냅샷을 쓴다 —
    #:   세대와 모델이 서로 다른 릴리스에서 오는 일을 막는다.
    return release.current().index_generation


def current_embed_model() -> str:
    """벡터를 만든 프로필 이름.

    ★**승인된 프로필이 없으면 빈 문자열**이다. 아무거나 골라 쓰지 않는다 —
      지금 모델은 미확정이고(128토큰 절단 사고), 잘린 벡터가 근거로 올라온다.

    ★그러나 **"검색 0건"이 정직한 상태는 아니다.**

        앞서 여기 "빈 문자열이면 검색이 0건이 되고 그게 정직하다"고 적혀 있었다.
        틀렸다. 0건을 그냥 돌려주면 호출자는 **"그런 조항이 없다"** 로 읽는다.
        "근거가 없다"와 "필터가 아무것도 안 맞는다"는 **다른 사실**이고,
        섞이면 판정이 근거 없이 기권하면서 원인은 감춰진다(CLAUDE.md §0).

        그래서 검색 경로가 `ensure_index_matches_release()` 로 **먼저 막는다.**
        빈 문자열은 여기서 그대로 두되, 그 상태로 질의가 나가지는 않는다.
    """
    from app.core import release

    return release.current().embed_profile.key


def index_state(conn) -> dict:
    """색인이 **실제로 무엇을 담고 있나.** 설정이 아니라 DB 를 센다.

    ★`release.ensure_ready()` 는 **디스크**만 본다. 산출물이 온전해도
      색인에 안 들어가 있으면 검색은 0건이다 — 그 구멍을 여기서 막는다.
    """
    gen, model = current_generation(), current_embed_model()
    with conn.cursor() as cur:
        #: ★**락을 기다리지 않는다.** 이건 현황 조회다 — 남이 DDL 을 걸고 있으면
        #:   비켜 준다. 실측 2026-08-03: 이 조회가 3시간짜리 읽기 락을 쥐고
        #:   `ALTER TABLE` 을 막았고, 그 뒤로 12개 세션이 밀렸다.
        cur.execute("SET LOCAL lock_timeout = '2s'")
        cur.execute("SELECT index_generation, count(*) FROM policy_clause_occurrence "
                    "GROUP BY 1 ORDER BY 2 DESC")
        gens = dict(cur.fetchall())
        cur.execute("SELECT embed_model, count(*) FROM policy_clause_chunk "
                    "GROUP BY 1 ORDER BY 2 DESC")
        models = dict(cur.fetchall())
        #: ★★**`sha256` 이 64자인지 본다.** 세대·모델만 보면 `ready:true` 인데
        #:   실제로는 조회가 전부 실패하는 상태가 된다.
        #:
        #:   실측 2026-08-03 — 적재 스크립트가 `p.stem` 을 써서
        #:   `"fd36cc4d66b2.clauses"`(20자)를 넣었다. 파일 저장소는 64자 sha 를
        #:   받아 앞 12자로 찾으므로 **짝이 안 맞아 PG 경로가 통째로 죽었다.**
        #:   그런데 `ready` 는 `true` 였다 — **준비됐다는 말이 거짓이었다.**
        cur.execute("SELECT count(*) FROM policy_clause_occurrence "
                    "WHERE index_generation = %s AND length(sha256) <> 64", (gen,))
        bad_sha = cur.fetchone()[0]
        #: ★★**발생 수를 그대로 내보내면 검색 가능 범위가 부풀어 보인다.**
        #:
        #:   실측 2026-08-04 — `occurrences_for_wanted` 가 209,883 이었는데
        #:   그중 **20,577행(10,500조항)에는 벡터가 없었다.** 우리 적재분(189,306)에
        #:   이전 s6 shadow 적재분이 섞인 것이다. 벡터가 없으면 **검색에 안 걸린다.**
        #:   그런데 숫자만 보면 20만 건이 다 찾아지는 것처럼 읽힌다.
        #:
        #:   지금은 새는 것이 아니다 — 그 행들은 게이트 값이 전부 `NULL` 이라
        #:   `eligibility` 가 "모른다 → 못 씀"으로 막는다(같은 날 실측: 게이트 통과인데
        #:   벡터 없는 행 **0건**). 그래도 **수를 갈라서 내보낸다** — 상태 보고가
        #:   실제와 어긋나는 것이 이 프로젝트에서 되풀이된 사고 유형이다(§0).
        #:
        #:   비용: 실측 0.13~0.22초(발생 21만 × 조각 12만, 병렬 해시조인).
        #:   `readiness` 의 `statement_timeout 5s` 아래서 돈다.
        cur.execute(
            "SELECT count(*) FROM policy_clause_occurrence o "
            "WHERE o.index_generation = %s AND EXISTS ("
            "  SELECT 1 FROM policy_clause_chunk k WHERE k.content_hash = o.content_hash)",
            (gen,),
        )
        with_vec = cur.fetchone()[0]
    return {
        "wanted_generation": gen,
        "wanted_embed_model": model,
        "generations_in_db": gens,
        "embed_models_in_db": models,
        "occurrences_for_wanted": gens.get(gen, 0),
        #: ★실제로 **검색에 걸릴 수 있는** 발생 수. 위 숫자와 다르면 차이가 곧 사각지대다.
        "occurrences_with_vector": with_vec,
        "occurrences_without_vector": gens.get(gen, 0) - with_vec,
        "chunks_for_wanted": models.get(model, 0),
        #: ★깨진 sha 는 **개수로 드러낸다.** 0 이 아니면 준비된 게 아니다.
        "occurrences_with_bad_sha": bad_sha,
        "ready": bool(gen) and bool(model)
        and gens.get(gen, 0) > 0 and models.get(model, 0) > 0
        and bad_sha == 0,
    }


def ensure_index_matches_release(conn) -> None:
    """★**필터가 안 맞는 것**과 **근거가 없는 것**을 구분하게 한다.

    실측 2026-08-03 — 승인 릴리스는 `index_generation='s5'` 를 가리키는데
    DB 에는 `s5-mixed` 158,186 · `s6` 195,617 뿐이었다(`s5` 는 0건).
    `embed_model` 은 승인 프로필이 비어 `''` 이고 DB 에는
    `jhgan/ko-sroberta-multitask@128` 46,385 조각이 있었다.

    **두 필터가 동시에 아무것도 안 맞았다.** 그런데 `search()` 는 빈 목록을
    돌려줬다 — 호출자는 "그런 조항이 없다"로 읽는다. 원인이 감춰진다.

    ★`s5-mixed` 는 **컬럼 기본값**이다(세대 컬럼을 나중에 붙였다).
      "s5 로 적재했다"는 뜻이 **아니라 세대 불명**이라는 뜻이다.
      그래서 `s5` 로 갈아 끼우지 않는다 — 모르는 것을 안다고 하면 안 된다.
    """
    st = index_state(conn)
    if st["ready"]:
        return
    lines = [f"색인이 승인 릴리스와 맞지 않습니다."]
    if not st["wanted_embed_model"]:
        lines.append(
            "  · 승인된 임베딩 프로필이 **없습니다**(`embed_profile` 이 비었습니다). "
            "모델을 정하고 `config/accepted_extraction.json` 에 적으세요."
        )
    elif not st["chunks_for_wanted"]:
        lines.append(
            f"  · 조각: 승인 모델 {st['wanted_embed_model']!r} 로 적재된 것이 **0건**입니다. "
            f"DB 에 있는 것 — {st['embed_models_in_db'] or '없음'}"
        )
    if st.get("occurrences_with_bad_sha"):
        lines.append(
            f"  · ★`sha256` 이 64자가 아닌 발생 행이 **{st['occurrences_with_bad_sha']:,}건** 있습니다. "
            "파일 저장소는 64자 sha 를 받아 앞 12자로 찾으므로 **짝이 안 맞습니다.** "
            "적재 스크립트가 sha 를 잘못 넣었습니다 — 지우고 다시 적재하세요."
        )
    if not st["occurrences_for_wanted"]:
        lines.append(
            f"  · 발생: 승인 세대 {st['wanted_generation']!r} 로 적재된 것이 **0건**입니다. "
            f"DB 에 있는 것 — {st['generations_in_db'] or '없음'}"
        )
        if "s5-mixed" in st["generations_in_db"]:
            lines.append(
                "    ★`s5-mixed` 는 세대 컬럼을 나중에 붙이며 들어간 **기본값**입니다. "
                "세대 불명이라는 뜻이지 s5 라는 뜻이 아닙니다 — 갈아 끼우지 마세요."
            )
    lines.append("  → `python -m scripts.index.build_clause_index` 로 다시 적재하세요.")
    raise InfraError("\n".join(lines))


def ensure_schema(conn) -> None:
    """테이블·인덱스 생성(멱등)."""
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS policy_clause_chunk (
                content_hash text    NOT NULL,
                chunk_ix     integer NOT NULL,
                --: ★이 조항이 **몇 조각으로 나뉘었는지**. 재개 판정에 쓴다.
                n_chunks     integer NOT NULL DEFAULT 0,
                text         text    NOT NULL,
                embedding    vector({embed_dim()}) NOT NULL,
                PRIMARY KEY (content_hash, chunk_ix)
            )
            """
        )
        #: ★본문을 **한 벌** 둔다(코덱스 제안: content / chunk / occurrence 3계층).
        #:   조각을 이어 붙여 본문을 복원하려 했는데, 겹침이 토큰 기준이라
        #:   **글자 수로 자를 수 없다.** 복원을 추측으로 하느니 원본을 둔다.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_clause_content (
                content_hash text    PRIMARY KEY,
                text         text    NOT NULL,
                n_chunks     integer NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_clause_occurrence (
                content_hash text    NOT NULL,
                sha256       text    NOT NULL,
                insurer      text    NOT NULL DEFAULT '',
                qualified_no text    NOT NULL DEFAULT '',
                section      text    NOT NULL DEFAULT '',
                title        text    NOT NULL DEFAULT '',
                page_from    integer NOT NULL DEFAULT 0,
                page_to      integer NOT NULL DEFAULT 0,
                PRIMARY KEY (content_hash, sha256, qualified_no, page_from)
            )
            """
        )
        #: ★★**참조 무결성을 DB 가 지키게 한다** (2026-08-26).
        #:
        #:   이 저장소에는 외래키가 **0개**였다. 그래서 조각·발생이 본문 없이 들어가도
        #:   아무도 안 막았고, 고아 발생이 45,816행 쌓였다.
        #:   지금 안전한 것은 DB 가 막아서가 아니라 **적재 코드가 우연히 순서를 지켜서**다.
        #:
        #:   ★**지금 세울 수 있는 것부터 세운다.** 실측(2026-08-26 정리 후) —
        #:     `chunk → content`      위반 **0**      → 세운다
        #:     `occurrence → content` 위반 28,227     → 못 세운다(s5-mixed 22,436 · s6 5,791)
        #:   못 세우는 것을 «나중에» 로 미루지 않고 **왜 못 세우는지 수를 적어 둔다.**
        #:
        #:   ★`ON DELETE RESTRICT` 다. `CASCADE` 로 두면 본문 한 줄을 지울 때
        #:     그 벡터가 조용히 함께 사라진다 — 검색 결과가 줄어드는데 아무도 모른다.
        #:     막고 사람이 보게 한다.
        #: ★★**스키마를 함께 본다** (2026-08-26 · 코덱스 감사 A1).
        #:   `conname` 만 보면 **다른 스키마의 같은 이름**을 보고 「이미 있다」고 판단해
        #:   이 스키마에는 **외래키를 안 만든다.** 실제로 그랬다 — 임시 스키마에
        #:   격리하니 PK 3개만 있고 FK 는 0개였다.
        cur.execute(
            """
            SELECT 1 FROM pg_constraint
             WHERE conname = 'policy_clause_chunk_content_fk'
               AND connamespace = current_schema()::regnamespace
            """
        )
        if not cur.fetchone():
            cur.execute(
                "SELECT count(*) FROM policy_clause_chunk c WHERE NOT EXISTS ("
                "  SELECT 1 FROM policy_clause_content t"
                "   WHERE t.content_hash = c.content_hash)"
            )
            violations = cur.fetchone()[0]
            if violations:
                #: ★조용히 건너뛰지 않는다. 못 세웠으면 **왜 못 세웠는지** 말한다.
                import logging

                logging.getLogger(__name__).warning(
                    "policy_clause_chunk → policy_clause_content 외래키를 세우지 못했습니다: "
                    "본문 없는 조각이 %d행 있습니다. 적재 정합을 먼저 맞추세요.", violations,
                )
            else:
                cur.execute(
                    "ALTER TABLE policy_clause_chunk "
                    "ADD CONSTRAINT policy_clause_chunk_content_fk "
                    "FOREIGN KEY (content_hash) "
                    "REFERENCES policy_clause_content(content_hash) "
                    "ON DELETE RESTRICT"
                )

        #: ★★**인용 게이트에 필요한 것을 저장한다.**
        #:
        #:   전에는 이 필드들이 없어서 `pg_clause_store` 가 전 행을
        #:   "모른다 → 못 씀"으로 판정했다. 그래서 `load_clauses()` 가
        #:   **0건**을 돌려줬다(실측 2026-08-04) — 데이터는 있는데 못 쓰는 상태였다.
        #:   그건 정직한 상태이긴 하지만 **PG 경로를 쓸 수 없게** 만든다.
        #: ★★**`ordinal` 이 여기 없었다** (2026-08-26 · 코덱스 감사 A1 이 드러냄).
        #:
        #:   운영 DB 에는 있다 — `backfill_occurrence_ordinal` 이 만들어 놨기 때문이다.
        #:   그런데 `ensure_schema()` 는 안 만들었다. 즉 **새 환경을 세우면 없다.**
        #:   `assign_ordinals` 가 `o.ordinal` 을 쓰므로 그 자리에서
        #:   `UndefinedColumn: column o.ordinal does not exist` 로 죽는다.
        #:
        #:   ★시험을 **임시 스키마에 격리하자마자** 드러났다. 그전에는 시험이
        #:     운영 테이블을 보고 있어서 이미 있는 열을 썼고, 그래서 안 보였다 —
        #:     **격리를 안 한 것이 결함을 가리고 있었다.**
        #:
        #:   ★`occurrence_id` 가 이 값으로 만들어진다. 없으면 인용 검증이
        #:     「정확히 한 행」을 특정 못 해 **전건 기권**한다.
        for col, typ in (("citation_eligible", "boolean"),
                         ("chunk_type", "text"),
                         ("is_statute", "boolean"),
                         ("parse_status", "text"),
                         ("ordinal", "integer"),
                         #: ★★산출물이 매긴 **원래** 순번. `occurrence_id` 는 이걸 쓴다.
                         #:   위 `ordinal` 은 색인에 든 행만 다시 매긴 **검색용** 번호라
                         #:   게이트 판정이 바뀌면 따라 바뀐다 — 영구 식별자로 쓸 수 없다
                         #:   (2026-08-27, 마이그레이션 020 참조).
                         ("source_ordinal", "integer")):
            cur.execute(
                f"ALTER TABLE policy_clause_occurrence "
                f"ADD COLUMN IF NOT EXISTS {col} {typ}"
            )
        #: 앞서 만든 테이블에는 이 열이 없다. 붙인다(멱등).
        cur.execute(
            "ALTER TABLE policy_clause_chunk "
            "ADD COLUMN IF NOT EXISTS n_chunks integer NOT NULL DEFAULT 0"
        )
        #: ★★**세대(generation)를 행에 박는다.**
        #:
        #:   조항 스키마가 오르면 `content_hash` 가 바뀐다(s6 에서 부록을 본문에서 뺐다).
        #:   구·신 해시가 한 테이블에 같이 남으면 **오염된 옛 근거가 계속 검색된다**(코덱스).
        #:   그렇다고 지우면 6시간짜리 임베딩을 버린다.
        #:   그래서 **지우지 않고 갈라 놓는다** — 검색은 현재 세대만 본다.
        #:
        #:   ★기존 행의 기본값을 `s5-mixed` 로 둔다. `s5` 라고 하지 않는다 —
        #:     실제로 s5 적재 중간에 s6 발생행이 섞여 들어간 적이 있어
        #:     그 안이 순수한 s5 라고 **말할 수 없다.** 모르면 모른다고 적는다(§0).
        cur.execute(
            "ALTER TABLE policy_clause_occurrence "
            "ADD COLUMN IF NOT EXISTS index_generation text NOT NULL DEFAULT 's5-mixed'"
        )
        #: ★조항인가 부록인가. 판정이 인용 문구를 다르게 써야 한다 —
        #:   부록을 `제27조(준용규정)` 이라 부르면 출처가 틀린다.
        cur.execute(
            "ALTER TABLE policy_clause_occurrence "
            "ADD COLUMN IF NOT EXISTS source_kind text NOT NULL DEFAULT 'clause'"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS policy_clause_occurrence_gen "
            "ON policy_clause_occurrence (index_generation)"
        )
        #: ★벡터를 만든 모델. 세대와 **같은 이유**로 행에 박는다 —
        #:   모델이 다르면 벡터 공간이 다르고, 섞여도 오류가 안 난다.
        #:   기존 행은 128토큰에서 잘린 벡터다. 그 사실을 이름에 적어 둔다.
        cur.execute(
            "ALTER TABLE policy_clause_chunk ADD COLUMN IF NOT EXISTS "
            f"embed_model text NOT NULL DEFAULT '{LEGACY_EMBED_MODEL}'"
        )
        #: ★PK 에 넣는다. 안 넣으면 `ON CONFLICT DO NOTHING` 때문에
        #:   새 모델 벡터가 **버려지고** 옛 벡터가 자리를 지킨다(발생행에서 겪은 그 함정).
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.key_column_usage
                    --: ★★**스키마를 함께 본다** (2026-08-26 · 코덱스 감사 A1).
                    --:   `table_name` 만 보면 **다른 스키마의 같은 이름**까지 센다.
                    --:   임시 스키마를 만든 시험이 이 함수를 부르면 운영 쪽 제약을 보고
                    --:   「이미 있다」로 판단하거나, 반대로 **운영 테이블에 DDL 을 건다.**
                    --:   ★`policy_clause_*` 는 지금 19만행대다 — PK DROP/ADD 는 그만한 잠금이다.
                    WHERE table_schema = current_schema()
                      AND table_name = 'policy_clause_chunk'
                      AND constraint_name = 'policy_clause_chunk_pkey'
                      AND column_name = 'embed_model'
                ) THEN
                    ALTER TABLE policy_clause_chunk
                        DROP CONSTRAINT IF EXISTS policy_clause_chunk_pkey;
                    ALTER TABLE policy_clause_chunk
                        ADD PRIMARY KEY (content_hash, chunk_ix, embed_model);
                END IF;
            END $$;
            """
        )
        #: ★★**세대를 기본키에 넣는다.** 이걸 빠뜨리면 조용히 새는 곳이 생긴다 —
        #:   `upsert_occurrences` 는 `ON CONFLICT DO NOTHING` 이라,
        #:   `(hash, sha, no, page)` 가 같은 옛 세대 행이 이미 있으면
        #:   **새 세대 행이 버려지고** 그 자리는 계속 `s5-mixed` 로 남는다.
        #:   검색은 현재 세대만 보므로 그 조항은 **영원히 안 나온다.**
        #:   적재 로그에는 "이미 있음"으로 찍혀 정상처럼 보인다. 최악의 실패다.
        cur.execute(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.key_column_usage
                    --: ★★**스키마를 함께 본다** (2026-08-26 · 코덱스 감사 A1).
                    --:   `table_name` 만 보면 **다른 스키마의 같은 이름**까지 센다.
                    --:   임시 스키마를 만든 시험이 이 함수를 부르면 운영 쪽 제약을 보고
                    --:   「이미 있다」로 판단하거나, 반대로 **운영 테이블에 DDL 을 건다.**
                    --:   ★`policy_clause_*` 는 지금 19만행대다 — PK DROP/ADD 는 그만한 잠금이다.
                    WHERE table_schema = current_schema()
                      AND table_name = 'policy_clause_occurrence'
                      AND constraint_name = 'policy_clause_occurrence_pkey'
                      AND column_name = 'index_generation'
                ) THEN
                    ALTER TABLE policy_clause_occurrence
                        DROP CONSTRAINT IF EXISTS policy_clause_occurrence_pkey;
                    ALTER TABLE policy_clause_occurrence
                        ADD PRIMARY KEY (content_hash, sha256, qualified_no,
                                         page_from, index_generation);
                END IF;
            END $$;
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS policy_clause_chunk_hnsw "
            "ON policy_clause_chunk USING hnsw (embedding vector_l2_ops)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS policy_clause_occurrence_sha "
            "ON policy_clause_occurrence (sha256)"
        )
    conn.commit()


def existing_hashes(conn, *, model: str | None = None) -> set[str]:
    """**온전히** 들어간 내용만. 다시 계산하지 않는다(재개 가능하게).

    ★조각 하나만 들어가도 "완료"로 보던 버그가 있었다.
      배치 중간에 죽으면 그 조항의 나머지 조각이 **영구히 누락되고**,
      다음 실행은 이미 있다고 건너뛴다. 아무도 모른다.

      실측(2026-08-02, 중단 지점): 내용 12,507개 중 **2개**의 끝 조각이
      800자로 꽉 찬 채 완료로 표시돼 있었다 — 뒤가 더 있었다는 뜻이다.
      5시간짜리 작업을 여러 번 끊으면 쌓이고, **감지되지 않는다.**

    이제 `n_chunks` 를 기록하고 **개수가 맞는 것만** 완료로 본다.
    """
    model = model or current_embed_model()
    with conn.cursor() as cur:
        #: ★**모델별로** 센다. 모델을 바꾸면 옛 모델 벡터가 있어도
        #:   "이미 있음"이 되어선 안 된다 — 새 모델로는 아직 안 만든 것이다.
        cur.execute(
            "SELECT ct.content_hash FROM policy_clause_content ct "
            "JOIN policy_clause_chunk ck ON ck.content_hash = ct.content_hash "
            "WHERE ck.embed_model = %s "
            "GROUP BY ct.content_hash, ct.n_chunks "
            "HAVING count(*) = ct.n_chunks",
            (model,),
        )
        return {r[0] for r in cur.fetchall()}


def drop_incomplete(conn, *, scope: str = "") -> dict:
    """미완성으로 남은 조각을 지운다. **다시 넣기 위해서다.**

    ★남겨 두면 검색에 반쪽짜리 본문이 근거로 올라온다.

    ★**저장소 전체를 훑는 정리 작업이다.** 적재 스크립트 시작 지점에서만 부른다.

    ★★**그래서 `scope="all"` 을 명시해야 돈다**(2026-08-26 · 코덱스 감사 P0).

        주석으로는 안 막혔다. **두 번 터졌다** —
          · 2026-08-02: 시험이 조각 **43,064개**를 지웠다.
          · 2026-08-26: 새 시험이 본문 **1,506행**을 지웠다.
            같은 파일 20줄 위에 경고가 있었는데 읽고도 그랬다.

        코덱스 감사의 표현이 정확하다 —
        「**운영 작업의 의도는 전역이지만 재사용 API 에는 범위가 빠졌다.**
          `conn` 하나로 실행되는 이상 테스트·REPL·새 스크립트가 운영 build 와
          같은 권한을 갖는다.」

        전역 정리 자체는 **필요하다**(반쪽 벡터를 다시 넣으려면 지워야 한다).
        없애는 대신 **의도를 말하게** 한다. 실수로는 못 부르고, 일부러는 부를 수 있다.

    ★★**고아를 만들지 않는다** (2026-08-25 실측으로 규명).

        여기 마지막 문장이 「벡터 없는 본문」을 지우면서 **`occurrence` 는 안 지웠다.**
        그래서 발생만 남고 본문이 사라진 행이 **38,326개** 쌓여 있었다
        (고유 해시 15,469 · 문서 1,104 · 전부 게이트 NULL · 벡터 0건).

        재임베딩으로 모델 이름이 바뀌면 옛 해시의 조각이 «현재 모델»에 없다.
        그러면 본문이 「반쪽」으로 보여 지워지는데, 그 본문을 가리키던
        옛 세대 발생은 그대로 남는다. 그게 고아의 정체였다.

        **본문을 지우기 전에 그 해시를 가리키는 발생이 있는지 본다.**
        · 가리키는 발생이 **없으면** 지운다 — 아무도 안 쓰는 반쪽이다.
        · 가리키는 발생이 **있으면 남긴다.** 지우면 그 발생이 고아가 된다.
          대신 **몇 건을 남겼는지 세어 돌려준다** — 조용히 넘기지 않는다(CLAUDE.md §3).

    돌려주는 것 (앞서는 `int` 하나였다 — 무엇이 일어났는지 알 수 없었다)
        ``chunks_deleted``   지운 조각 해시 수
        ``content_deleted``  지운 본문 행 수(가리키는 발생이 없던 것)
        ``content_kept``     ★본문이 반쪽이지만 **발생이 가리켜서 남긴** 행 수
        ``orphans_before``   실행 전부터 있던 고아 발생 행 수(이 함수가 만든 것이 아니다)
        ``orphaned_by_drop`` ★**이 실행의 조각 삭제가 «새로» 만든 고아 발생 행 수.**
                             0 이 아니면 그만큼 다시 임베딩해야 한다 — 조용히 넘기지 않는다.
    """
    if scope != "all":
        #: ★기본값을 「안 함」으로 두지 않고 **거절**한다. 조용히 아무것도 안 하면
        #:   호출자는 정리가 된 줄 안다 — 그게 더 나쁘다.
        raise ValueError(
            "drop_incomplete 는 저장소 전체를 훑어 지웁니다. "
            'scope="all" 을 명시하세요. '
            "시험에서 부른다면 임시 스키마로 격리하고(CREATE SCHEMA + SET search_path + "
            "빈 테이블 확인) 나서 부르세요 — 안 그러면 운영 데이터가 지워집니다."
        )

    done = existing_hashes(conn)
    with conn.cursor() as cur:
        #: ★**현재 모델 안에서만** 반쪽을 찾는다. 모델 전체를 섞어 보면
        #:   옛 모델 벡터를 "미완성"으로 오인해 통째로 지운다.
        cur.execute("SELECT DISTINCT content_hash FROM policy_clause_chunk "
                    "WHERE embed_model = %s", (current_embed_model(),))
        have = {r[0] for r in cur.fetchall()}
        bad = sorted(have - done)

        #: ★★**이 삭제가 고아를 만든다** (2026-08-26 · DB10).
        #:
        #:   반쪽 조각을 지우는 것은 맞다 — 남겨 두면 검색에 잘린 본문이 올라온다.
        #:   그런데 그 해시를 **발생행이 가리키고 있으면** 지우는 순간 고아가 된다.
        #:   본문 삭제 쪽은 2026-08-25 에 막았는데(아래) **조각 삭제는 그대로였다.**
        #:
        #:   ★외래키(`chunk → content`)는 이걸 **안 막는다.** 그 제약은 「본문 없는
        #:     조각을 못 넣게」 하는 것이지 「조각을 지우지 못하게」 하는 것이 아니다.
        #:     `occurrence → content` 도 마찬가지다 — 발생이 가리키는 것은 **본문**이다.
        #:
        #:   ★그렇다고 안 지울 수는 없다. 반쪽을 남기면 그게 근거로 나간다.
        #:     **지우되, 몇 건이 고아가 되는지 세어 보고한다.** 조용히 만들지 않는다.
        orphaned_by_drop = 0
        if bad:
            cur.execute(
                "SELECT count(*) FROM policy_clause_occurrence o"
                " WHERE o.content_hash = ANY(%s)"
                #: 다른 모델 조각이 남아 있으면 고아가 안 된다.
                "   AND NOT EXISTS (SELECT 1 FROM policy_clause_chunk c"
                "                    WHERE c.content_hash = o.content_hash"
                "                      AND c.embed_model <> %s)",
                (bad, current_embed_model()),
            )
            orphaned_by_drop = cur.fetchone()[0]
            cur.execute("DELETE FROM policy_clause_chunk "
                        "WHERE content_hash = ANY(%s) AND embed_model = %s",
                        (bad, current_embed_model()))
        n = len(bad)

        #: 들어오기 전부터 있던 고아. 이 함수가 만든 것과 구분해 보고한다.
        cur.execute(
            "SELECT count(*) FROM policy_clause_occurrence o WHERE NOT EXISTS ("
            "  SELECT 1 FROM policy_clause_content t WHERE t.content_hash = o.content_hash)"
        )
        orphans_before = cur.fetchone()[0]

        #: 본문만 있고 조각이 없는 것 — **가리키는 발생이 없을 때만** 지운다.
        cur.execute(
            "DELETE FROM policy_clause_content ct"
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM policy_clause_chunk ck WHERE ck.content_hash = ct.content_hash)"
            " AND NOT EXISTS ("
            "   SELECT 1 FROM policy_clause_occurrence o WHERE o.content_hash = ct.content_hash)"
        )
        content_deleted = cur.rowcount

        #: 반쪽이지만 발생이 가리켜서 남긴 것 — 지웠다면 고아가 됐을 행들이다.
        cur.execute(
            "SELECT count(*) FROM policy_clause_content ct"
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM policy_clause_chunk ck WHERE ck.content_hash = ct.content_hash)"
            " AND EXISTS ("
            "   SELECT 1 FROM policy_clause_occurrence o WHERE o.content_hash = ct.content_hash)"
        )
        content_kept = cur.fetchone()[0]
    conn.commit()
    return {
        "chunks_deleted": n,
        "content_deleted": content_deleted,
        "content_kept": content_kept,
        "orphans_before": orphans_before,
        "orphaned_by_drop": orphaned_by_drop,
    }


def upsert_content(conn, rows) -> int:
    """`(content_hash, text, n_chunks)` — 조항 본문 한 벌."""
    n = 0
    with conn.cursor() as cur:
        for content_hash, text, n_chunks in rows:
            cur.execute(
                "INSERT INTO policy_clause_content (content_hash, text, n_chunks) "
                "VALUES (%s, %s, %s) ON CONFLICT (content_hash) DO UPDATE "
                "SET text = EXCLUDED.text, n_chunks = EXCLUDED.n_chunks",
                (content_hash, text, n_chunks),
            )
            n += cur.rowcount
    return n


def upsert_chunks(conn, rows, *, model: str | None = None) -> int:
    """`(content_hash, chunk_ix, n_chunks, text, embedding)` 을 넣는다.

    ★한 조항의 조각은 **전부 한 트랜잭션**에 들어와야 한다.
      호출자가 조항 단위로 묶어서 넘긴다 — 중간에 죽어도 반쪽이 남지 않는다.

    ★★**느리다 — 조각 하나당 왕복 한 번이다.**

        실측 2026-08-03: 122,697조각 적재에 **초당 약 100건**(20분).
        그때 클라이언트 CPU 9% · postgres 75%(8코어 중 1) 였다 —
        계산이 아니라 **파싱·트랜잭션 오버헤드**가 병목이다.

        `psycopg.extras.execute_values` 나 `COPY` 로 묶어 보내면 크게 준다.
        지금 고치지 않은 이유는 **적재가 한 번뿐인 작업**이어서다.
        다시 적재할 일이 잦아지면 그때 고친다(YAGNI — RULE §3.3).

        ★기계를 바꿔서 해결되지 않는다. DB 가 로컬이라 다른 기계로 옮기면
          loopback 이 네트워크 왕복이 되어 **더 느려진다.**
    """
    model = model or current_embed_model()
    n = 0
    with conn.cursor() as cur:
        for content_hash, chunk_ix, n_chunks, text, vec in rows:
            cur.execute(
                "INSERT INTO policy_clause_chunk "
                "(content_hash, chunk_ix, n_chunks, text, embedding, embed_model) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (content_hash, chunk_ix, n_chunks, text, vec, model),
            )
            n += cur.rowcount
    conn.commit()
    return n


def assign_ordinals(conn, *, generation: str, sha256s: list[str] | None = None,
                    dry_run: bool = False, scope: str = "") -> int:
    """발생행에 **수록 순번**을 매긴다. 문서·종류별 결정적 순위. **멱등**하다.

    ★**왜 여기 있나** — `occurrence_id`(= `릴리스:sha256:source_kind:순번`)가 이 값으로
      만들어진다. 비어 있으면 인용 검증이 "정확히 한 행"을 특정하지 못해 **기권**하고,
      겹치면 그 키를 「못 쓰는 것」으로 표시해 근거에서 **버린다.**
      즉 이 표에 행을 넣는 쪽이 **유일성을 책임져야 한다** — 호출자에게 맡기면 깨진다.

    ★★**호출자가 준 번호를 쓰지 않는다(2026-08-25).**

        조항 JSON 의 `ordinal` 을 그대로 넣어 봤다가 깨졌다. 발생행은 JSON 등장과
        **1:1 이 아니다** — `ON CONFLICT` 키로 **합쳐지기** 때문이다. 합쳐진 행에
        어느 번호를 줄지 고르는 순간 **다른 행이 이미 쓰는 번호와 겹친다.**
        실제로 한 문서에 `…:clause:1` 이 둘이었다
        (`tests/test_clause_store_parity.py::test_수록_식별자는_문서_안에서_유일하다`).

        그래서 **표에 실제로 남은 행을 기준으로** 순위를 매긴다.
        정렬 키 `(page_from, qualified_no, content_hash)` 는 `(sha256, generation)` 안에서
        유일하다 — `ON CONFLICT` 키와 같은 조합이기 때문이다. 그래서 실행마다 같은 값이다.

    ★`source_kind` 로 나눠 매긴다. `occurrence_id` 에 종류가 들어가므로
      조항·부록·승인fact 가 서로 번호를 다투지 않는다.

    ★문서 단위로 돌린다 — 21만 행을 한 문장으로 갱신하면 `statement_timeout` 에 걸리고
      (실측 2026-08-25), 공유 DB 에서 긴 잠금을 잡게 된다.

    Args:
        sha256s: 대상 문서. `None` 이면 **그 세대 전체**.

    Returns:
        값이 실제로 바뀐 행 수.
    """
    with conn.cursor() as cur:
        if sha256s is None:
            cur.execute(
                "SELECT DISTINCT sha256 FROM policy_clause_occurrence "
                "WHERE index_generation = %s",
                (generation,),
            )
            targets = [r[0] for r in cur.fetchall()]
        else:
            targets = sorted(set(sha256s))

    #: ★★**세기만 하는 길을 둔다**(`dry_run`, 2026-08-26).
    #:
    #:   이 함수는 문서마다 `conn.commit()` 한다. 그래서 「값이 그대로인지 보자」는
    #:   목적으로 부르면 **보는 순간 바꿔 버린다.** 실제로 시험
    #:   `test_다시_매겨도_값이_그대로다` 가 운영 DB 의 순번 146,601행을 다시 매겼다
    #:   (2026-08-26). 확인하려던 것이 확인 대상을 바꾼 것이다.
    #:
    #:   ★순번이 바뀌면 `occurrence_id` 가 바뀌고, 그건 **어제 발급한 판정의 근거를
    #:     오늘 못 찾는다**는 뜻이다. 보는 일과 바꾸는 일을 갈라 놓는다.
    #: ★★**`sha256s=None` 은 「그 세대 전체」다** — 쓰기 기본값으로는 너무 넓다
    #:   (2026-08-26 · 코덱스 감사 P1).
    #:
    #:   순번이 바뀌면 `occurrence_id` 가 바뀌고, 그건 **어제 발급한 판정의 근거를
    #:   오늘 못 찾는다**는 뜻이다. 실제로 시험 하나가 운영 순번 **146,601행**을
    #:   다시 매겼다(그 시험은 `dry_run=True` 로 고쳤다).
    #:
    #:   ★정상 적재는 안전하다 — `upsert_occurrences` 가 **건드린 문서 목록**을 넘긴다.
    #:     위험은 소급 CLI 와 새 직접 호출에 있다. 그쪽만 막는다.
    #:   ★`dry_run` 은 안 막는다. 세는 것은 아무것도 안 바꾼다.
    if sha256s is None and not dry_run and scope != "all_in_generation":
        raise ValueError(
            f"sha256s 없이 부르면 세대 '{generation}' 의 **모든 문서** 순번을 다시 매깁니다. "
            "그러면 occurrence_id 가 바뀌어 이미 발급된 인용이 다른 조항을 가리킵니다. "
            'scope="all_in_generation" 을 명시하거나, 문서 목록을 넘기거나, '
            "dry_run=True 로 세기만 하세요."
        )

    changed = 0
    if dry_run:
        for sha in targets:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT count(*)
                      FROM policy_clause_occurrence o
                      JOIN (SELECT ctid,
                                   row_number() OVER (
                                       PARTITION BY source_kind
                                       ORDER BY page_from, qualified_no, content_hash) - 1 AS rn
                              FROM policy_clause_occurrence
                             WHERE index_generation = %s AND sha256 = %s) r
                        ON r.ctid = o.ctid
                     WHERE o.ordinal IS DISTINCT FROM r.rn
                    """,
                    (generation, sha),
                )
                changed += cur.fetchone()[0]
        return changed

    for sha in targets:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE policy_clause_occurrence o
                   SET ordinal = r.rn - 1
                  FROM (SELECT ctid,
                               row_number() OVER (
                                   PARTITION BY source_kind
                                   ORDER BY page_from, qualified_no, content_hash) AS rn
                          FROM policy_clause_occurrence
                         WHERE index_generation = %s AND sha256 = %s) r
                 WHERE o.ctid = r.ctid
                   --: 이미 맞으면 건드리지 않는다(멱등·불필요한 WAL 방지)
                   AND o.ordinal IS DISTINCT FROM r.rn - 1
                """,
                (generation, sha),
            )
            changed += cur.rowcount
        conn.commit()
    return changed


def upsert_occurrences(conn, rows, *, generation: str | None = None) -> int:
    """조항이 **어느 문서 어디에** 실렸는지. 같은 자리는 한 번만.

    ★`rows` 는 `(hash, sha, insurer, qualified_no, section, title, page_from, page_to)`
      또는 뒤에 `source_kind` 를 하나 더 붙인 9튜플.

    ★★**넣은 뒤 수록 순번을 매긴다(2026-08-25).** 순번이 없으면 `occurrence_id` 가 비고,
      그러면 **인용 검증이 전건 기권**한다 — `CLAUSE_STORE=pg` 판정이 통째로 죽는다.
      호출자가 잊을 수 있는 일이 아니므로 **여기서 책임진다**(`assign_ordinals` 주석 참조).
      건드린 문서만 다시 매기므로 비용은 그 문서 몫뿐이고, 멱등하다.
    """
    #: ★import 시점 기본값을 두지 않는다(코덱스). 부를 때 정한다.
    generation = generation or current_generation()
    n = 0
    with conn.cursor() as cur:
        for r in rows:
            kind = r[8] if len(r) > 8 else "clause"
            #: ★인용 게이트 값. **없으면 `None`(=모른다)** 로 둔다 —
            #:   `True` 로 때우면 못 쓸 조항이 근거로 나간다.
            gate = r[9] if len(r) > 9 else {}
            #: ★★산출물이 매긴 **원래** 순번(11번째). `occurrence_id` 가 이걸 쓴다.
            #:   안 주면 `None` — 「모른다」다. 0 으로 때우면 **다른 조항을 가리킨다.**
            #:   못 채운 행은 `occurrence_id` 가 빈 문자열이 되어 호출부가 기권한다.
            source_ordinal = r[10] if len(r) > 10 else None
            cur.execute(
                "INSERT INTO policy_clause_occurrence "
                "(content_hash, sha256, insurer, qualified_no, section, title, "
                " page_from, page_to, index_generation, source_kind, "
                " citation_eligible, chunk_type, is_statute, parse_status, source_ordinal) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (content_hash, sha256, qualified_no, page_from, index_generation) "
                #: ★★**아는 값을 모르는 값으로 덮지 않는다**(2026-08-25 실측으로 규명).
                #:
                #:   앞서는 `citation_eligible = EXCLUDED.citation_eligible` 였다.
                #:   게이트를 안 주는 호출자(짧은 튜플 → `gate = {}`)가 같은 자리를
                #:   다시 쓰면 **이미 채워져 있던 값이 NULL 로 덮였다.**
                #:   NULL 은 「모른다」이므로 그 조항은 **인용 불가로 떨어진다** — 조용히.
                #:
                #:   실물 증거: `s6` 210,733행 중 청크가 있으면서 게이트 4필드가
                #:   전부 NULL 인 발생이 **정확히 한 행** 있었다
                #:   (`0cf7c85fc900` / 특별약관 제3조 · 보험금 지급에 관한 세부규정).
                #:   그때는 1건이었지만 **막혀 있지 않았다.**
                #:
                #:   `COALESCE(EXCLUDED.x, 기존값)` — 새 값이 있으면 쓰고, 없으면 **둔다.**
                #:   ★거꾸로가 아니다. 새로 «아는» 값이 오면 그건 반영해야 한다.
                #:     `False` 는 NULL 이 아니므로 **정상 반영된다** — 인용 가능이던 조항이
                #:     불가로 바뀌면 그대로 내려간다. 막히는 것은 NULL(=모른다) 뿐이다.
                #:
                #: ★★대가를 적어 둔다 — 이제 **「알던 것을 다시 모르게 되는」 전이는 못 쓴다.**
                #:   재추출이 판단을 못 하게 돼 NULL 로 되돌려야 하는 경우, 이 경로로는
                #:   안 된다. 그 상황이 실제로 생기면 **명시적으로 NULL 을 쓰는 경로**를
                #:   따로 만들어야지, 여기를 되돌리면 안 된다 — 되돌리면 게이트를 안 주는
                #:   호출이 다시 남의 값을 지운다.
                "DO UPDATE SET "
                "  citation_eligible = COALESCE(EXCLUDED.citation_eligible,"
                "                               policy_clause_occurrence.citation_eligible),"
                "  chunk_type        = COALESCE(EXCLUDED.chunk_type,"
                "                               policy_clause_occurrence.chunk_type),"
                "  is_statute        = COALESCE(EXCLUDED.is_statute,"
                "                               policy_clause_occurrence.is_statute),"
                "  parse_status      = COALESCE(EXCLUDED.parse_status,"
                "                               policy_clause_occurrence.parse_status),"
                #: ★같은 이유로 COALESCE — 원래 순번을 안 주는 호출자가
                #:   이미 채워진 값을 NULL 로 덮으면 그 행은 **인용 불가**가 된다.
                "  source_ordinal    = COALESCE(EXCLUDED.source_ordinal,"
                "                               policy_clause_occurrence.source_ordinal)",
                (*r[:8], generation, kind,
                 gate.get("citation_eligible"), gate.get("chunk_type"),
                 gate.get("is_statute"), gate.get("parse_status"), source_ordinal),
            )
            n += cur.rowcount
    conn.commit()
    #: ★넣은 문서만 다시 매긴다. 세대 전체를 훑으면 남의 문서까지 잠근다.
    touched = {r[1] for r in rows if len(r) > 1 and r[1]}
    if touched:
        assign_ordinals(conn, generation=generation, sha256s=sorted(touched))
    return n


def search(
    conn,
    query_vec,
    *,
    sha256s: list[str] | None,
    limit: int = 8,
    max_distance: float | None = None,
) -> list[ClauseHit]:
    """조항 검색.

    ★`sha256s` 는 **반드시 넘긴다.** `None` 은 "전역으로 찾겠다"는 **명시적 선택**이고,
      용어 설명 경로에서만 쓴다. 판정 경로는 약관 버전 목록을 넘겨 가둔다.
      기본값을 두지 않는 이유다 — 안 넘기면 호출이 실패해야 한다.

    ★`max_distance` 를 넘는 것은 **버린다**(기본 `MAX_DISTANCE`).
      결과가 빈 목록이면 그건 "근거를 못 찾았다"이고, 호출자는 그렇게 읽어야 한다.
      ★`0` 을 주면 하한이 없다 — 분포를 재는 도구만 그렇게 쓴다.
    """
    max_distance = MAX_DISTANCE if max_distance is None else max_distance
    if max_distance <= 0:
        max_distance = 1e9
    if sha256s is not None and not sha256s:
        #: 빈 목록은 "쓸 수 있는 약관이 없다"이다. 전역 검색으로 바꿔치지 않는다.
        return []
    #: ★★**필터가 안 맞는 것을 "결과 없음"으로 내보내지 않는다.**
    #:   승인 세대·모델이 색인에 0건이면 이 질의는 무엇을 물어도 빈 목록이다.
    #:   그대로 돌려주면 호출자가 "그런 조항이 없다"로 읽는다 — 원인이 감춰진다.
    #:   실측 2026-08-03: 세대 's5' 0건 · 모델 '' 0건인데 조용히 [] 였다.
    ensure_index_matches_release(conn)
    #: ★조각을 **먼저** 고르고, 그다음에 발생을 붙인다.
    #:
    #:   앞서는 발생을 조인한 뒤 `LIMIT` 을 걸었다. 한 조항이 최대 **170개 문서**에
    #:   실리므로 **같은 조항 하나가 결과를 다 채운다** — `LIMIT 8` 이 서로 다른
    #:   조항 8개가 아니라 같은 조항의 발생 8개가 된다. (코덱스 지적 2026-08-02)
    #:
    #:   그래서 조각을 유사도 순으로 먼저 뽑고(약관 필터는 `EXISTS` 로 걸어
    #:   범위 밖 조항이 자리를 차지하지 못하게 한다), 내용마다 대표 발생 하나를 붙인다.
    filt = (
        "AND EXISTS (SELECT 1 FROM policy_clause_occurrence o2 "
        "WHERE o2.content_hash = c.content_hash AND o2.sha256 = ANY(%(shas)s) "
        "AND o2.index_generation = %(gen)s)"
        if sha256s is not None
        #: ★약관을 안 가둬도 **세대는 가둔다.** 옛 세대 조각이 올라오면
        #:   부록을 삼킨 조항이 근거로 붙는다.
        else "AND EXISTS (SELECT 1 FROM policy_clause_occurrence o2 "
             "WHERE o2.content_hash = c.content_hash AND o2.index_generation = %(gen)s)"
    )
    #: ★HNSW 후보를 거리순으로 먼저 줄인다. 이전 쿼리는 `DISTINCT ON` 을
    #:   전체 모델 청크에 먼저 적용해 HNSW 인덱스를 사실상 쓰지 못했고,
    #:   S7.1 12만 청크에서 top20 p50 5.42초였다. 내용당 여러 청크가 있으므로
    #:   요청 수의 50배(최소 1,000)를 후보로 뽑은 뒤 내용 중복을 제거한다.
    #:   넉넉한 풀은 top-k 의미를 유지하면서 전량 정렬만 피한다.
    sql = f"""
        WITH nearest AS (
            SELECT c.content_hash, c.chunk_ix, c.text,
                   c.embedding <-> %(q)s AS distance
            FROM policy_clause_chunk c
            WHERE c.embed_model = %(model)s {filt}
            ORDER BY c.embedding <-> %(q)s
            LIMIT %(pool)s
        ), best AS (
            SELECT DISTINCT ON (c.content_hash)
                   c.content_hash, c.chunk_ix, c.text,
                   c.distance
            FROM nearest c
            ORDER BY c.content_hash, distance
        )
        SELECT b.content_hash, b.chunk_ix, b.text, b.distance,
               o.sha256, o.insurer, o.qualified_no, o.section, o.title,
               o.page_from, o.page_to,
               --: ★부모 문서 회수. 조각으로 찾고 **조 전체**를 돌려준다.
               --:   `LEFT JOIN` 이다 — 본문이 없어도 결과를 **버리지 않는다.**
               --:   그 경우 `citable_text` 가 조각으로 떨어지고, 그건
               --:   적재가 반쪽이라는 신호다(`drop_incomplete` 가 처리).
               COALESCE(ct.text, '') AS full_text
        FROM best b
        LEFT JOIN policy_clause_content ct ON ct.content_hash = b.content_hash
        JOIN LATERAL (
            SELECT * FROM policy_clause_occurrence o
            WHERE o.content_hash = b.content_hash
              AND o.index_generation = %(gen)s
              {"AND o.sha256 = ANY(%(shas)s)" if sha256s is not None else ""}
            ORDER BY o.sha256, o.page_from
            LIMIT 1
        ) o ON TRUE
        --: ★하한을 넘는 것은 **버린다.** 근거가 못 되는 것을 근거로 주지 않는다.
        WHERE b.distance <= %(maxd)s
        ORDER BY b.distance
        LIMIT %(k)s
    """
    #: ★파이썬 리스트를 그냥 넘기면 `double precision[]` 로 가서
    #:   `operator does not exist: vector <-> double precision[]` 로 죽는다.
    #:   `register_vector()` 가 알아보는 것은 **numpy 배열**이다.
    import numpy as np

    q = np.asarray(query_vec, dtype=np.float32)
    params = {"q": q, "k": limit, "pool": max(limit * 50, 1000),
              "shas": sha256s, "maxd": max_distance,
              "gen": current_generation(), "model": current_embed_model()}
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return [ClauseHit(*row) for row in cur.fetchall()]


def stats(conn) -> dict:
    """적재 현황. **응답·리포트에 그대로 싣는다.**"""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), count(DISTINCT content_hash) FROM policy_clause_chunk")
            chunks, contents = cur.fetchone()
            cur.execute("SELECT embed_model, count(*), count(DISTINCT content_hash) "
                        "FROM policy_clause_chunk GROUP BY 1 ORDER BY 1")
            by_model = {m: {"chunks": c, "contents": d} for m, c, d in cur.fetchall()}
            cur.execute("SELECT count(*) FROM policy_clause_content")
            (bodies,) = cur.fetchone()
            cur.execute(
                "SELECT count(*), count(DISTINCT sha256) FROM policy_clause_occurrence"
            )
            occ, docs = cur.fetchone()
            #: ★세대·종류별로 **쪼개서** 보여준다. 합계만 보면 옛 세대가 섞인 걸 못 본다.
            cur.execute(
                "SELECT index_generation, source_kind, count(*), count(DISTINCT sha256) "
                "FROM policy_clause_occurrence GROUP BY 1,2 ORDER BY 1,2"
            )
            by_gen = {f"{g}/{k}": {"occurrences": n, "documents": d}
                      for g, k, n, d in cur.fetchall()}
    except Exception as exc:  # noqa: BLE001
        raise InfraError(f"인덱스 A 현황을 읽지 못했습니다: {exc}") from exc
    return {
        "chunks": chunks,
        "distinct_contents": contents,
        "bodies": bodies,
        "occurrences": occ,
        "documents": docs,
        #: ★검색이 실제로 보는 세대. 합계와 나란히 놓아야 오염이 눈에 띈다.
        "current_generation": current_generation(),
        "current_embed_model": current_embed_model(),
        "by_embed_model": by_model,
        "by_generation": by_gen,
    }


def demote_occurrences(conn, rows, *, generation: str) -> dict:
    """게이트에 걸린 조항의 **기존 발생행만** 갱신한다. 새로 넣지 않는다.

    ★왜 필요한가 (2026-08-26 · 코덱스 교차검증에서 잡혔다)

        적재기는 게이트에 걸린 조항을 `continue` 로 건너뛰어 **발생을 아예 안 보냈다.**
        그래서 어떤 조항이 인용 가능(`True`)이었다가 **불가로 바뀌면 DB 가 그 사실을
        못 듣는다** — 옛 `True` 와 옛 청크가 그대로 남는다.

        그리고 판정 경로(`pg_clause_store.load_clauses`)는 DB 의 그 `True` 를
        **현재 값으로 읽는다.** 즉 **인용 불가가 된 조항이 근거로 나갈 수 있었다.**
        이 프로젝트에서 가장 나쁜 방향의 결함이다(CLAUDE.md §0).

        ★코덱스 실측: 현행 스냅샷에 그런 행은 **0 / 189,305** 다.
          이미 오염된 것이 아니라 **막혀 있지 않았던** 것이다.

    ★★**INSERT 하지 않는다.** 넣으면 청크 없는 발생행이 늘고, 그게 곧 고아다.
      고치려는 것은 「없는 사실을 추가」가 아니라 **「낡은 사실을 갱신」**이다.
      DB 에 없던 조항은 `matched` 에 안 잡히고, 그건 정상이다.

    Args:
        rows: ``(content_hash, sha256, gate_dict)`` 목록.

    돌려주는 것
        ``matched``   실제로 갱신한 행 수
        ``was_true``  ★그중 `citation_eligible` 이 **True 였던** 행 수.
                      0 이 아니면 그전까지 **잘못된 근거가 나갈 수 있었다**는 뜻이다.
    """
    out = {"matched": 0, "was_true": 0}
    with conn.cursor() as cur:
        for h, sha, gate in rows:
            #: 바꾸기 **전에** 센다 — 바꾼 뒤에는 알 수 없다.
            cur.execute(
                "SELECT count(*) FROM policy_clause_occurrence "
                " WHERE content_hash=%s AND sha256=%s AND index_generation=%s "
                "   AND citation_eligible IS TRUE",
                (h, sha, generation),
            )
            out["was_true"] += cur.fetchone()[0]
            cur.execute(
                "UPDATE policy_clause_occurrence SET "
                #: ★여기서는 `COALESCE` 를 쓰지 않는다. **덮어쓰는 것이 목적**이다 —
                #:   「이 조항은 이제 인용 불가다」를 확실히 기록한다.
                "  citation_eligible = %s,"
                #: 나머지 셋은 모르면 기존 값을 둔다(지어내지 않는다).
                "  chunk_type   = COALESCE(%s, chunk_type),"
                "  is_statute   = COALESCE(%s, is_statute),"
                "  parse_status = COALESCE(%s, parse_status)"
                " WHERE content_hash=%s AND sha256=%s AND index_generation=%s",
                (gate.get("citation_eligible"), gate.get("chunk_type"),
                 gate.get("is_statute"), gate.get("parse_status"),
                 h, sha, generation),
            )
            out["matched"] += cur.rowcount
    conn.commit()
    return out


def reconcile_occurrences(conn, *, generation: str, artifact_hashes,
                          source_kinds=("clause", "annex"), apply: bool = False,
                          backup_table: str | None = None,
                          protect_usable: bool = True, reason: str = "",
                          prune_missing_artifact: bool = False) -> dict:
    """발생행을 **현행 산출물과 맞춘다.** 산출물에서 사라진 행을 골라낸다.

    ★왜 필요한가 (2026-08-25 실측)

        적재는 `upsert` 만 한다 — 산출물에서 **없어진** 조항의 발생행을 안 지운다.
        추출기가 바뀌어 조항 경계가 달라지면 해시가 바뀌는데, 청크는 현행 산출물로
        다시 만들어지므로 **옛 해시의 발생은 가리킬 청크가 없다.** 그게 고아다.
        무작위 15문서 표본에서 고아 270행 중 **176행(65.2%)** 이 이 무리였다.

    ★★**증거 없이 지우지 않는다.**

        `artifact_hashes` 에 그 문서(`sha256`)의 항목이 **없으면 건너뛴다.**
        「산출물을 못 읽었다」와 「산출물에 그 조항이 없다」는 전혀 다른 말인데,
        뭉개면 **읽기에 실패했다는 이유로 멀쩡한 행을 지운다.**
        건너뛴 문서 수를 세어 돌려준다 — 조용히 넘기지 않는다(CLAUDE.md §3).

    ★★**산출물이 낳지 않은 행은 심판하지 않는다** (2026-08-25, 지우기 직전에 잡았다).

        처음 판에는 `source_kind` 를 안 봤다. 그랬더니 삭제 후보 14,378행 중
        **850행이 `citation_eligible=True` 이고 청크도 있었다** — 살아 있는 인용 가능 행이다.
        정체는 S7.1 승인 OCR fact(`source_kind='approved_ocr_table_fact'`)로,
        `load_s7_1_approved_facts.py` 가 **다른 출처에서** 넣은 것이다.
        구조화 산출물에 없는 게 당연한데, 「산출물에 없으니 낡았다」로 읽어 지울 뻔했다.

        그래서 대조 대상을 `source_kinds` 로 **명시해서 받는다**(기본 `clause`·`annex`).
        내가 모르는 출처가 또 생겨도 그건 심판 대상이 아니다.

    ★★안전장치를 하나 더 건다 — **청크가 있거나 인용 가능한 행은 안 지운다.**
        위 `source_kinds` 만으로도 막히지만, 출처 라벨이 틀렸을 때를 대비한다.
        지우려는 것은 「아무도 못 쓰는 낡은 행」이지 「쓰이는 행」이 아니다.
        걸린 수를 `protected` 로 돌려준다 — 0이 아니면 **뭔가 잘못된 것이다.**

    ★★★그런데 그 안전장치는 **전량 재생성 뒤에는 전제가 깨진다** (2026-08-27 실측).

        이 장치는 「낡은 행 ⇒ 청크가 없다」를 전제한다. 조항 경계가 바뀌면 해시가
        바뀌고 새 해시로만 청크를 만드니, 옛 해시는 가리킬 청크가 없다 —
        **한 문서만 놓고 보면 맞다.**

        그런데 내용은 **해시로 공유된다.** 실측(s6): 조항 등장의 65%가 중복이고
        한 조항이 최대 170개 문서에 실린다. 그래서 A 문서에서 사라진 해시가
        B 문서에는 그대로 남아 **청크도 남는다.** 인용 가능도 마찬가지다.

        실제로 s6 전량 재생성 뒤 낡은 행 **64,171개 중 64,171개 전부**가 이 장치에
        걸려 하나도 못 지웠다. 그 상태로 순번을 다시 매기면 낡은 행이 **섞인 채**
        번호를 받아 산출물과 자리가 어긋난다 — 실측 일치율 **14.70%**.
        즉 이 장치가 켜져 있으면 **재색인이 끝나지 않는다.**

        그래서 끄는 갈래를 **명시적으로** 만든다(`protect_usable=False`).
        ★그냥은 못 끈다 — `reason` 을 적어야 하고, 결과에 그대로 담아 돌려준다.
        ★★끄더라도 `source_kinds` 밖(예: `approved_ocr_table_fact`)은 여전히
          심판하지 않는다. 그 보호는 **끄는 대상이 아니다** — 850행을 지울 뻔했던
          그 사고를 막는 것은 이쪽이지 이 장치가 아니었다.

    ★★**되돌릴 수 있게 지운다.** `apply=True` 면 지우기 **전에** 지울 행을
        `backup_table` 에 그대로 복사한다(같은 트랜잭션). 잘못됐으면

            INSERT INTO policy_clause_occurrence SELECT * FROM <backup_table>;

        한 줄로 돌아온다. 백업이 실패하면 **삭제도 안 한다** —
        되돌릴 수 없는 삭제를 하지 않는다.

    ★기본은 **조회만**(`apply=False`). 지우는 것은 명시적으로 켜야 한다.

    Args:
        artifact_hashes: ``{sha256: set[content_hash]}``. 그 문서의 현행 산출물이
            담고 있는 내용 해시 전부(조항 + 부록).
        apply: True 면 실제로 지운다. False 면 세기만 한다.

    돌려주는 것
        ``documents_checked``  대조한 문서 수
        ``documents_skipped``  산출물이 없어 **건너뛴** 문서 수
        ``stale_rows``         산출물에 없는 발생행 수
        ``protected``          쓰이는 행이라 삭제에서 **뺀** 수(0 이 아니면 원인을 봐야 한다)
        ``backup_table``       지운 행을 복사해 둔 테이블 이름
        ``backed_up``          백업에 복사한 행 수(``deleted`` 와 같아야 한다)
        ``deleted``            실제로 지운 행 수(``apply=False`` 면 0)
        ``orphans_before``     실행 전 고아 발생 행 수
        ``orphans_after``      실행 후 고아 발생 행 수(``apply=False`` 면 before 와 같다)
    """
    def _orphans() -> int:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM policy_clause_occurrence o WHERE NOT EXISTS ("
                "  SELECT 1 FROM policy_clause_chunk c WHERE c.content_hash = o.content_hash)"
            )
            return cur.fetchone()[0]

    kinds = sorted(source_kinds)
    if not protect_usable and not str(reason).strip():
        #: ★이유 없이 안전장치를 끄지 않는다. 나중에 「왜 껐나」를 답할 수 있어야 한다.
        raise ValueError(
            "protect_usable=False 로 부르려면 reason 을 적어야 합니다. "
            "이 장치는 「낡은 행 ⇒ 청크 없음」을 전제하는데, 내용이 문서 사이에 "
            "공유되면(실측 중복 65%) 그 전제가 깨집니다 — 그 사실을 적어 두세요."
        )
    if prune_missing_artifact and not str(reason).strip():
        #: ★이쪽은 더 위험하다 — 문서를 **통째로** 지운다. 이유 없이는 안 된다.
        raise ValueError(
            "prune_missing_artifact=True 로 부르려면 reason 을 적어야 합니다. "
            "「산출물이 없다」는 「제외됐다」와 「읽기에 실패했다」 둘 다일 수 있습니다. "
            "전처리 대상 목록과 대조해 제외를 확인했다는 근거를 적으세요."
        )
    out = {"documents_checked": 0, "documents_skipped": 0, "stale_rows": 0,
           "protected": 0, "deleted": 0, "source_kinds": kinds,
           "protect_usable": bool(protect_usable), "reason": str(reason),
           "prune_missing_artifact": bool(prune_missing_artifact),
           "missing_artifact_rows": 0, "missing_artifact_deleted": 0,
           "backup_table": None, "backed_up": 0,
           "orphans_before": _orphans(), "orphans_after": 0}

    if apply:
        #: ★이름을 호출자가 준다 — 시각을 여기서 만들면 재실행 때 테이블이 흩어진다.
        if not backup_table:
            raise ValueError(
                "지우려면 backup_table 을 줘야 합니다. 되돌릴 수 없는 삭제는 하지 않습니다."
            )
        if not backup_table.replace("_", "").isalnum():
            #: ★이름을 SQL 에 그대로 넣으므로 형태를 좁힌다.
            raise ValueError(f"백업 테이블 이름이 이상합니다: {backup_table!r}")
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS {backup_table} "
                "(LIKE policy_clause_occurrence INCLUDING DEFAULTS)"
            )
        conn.commit()
        out["backup_table"] = backup_table

    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT sha256 FROM policy_clause_occurrence WHERE index_generation = %s",
            (generation,),
        )
        shas = [r[0] for r in cur.fetchall()]

    for sha in shas:
        keep = artifact_hashes.get(sha)
        if not keep:
            #: ★산출물을 못 봤다. **모르는 것을 근거로 지우지 않는다.**
            out["documents_skipped"] += 1
            if prune_missing_artifact:
                #: ★★**「없다」를 「제외됐다」로 읽는 갈래.** 기본은 꺼져 있다.
                #:   부르는 쪽이 **전처리 대상 목록과 대조해** 정말 제외된 것인지
                #:   확인한 뒤에만 켜야 한다 — 읽기에 실패한 경우와 구분이 안 되면
                #:   멀쩡한 문서를 통째로 날린다.
                #:   실측(2026-08-27): 이렇게 남은 11문서 664행(인용가능 646)은
                #:   비의료실손 격리 5건 + 판매개시일 미상 6건이었고,
                #:   `run_all --dry-run` 대상 1,355건 어디에도 없었다.
                w = (" WHERE sha256 = %s AND index_generation = %s"
                     "   AND source_kind = ANY(%s)")
                p = (sha, generation, kinds)
                with conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM policy_clause_occurrence" + w, p)
                    n = cur.fetchone()[0]
                    out["missing_artifact_rows"] += n
                    if n and apply:
                        cur.execute(f"INSERT INTO {backup_table} "
                                    "SELECT * FROM policy_clause_occurrence" + w, p)
                        b = cur.rowcount
                        cur.execute("DELETE FROM policy_clause_occurrence" + w, p)
                        d = cur.rowcount
                        if b != d:
                            conn.rollback()
                            raise RuntimeError(
                                f"백업 {b}행 ≠ 삭제 {d}행 — 되돌렸습니다.")
                        out["backed_up"] += b
                        out["deleted"] += d
                        out["missing_artifact_deleted"] += d
                        conn.commit()
            continue
        out["documents_checked"] += 1
        #: 대조 대상: 이 산출물이 낳은 출처만. 그 밖은 애초에 세지도 않는다.
        where = (" WHERE sha256 = %s AND index_generation = %s AND source_kind = ANY(%s)"
                 "   AND NOT (content_hash = ANY(%s))")
        params = (sha, generation, kinds, sorted(keep))
        #: ★안전장치 — 쓰이는 행(청크 있음 또는 인용가능)은 제외한다.
        #: ★★`protect_usable=False` 면 이 절을 안 붙인다. 전량 재생성 뒤에는 전제가
        #:   깨지기 때문이다(위 docstring 참조). `source_kinds` 보호는 **그대로 남는다** —
        #:   위 `where` 에 이미 들어 있고 여기서 끄지 않는다.
        safe = ("" if not protect_usable else
                " AND NOT EXISTS (SELECT 1 FROM policy_clause_chunk c"
                "                 WHERE c.content_hash = policy_clause_occurrence.content_hash)"
                " AND citation_eligible IS DISTINCT FROM TRUE")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM policy_clause_occurrence" + where, params)
            n = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM policy_clause_occurrence" + where + safe, params)
            deletable = cur.fetchone()[0]
            out["stale_rows"] += n
            out["protected"] += n - deletable
            if deletable and apply:
                #: ★지우기 **전에** 그대로 복사한다. 같은 트랜잭션이라
                #:   복사가 실패하면 삭제도 안 일어난다.
                cur.execute(
                    f"INSERT INTO {backup_table} "
                    "SELECT * FROM policy_clause_occurrence" + where + safe, params)
                out["backed_up"] += cur.rowcount
                cur.execute("DELETE FROM policy_clause_occurrence" + where + safe, params)
                out["deleted"] += cur.rowcount
                if out["backed_up"] != out["deleted"]:
                    conn.rollback()
                    raise RuntimeError(
                        f"백업 {out['backed_up']}행 ≠ 삭제 {out['deleted']}행 — 되돌렸습니다."
                    )
        if apply:
            conn.commit()

    out["orphans_after"] = _orphans() if apply else out["orphans_before"]
    return out


__all__ = [
    "ClauseHit",
    "demote_occurrences",
    "reconcile_occurrences",
    "CHUNK_SIZE",
    "CHUNK_OVERLAP",
    "ensure_schema",
    "chunk_clause",
    "drop_incomplete",
    "existing_hashes",
    "search",
    "stats",
    "upsert_chunks",
    "upsert_content",
    "upsert_occurrences",
]
