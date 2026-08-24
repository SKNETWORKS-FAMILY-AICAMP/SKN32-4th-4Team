"""질병명 → 질병코드 유스케이스 — 팀 MVP 기능 4.

"우울증을 넣으면 관련 질병코드를 매칭해서 어떤 항목이 보장되고 안 되는지 알려준다."

★이 유스케이스가 절대 하지 않는 것: **코드를 하나로 자동 확정하는 것.**

    '우울증'은 코드 하나가 아니다. F32(우울에피소드) · F33(재발성 우울장애) ·
    F34.1(기분저하증) 등으로 갈리고, **약관 판정이 코드마다 다르다.**
    자동으로 하나를 골라 판정하면 틀린 답을 확신 있게 주게 된다.

    처방전 OCR과 같은 원칙이다 — 후보를 제시하고, 확정은 사용자가 한다.

또한 이것은 **의료 진단이 아니다.** "당신의 증상은 F32입니다"가 아니라
"F32는 이 약관에서 이렇게 다뤄집니다"까지가 범위다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.domain.insurance import KcdCode
from app.core.errors import NotFoundErr, ValidationErr
from app.core.ports.insurance import KcdLookupPort

#: 후보를 너무 많이 보여주면 사용자가 고를 수 없다.
DEFAULT_CANDIDATE_LIMIT = 8


@dataclass(frozen=True)
class DiagnosisCandidates:
    """사용자에게 제시할 후보 목록.

    ``requires_user_selection`` 은 항상 ``True`` 다. 후보가 하나뿐이어도 마찬가지다 —
    검색 결과가 하나라는 것이 그것이 정답이라는 뜻은 아니다.
    """

    query: str
    candidates: tuple[KcdCode, ...]
    requires_user_selection: bool = True

    @property
    def is_ambiguous(self) -> bool:
        return len(self.candidates) > 1


class LookupDiagnosisCodes:
    """질병명으로 KCD 후보를 찾는다. 고르지는 않는다."""

    def __init__(self, kcd: KcdLookupPort, *, limit: int = DEFAULT_CANDIDATE_LIMIT) -> None:
        if limit < 1:
            raise ValidationErr("limit 은 1 이상이어야 합니다.")
        self._kcd = kcd
        self._limit = limit

    def run(self, *, name: str) -> DiagnosisCandidates:
        """Raises:
        ValidationErr: 질병명이 비었을 때.
        NotFoundErr: 후보가 하나도 없을 때. **비슷한 것을 대신 내주지 않는다.**
        """
        cleaned = name.strip()
        if not cleaned:
            raise ValidationErr("질병명이 비어 있습니다.")

        found = tuple(self._kcd.candidates_by_name(name=cleaned, limit=self._limit))
        if not found:
            raise NotFoundErr(
                f"'{cleaned}'에 해당하는 질병분류기호를 찾지 못했습니다. "
                "임의로 유사한 코드를 대신 사용하지 않습니다."
            )

        return DiagnosisCandidates(query=cleaned, candidates=found)


class ConfirmDiagnosisCode:
    """사용자가 고른 코드를 확정한다. 여기서부터 판정에 쓸 수 있다."""

    def __init__(self, kcd: KcdLookupPort) -> None:
        self._kcd = kcd

    def run(self, *, version_label: str, code: str) -> KcdCode:
        """코드 문자열만으로는 유일하지 않으므로 차수(``version_label``)를 함께 받는다."""
        if not version_label.strip() or not code.strip():
            raise ValidationErr("KCD 차수와 코드가 모두 필요합니다.")
        return self._kcd.get(version_label=version_label.strip(), code=code.strip())
