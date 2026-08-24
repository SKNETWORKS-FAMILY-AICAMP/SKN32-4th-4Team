"""호출자 선언 — "사람인가 AI인가"를 정직하게 표현한다.

팀 제안: 접속자에게 **설문처럼** "AI 에이전트면 체크해 주세요"를 권장한다.
차단 게이트가 아니라 자발적 선언이므로 도입 자체는 문제없다. 문제는 **표현 방식**이다.

★이 모듈이 막는 것: `is_ai: bool` 하나로 저장하는 것.

    그렇게 두면 검증된 고정 신원처럼 보이고, 곧 "우리 사용자 중 30%가 AI"라는
    **검증되지 않은 통계**가 만들어진다. 자기선언은 거짓일 수 있고,
    에이전트는 호출량이 많아 '요청 비율'과 '사용자 비율'이 전혀 다르다.

그래서 네 축을 분리한다 — 무엇이라 주장했나 / 누구를 대리하나 / 근거가 무엇인가 / 검증됐는가.
(초안의 `classify() -> "declared"|"inferred"|"unknown"` 은 앞의 두 축과 뒤의 두 축을
한 값에 섞고 있었다. Codex 지적으로 분리했다.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ClientSoftwareKind(str, Enum):
    """무엇이라고 **주장**했는가. 사실이라는 뜻이 아니다."""

    HUMAN_BROWSER = "human_browser"
    AI_AGENT = "ai_agent"
    AUTOMATED_SYSTEM = "automated_system"
    OTHER = "other"
    #: 응답하지 않을 자유. 미응답에 어떤 불이익도 두지 않는다.
    PREFER_NOT_TO_SAY = "prefer_not_to_say"
    #: 아예 묻지 못했거나 채널에 선언 수단이 없는 경우.
    UNKNOWN = "unknown"


class RepresentedPartyKind(str, Enum):
    """누구를 대리하는가.

    ★'AI인가'와 'AI가 사람을 대리하는가'는 다른 질문이다.
    사람을 대리하는 에이전트와 자율 실행 에이전트는 다르게 다뤄야 한다.
    """

    HUMAN_END_USER = "human_end_user"
    ORGANIZATION = "organization"
    SELF_OPERATING_AGENT = "self_operating_agent"
    UNKNOWN = "unknown"


class ClaimBasis(str, Enum):
    """이 값을 무엇을 근거로 알았는가."""

    #: 호출자가 스스로 밝혔다(설문 체크박스, MCP `_meta`, REST 헤더).
    SELF_DECLARATION = "self_declaration"
    #: 인증된 클라이언트 메타데이터(OAuth client 등록 정보).
    AUTHENTICATED_CLIENT_METADATA = "authenticated_client_metadata"
    #: 행동 신호로 추정했다. ★자기선언과 절대 같은 필드에 합치지 않는다.
    INFERRED = "inferred"
    #: 근거가 없다.
    NONE = "none"


class VerificationStatus(str, Enum):
    """검증됐는가. 자기선언은 언제나 ``UNVERIFIED`` 다."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"


class DeclarationChannel(str, Enum):
    """어디서 받았는가.

    ★웹 체크박스는 **사람용 UI**다. 에이전트는 HTML을 안 볼 수 있으므로
    주 채널이 될 수 없다. 에이전트의 제대로 된 채널은 MCP `_meta` / REST 헤더 / A2A 카드다.
    """

    WEB_FORM = "web"
    REST_METADATA = "rest_metadata"
    MCP_METADATA = "mcp_metadata"
    A2A_AGENT_CARD = "a2a_agent_card"


@dataclass(frozen=True)
class ActorDeclaration:
    """호출자에 대한 하나의 선언 사건.

    갱신하지 않고 **매번 새 사건으로 쌓는다**(append-only). 마지막 값으로 덮어쓰면
    "언제 무엇을 선언했는지"가 사라져 감사와 삭제가 불가능해진다.
    """

    client_software_kind: ClientSoftwareKind
    represented_party_kind: RepresentedPartyKind
    claim_basis: ClaimBasis
    verification_status: VerificationStatus
    channel: DeclarationChannel
    declared_at: datetime

    @classmethod
    def unknown(cls, *, channel: DeclarationChannel, at: datetime) -> "ActorDeclaration":
        """선언을 받지 못한 경우. **사람이라고 가정하지 않는다.**"""
        return cls(
            client_software_kind=ClientSoftwareKind.UNKNOWN,
            represented_party_kind=RepresentedPartyKind.UNKNOWN,
            claim_basis=ClaimBasis.NONE,
            verification_status=VerificationStatus.UNVERIFIED,
            channel=channel,
            declared_at=at,
        )

    @property
    def is_usable_for_access_control(self) -> bool:
        """권한·레이트리밋·부정사용 판정에 써도 되는가.

        ★자기선언은 **언제나 안 된다.** 거짓으로 선언하면 그대로 통과하기 때문이다.
        인증된 클라이언트 메타데이터만 접근 통제의 근거가 될 수 있다.
        """
        return (
            self.claim_basis is ClaimBasis.AUTHENTICATED_CLIENT_METADATA
            and self.verification_status is VerificationStatus.VERIFIED
        )

    def describe(self) -> str:
        """대시보드·응답에 쓸 한 줄. **검증 여부를 빼고 말하지 않는다.**"""
        if self.claim_basis is ClaimBasis.NONE:
            return "클라이언트 유형 미상(선언 없음)"
        verified = "검증됨" if self.verification_status is VerificationStatus.VERIFIED else "미검증"
        return f"{self.client_software_kind.value} (근거: {self.claim_basis.value}, {verified})"
