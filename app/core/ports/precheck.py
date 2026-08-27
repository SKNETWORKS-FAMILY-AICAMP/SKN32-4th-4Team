"""사전판정 유스케이스가 바깥에 요구하는 것 — 포트.

이 모듈은 **프레임워크도 바깥 계층도 모른다**(클린아키텍처 2단계 안쪽).
구현은 `app/adapters/` 가 한다.

★왜 이 포트가 따로 필요한가

    `app/core/ports/insurance.py` 에 이미 `PolicyVersionResolverPort` 가 있다.
    그건 **DB 에 적재된 뒤**를 전제로 `product_id` 로 해소한다.

    지금 프로토타입은 **매니페스트 파일**을 직접 읽는다. 아직 DB 적재 전이다.
    그래서 "보험사명 + 가입일 + 상품명"으로 찾는 **파일 단계 포트**를 따로 둔다.

    ★DB 적재가 끝나면 이 포트는 사라지고 `PolicyVersionResolverPort` 로 합친다.
      두 개가 영구히 공존하면 안 된다 — 같은 일을 두 곳에서 하게 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable


#: ★확정되지 않은 문서로 판정할 것인가. **기본은 아니다.**
#:
#:   지금 확정 문서가 0건이라 이걸 켜면 판정 가능 문서가 0이 된다.
#:   그래서 프로토타입 시연을 위해 끌 수 있게 뒀다. 다만 **끄면 응답에 표시한다** —
#:   조용히 미확정 약관으로 답하면 그게 가장 위험하다.
#:
#:   환경변수 `PRECHECK_ALLOW_UNCONFIRMED=1` 로 끈다.
import os

REQUIRE_CONFIRMED = os.getenv("PRECHECK_ALLOW_UNCONFIRMED", "").strip() not in (
    "1",
    "true",
    "TRUE",
)


#: ★★**세대 검토 상태의 어휘는 여기 하나뿐이다.**
#:
#:   실측 2026-08-26 — 게이트가 `("reviewed", "partial")` 만 받는데 확정 원장은
#:   `verified` 54건 · `not_applicable` 3건을 쓰고 있었다. 말이 안 맞아
#:   **확정까지 끝난 문서 57건이 판정에서 조용히 빠졌다**(1,353 → 1,296).
#:   KB손해보험은 LIG 시절 약관 4건이 통째로 검색에도 안 잡혔다.
#:
#:   그래서 어휘를 상수로 꺼내 **한 곳에서만** 정한다. 원장을 쓰는 쪽이 새 낱말을
#:   만들면 `tests/test_generation_review_vocabulary.py` 가 곧바로 깨진다 —
#:   조용히 빠지는 것보다 시끄럽게 깨지는 편이 낫다(CLAUDE.md §3).
#:
#:   `verified`       사람이 세대를 확인했다. `partial` 보다 **강한** 주장이다.
#:   `reviewed`       규칙셋 검토를 마쳤다.
#:   `partial`        일부만 검토됐다 — 그래도 세대는 정해졌다.
#:   `not_applicable` 세대 규칙이 **해당하지 않는** 문서다(노후실손 특별약관 등).
#:                    세대를 잘못 적용할 위험 자체가 없으므로 막을 이유가 없다.
GENERATION_REVIEW_OK = frozenset({"verified", "reviewed", "partial", "not_applicable"})

#: 검토가 **아직 안 된** 상태. 이건 막는다.
GENERATION_REVIEW_BLOCKED = frozenset({"", "pending", "unreviewed"})

#: 게이트가 아는 말 전부. 여기 없는 값이 원장에 나타나면 테스트가 깨진다.
GENERATION_REVIEW_KNOWN = GENERATION_REVIEW_OK | GENERATION_REVIEW_BLOCKED


@dataclass(frozen=True)
class PolicyVersionRow:
    """매니페스트 한 줄을 판정에 필요한 만큼만 투영한 것.

    ★`app/core/domain/insurance.PolicyVersion` 과 **다른 것**이다.
      저쪽은 DB 적재 후의 도메인 엔티티(`date` 타입·`identification_status`)이고,
      이쪽은 **파일에서 막 읽은 값**이다(`YYYYMMDD` 문자열).
      섞으면 "확정된 것"과 "아직 아닌 것"이 구분되지 않는다.
    """

    insurer: str
    product_name: str
    sale_start: str
    sale_end: str
    generation: int | None
    generation_label: str
    product_line: str
    sha256: str
    date_confidence: str
    generation_confidence: str
    generation_review: str = ""

    @property
    def is_rider(self) -> bool:
        """특별약관(부가 특약)인가."""
        from app.core.domain.policy_naming import looks_like_rider

        return looks_like_rider(self.product_name)

    #: ★이 문서가 **무엇인지 확정됐는가**.
    #:   `unidentified` / `ambiguous` / `confirmed`.
    #:   받아왔다는 이유로 무엇인지 안다고 하지 않는다 — 수집과 식별은 별개다.
    identification: str = "unidentified"

    @property
    def usable_for_judgment(self) -> bool:
        """자동 판정에 쓸 수 있는가.

        ★**확정된 문서만** 쓴다.

            ERD 는 `policy_version.confirmed_document_id` 를 NOT NULL FK 로 두어
            "확인 안 된 약관으로 판정하지 않는다"를 **구조로** 만들었다.
            그런데 이 판정 코드가 매니페스트를 직접 읽으면서 그 절차를 우회했다 —
            확정 0건인데 `load_versions()` 가 **1,361건을 내줬다.**

            실측(2026-08-02): `identification` 이 `unidentified` 2,117행 ·
            확정 매니페스트 573행이 전부 `ambiguous`. **`confirmed` 는 0건이다.**

            ★그래서 지금 이 게이트를 켜면 판정 가능 문서가 **0이 된다.**
              기능이 죽은 것처럼 보이지만 **그게 정답이다** —
              확인 안 된 약관으로 "보장됩니다"라고 말하는 것보다 낫다.
              `REQUIRE_CONFIRMED` 로 끌 수 있게 두되 **기본은 켬**이다.
        """
        if REQUIRE_CONFIRMED and self.identification != "confirmed":
            return False
        #: ★★**세대 규칙셋이 검토됐는지도 본다.** 코덱스가 잡은 구멍이다 —
        #:   `generation_review` 를 포트까지 실어 놓고 **아무도 안 봤다.**
        #:   `GenerationRuleset.is_reviewed` 도 정의만 되어 있고 호출부가 없었다.
        #:
        #:   세대가 틀리면 **다른 세대 약관을 근거로 든다.** 2019년 가입자에게
        #:   4세대 자기부담률을 적용하는 식이다. 지금은 `REQUIRE_CONFIRMED`
        #:   때문에 어차피 0건이라 드러나지 않지만, 확정 절차가 붙는 순간
        #:   **조용히 새는 문이 된다.** 그 전에 막는다.
        #:
        #:   ★`""` 도 막는다. 값이 없으면 "검토했다"가 아니다.
        #:
        #: ★★2026-08-26 정정 — 여기 `("reviewed", "partial")` 이라고 **손으로 적어** 뒀는데,
        #:   원장은 `verified`·`not_applicable` 도 쓴다. 그래서 사람이 세대를 확인해 둔
        #:   문서 57건이 판정에서 조용히 빠졌다. 어휘는 `GENERATION_REVIEW_OK` 하나로 모은다.
        #:   ★모르는 낱말은 여전히 막는다(fail-closed). 다만 **모른 채로 조용히 막지는
        #:   않는다** — 원장 어휘를 테스트가 대조한다.
        if self.generation_review not in GENERATION_REVIEW_OK:
            return False
        if self.date_confidence not in ("exact", "month"):
            return False
        #: ★`00000000` 은 "모른다"를 뜻하는 자리표시자다. 날짜가 아니다.
        if not self.sale_start or self.sale_start == "00000000":
            return False
        return True


@dataclass(frozen=True)
class NotResolved:
    """확정하지 못했다. **추측하지 않는다.**"""

    reason_code: str
    message: str
    candidates: tuple[PolicyVersionRow, ...] = ()


@dataclass(frozen=True)
class ClauseRow:
    """조항 하나. 판정에 넘길 최소 단위."""

    sha256: str
    qualified_no: str
    clause_no: str
    section: str
    title: str
    text: str
    page_from: int
    page_to: int
    content_hash: str
    usable: bool = True
    unusable_reason: str = ""
    #: ── 공통 게이트(`app/core/domain/eligibility.py`)가 보는 값 ──
    #:   ★`None` 은 "모른다"다. `True`/`False` 와 다르다 — 모르면 못 쓴다(§0).
    citation_eligible: bool | None = None
    chunk_type: str | None = None
    is_statute: bool | None = None
    parse_status: str | None = None
    #: ★수록(occurrence) 순번. ★★**검색용 재번호다** — 조항 JSON 의 값이 아니다
    #:   (2026-08-27 정정). `assign_ordinals` 가 **색인에 든 행만** 줄세워 다시 매긴다.
    #:   게이트 판정이 바뀌면 이 값도 바뀐다. 영구 식별자로 쓰면 안 된다.
    ordinal: int | None = None
    #: ★★산출물(clauses.json)이 매긴 **원래** 순번. `occurrence_id` 는 이걸 쓴다.
    #:   게이트가 바뀌어도 안 바뀐다 — 그래서 영구 식별자에 넣을 수 있다.
    source_ordinal: int | None = None
    #: 조항인가 부록인가. 부록을 조 이름으로 인용하면 출처가 틀린다.
    source_kind: str = "clause"
    #: 어느 승인 릴리스에서 왔나. 인용 검증이 세대까지 맞춰 볼 수 있어야 한다.
    release_id: str = ""

    @property
    def occurrence_id(self) -> str:
        """**정확히 한 행**을 가리키는 식별자.

        ★`clause_id` 로는 부족하다(코덱스 지적) — `sha12` 로 자르고
          `page_from`·세대·조항/부록 구분이 없다. 인용 검증이
          "정확히 한 행이 조회될 때만 통과"하려면 이만큼이 필요하다.

        ★값이 하나라도 없으면 **빈 문자열**이다. 지어내지 않는다 —
          호출부가 "식별자가 없다"를 보고 기권해야 한다.

        ★★**형식이 바뀌었다: v2** (2026-08-27, 코덱스와 교차검증)

            v1  `릴리스:sha256:source_kind:ordinal`
            v2  `v2:릴리스:sha256:source_kind:source_ordinal:content_hash`

          v1 은 **검색용 재번호**(`ordinal`)를 영구 식별자에 넣고 있었다.
          그 번호는 `assign_ordinals` 가 **색인에 든 행만** 줄세워 매기고,
          색인에 무엇이 드는지는 **인용 게이트**가 정한다.
          곧 **게이트 판정이 바뀌면 어제 발급한 인용이 다른 조항을 가리킨다.**
          게이트는 판단이고 앞으로도 바뀐다 — 하루에 강등 17,820행이 난 적도 있다.

          실측 피해: `core.policy_clause.ordinal`(산출물 그대로)과 자리 일치가
          **62.51%** 뿐이라, 인용을 원장에 저장할 때 **30%가 실패**했다
          (표본 300건: 찾음 209 · 못 찾음 91 · 모호 0).
          ★틀린 조항이 저장되지는 않았다 — 조회에 `position(quote in body)>0` 가 있어
            어긋나면 실패한다(fail-closed). 그래도 30%를 못 남기는 것은 결함이다.

          v2 는 **게이트가 못 건드리는 두 값**만 쓴다 —
            · `source_ordinal` 산출물이 매긴 자리. 실측 (문서,종류,순번) **중복 0**
            · `content_hash`   그 자리에 실린 내용. 자리가 밀렸는지 바로 드러난다

          ★해시만으로는 부족하다 — 한 문서 안에서 같은 내용이 두 번 실리는 자리가
            **2,789개** 있다. 그래서 자리와 내용을 **함께** 본다(코덱스 지적).

        ★지금 바꾸는 이유 — 발급된 인용이 **0건**이다
          (`core.clause_reference` 0행 · `app.assessment_clause_citation` 0행).
          소급 피해 없이 형식을 바꿀 수 있는 창은 지금뿐이다.
        """
        #: ★`release_id` 가 없으면 **빈 문자열**이다. `'?'` 로 채우면
        #:   서로 다른 릴리스의 행이 **같은 식별자**를 갖는다(코덱스 지적).
        if (self.source_ordinal is None or not self.sha256
                or not self.release_id or not self.content_hash):
            return ""
        return (f"v2:{self.release_id}:{self.sha256}:{self.source_kind}"
                f":{self.source_ordinal}:{self.content_hash}")

    @property
    def clause_id(self) -> str:
        """문서 안에서 이 조항을 가리키는 식별자.

        ★`{sha12}/{qualified_no}` 만으로는 **유일하지 않다.**
          실측(v4 전량 1,367문서): `qualified_no` 중복 31,085건 / 1,181문서(86%).
          부 탐지 입도가 특약보다 굵어 서로 다른 특약이 한 라벨 아래 뭉친다.

              p73  보험료 자동납입 특별약관/1.   "보험료 납입"            324자
              p73  보험료 자동납입 특별약관/1.   "특별약관의 체결 및 효력"  798자

          그래서 **내용 해시**를 덧붙인다. 같은 자리에 실린 다른 내용이 구분된다.
          `content_hash` 는 조항 구조화 때 이미 계산해 둔 값이다.

        ★영속 식별자가 아니다. 재추출하면 부 라벨이 바뀔 수 있다.
          저장소에 넣을 때는 별도 UUID 를 발급하고 이건 조회 키로만 쓴다.
        """
        tail = f"#{self.content_hash[:8]}" if self.content_hash else ""
        return f"{self.sha256[:12]}/{self.qualified_no}{tail}"


@runtime_checkable
class PolicyVersionSourcePort(Protocol):
    """판정 대상 약관 버전 목록을 준다."""

    def load_versions(self) -> list[PolicyVersionRow]: ...

    def resolve(
        self,
        *,
        insurer: str,
        enrolled_on: str,
        product_name: str | None = None,
        versions: list[PolicyVersionRow] | None = None,
    ) -> PolicyVersionRow | NotResolved: ...


@runtime_checkable
class ClauseSourcePort(Protocol):
    """확정된 약관 버전의 조항을 준다.

    ★검색 범위를 약관 버전으로 **가둔다.** 전체에서 찾으면
      2019년 가입자에게 2024년 조항이 근거로 붙는다.
    """

    def load_clauses(self, sha256: str, *, usable_only: bool = True) -> list[ClauseRow]: ...

    def stats(self, sha256: str) -> dict: ...

    def search(self, sha256: str, query: str, *, limit: int = 8) -> Sequence[ClauseRow]: ...


@runtime_checkable
class RelatedClausePort(Protocol):
    """의미검색으로 **읽어 볼 만한 조항**을 찾는다.

    ★★**판정 근거를 주는 포트가 아니다.** `ClauseSourcePort` 와 나눠 둔 이유가 그것이다.

      · `ClauseSourcePort.load_clauses` → 질병기호가 **실제로 적힌** 조항을 찾는 재료.
        여기서 나온 것만 `citations` 가 되고, 판정을 바꾼다.
      · `RelatedClausePort.find` → 벡터 유사도가 고른 조항. **유사도는 근거가 아니다.**
        사람이 읽을 참고 자료로만 나가고(`related_clauses`), 판정은 건드리지 않는다.

      두 포트를 하나로 합치면 조립 지점이 실수로 벡터 검색을 판정 재료로 꽂을 수 있다.
      타입으로 막는다.

    ★범위는 **약관 한 벌**로 가둔다(`sha256`). 전역으로 열면 2019년 가입자에게
      2024년 조항이 참고로 붙는다 — 참고라도 그건 틀린 참고다.

    ★못 찾으면 빈 목록, 못 하면 **예외**다. 둘을 같게 만들지 않는다 —
      호출부가 「관련 조항 없음」과 「검색 실패」를 구별해야 한다(CLAUDE.md §0).
    """

    def find(self, sha256: str, query: str, *, limit: int = 5) -> Sequence[ClauseRow]: ...
