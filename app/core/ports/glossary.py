"""용어 설명이 바깥에 요구하는 것 — 포트.

이 모듈은 **프레임워크도 바깥 계층도 모른다**(클린아키텍처 2단계 안쪽).

★용어 설명은 별도 RAG 가 아니다

    용어의 뜻은 약관 안에 있다(「제2조 용어의 정의」·「붙임1_용어의 정의」).
    코퍼스가 같으므로 인덱스도 하나다 — 두 벌로 두면 어긋났을 때
    무엇이 맞는지 판단할 근거가 없어진다.

★그래도 **유스케이스는 나눈다**

    | | 판정용 | 용어설명용 |
    |---|---|---|
    | 문서 범위 | 확정된 약관 버전 **하나로 고정** | 전역 |
    | 확정 여부 | `confirmed` 만 | 완화 가능 |
    | 출력 | `verdict` 4단 + 근거 + 면책 | **정의 인용뿐** |

    챗봇이 "그래서 저 보장되나요?" 로 자연스럽게 넘어가는데,
    **거기서 답하면 규칙엔진을 우회한다** — 약관버전 확정·인용검증·4단 판정을
    전부 건너뛴 답이 나간다. 그래서 이 경로의 출력에는 `verdict` 가 **없다.**

★필터는 **명시 인자로 받는다.** 기본값을 느슨하게 두면
  용어 경로의 완화된 필터가 판정 경로로 샌다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable


@dataclass(frozen=True)
class TermPassage:
    """용어 정의가 실린 **원문 구절 하나**.

    ★용어→뜻 쌍이 아니라 **구절**이다.

        정의표가 PDF 안에서 테두리 없는 표라 표 추출이 잡지 못한다.
        본문에서는 칸이 무너져 `용 어  정  의  계약 보험계약 …` 처럼 나오는데,
        여기서 "계약 = 보험계약" 이라고 끊어 읽는 것은 **추측이다.**
        끊는 규칙을 만들면 그럴듯하게 틀린 정의가 대량으로 만들어진다.
        원문을 그대로 두고 사람이 판단하게 한다.
    """

    #: `clause`(「용어의 정의」 조항) | `appendix`(붙임·별표 정의표)
    kind: str
    sha256: str
    insurer: str
    qualified_no: str
    section: str
    title: str
    page_from: int
    page_to: int
    content_hash: str
    text: str


@runtime_checkable
class GlossarySourcePort(Protocol):
    """용어가 나온 정의 구절을 준다.

    ★`insurer` 를 주면 그 보험사 약관만 본다. 안 주면 전역이다 —
      **전역이 기본인 것은 용어 경로뿐**이고, 판정 경로는 약관 버전 하나로 가둔다.

    ★`limit=0` 은 **상한 없음**이다. 몇 개 있는지 세어서 알려주려면
      끝까지 훑어야 한다 — 중간에 멈추면 상한값이 개수인 척한다.
    """

    def find(
        self, term: str, *, insurer: str | None = None, limit: int = 20
    ) -> Sequence[TermPassage]: ...

    def meta(self) -> dict: ...
