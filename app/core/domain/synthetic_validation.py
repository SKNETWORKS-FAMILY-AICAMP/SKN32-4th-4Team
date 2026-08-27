"""합성 시뮬레이션 제출의 결정론적 **정합성** 검사.

이 검사는 보험금 지급 진위, 실제 증빙, 약관상 보장 여부를 판단하지 않는다.
시뮬레이터가 약속한 형식과 값 범위를 지켰는지만 검사한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.core.domain.kcd_ranges import CodeRef

RULE_VERSION = "synthetic-consistency-v2"

ALLOWED_INSURERS = frozenset(
    {"삼성화재", "DB손해보험", "NH농협생명", "동양생명", "현대해상", "흥국화재"}
)
ALLOWED_AGE_BANDS = frozenset({"20대", "30대", "40대", "50대", "60대"})
ALLOWED_OUTCOMES = frozenset({"paid", "denied", "partial", "pending"})
ALLOWED_KCD_CODES = frozenset(
    {"S72.0", "J20.9", "E11.9", "M51.2", "C50.9", "K35.8", "H25.9", "N20.0"}
)

_RUN_ID = re.compile(r"^[0-9a-f]{12}$")
_CLIENT_REF = re.compile(r"^sim-agent-(\d{3})$")


@dataclass(frozen=True)
class GateDecision:
    accepted: bool
    rule_version: str
    reason_codes: tuple[str, ...]
    evidence: dict[str, bool | str]


def evaluate(payload: dict) -> GateDecision:
    """시뮬레이터 제출을 검사한다. 결과는 항상 사유와 검사 증거를 포함한다."""
    reasons: list[str] = []
    run_id = str(payload.get("simulation_run_id") or "").strip()
    client_ref = str(payload.get("client_ref") or "").strip()
    idem = str(payload.get("idempotency_key") or "").strip()
    case_no = payload.get("simulation_case_no")

    run_ok = bool(_RUN_ID.fullmatch(run_id))
    client_match = _CLIENT_REF.fullmatch(client_ref)
    ordinal_ok = isinstance(case_no, int) and 1 <= case_no <= 50
    expected_idem = (
        f"sim-{run_id}-{client_match.group(1)}-{case_no:03d}"
        if run_ok and client_match and ordinal_ok
        else ""
    )
    idem_ok = bool(expected_idem and idem == expected_idem)

    insurer_ok = str(payload.get("insurer") or "") in ALLOWED_INSURERS
    age_ok = payload.get("age_band") in ALLOWED_AGE_BANDS
    outcome_ok = str(payload.get("outcome") or "") in ALLOWED_OUTCOMES
    codes = payload.get("kcd_codes") or []
    code_format_ok = (
        isinstance(codes, list)
        and len(codes) == 1
        and CodeRef.parse(str(codes[0])) is not None
    )
    code_known_ok = code_format_ok and str(codes[0]).upper() in ALLOWED_KCD_CODES
    try:
        datetime.strptime(str(payload.get("enrolled_on") or ""), "%Y%m%d")
        date_ok = True
    except ValueError:
        date_ok = False

    checks = {
        "route_bound_to_synthetic": True,
        "run_id_valid": run_ok,
        "client_ref_valid": bool(client_match),
        "case_ordinal_valid": ordinal_ok,
        "idempotency_matches_run_case": idem_ok,
        "insurer_allowed": insurer_ok,
        "enrolled_on_valid": date_ok,
        "single_kcd_code_valid": code_format_ok,
        "kcd_code_in_simulator_catalog": code_known_ok,
        "age_band_allowed": age_ok,
        "outcome_allowed": outcome_ok,
    }
    for key, ok in checks.items():
        if ok is not True:
            reasons.append(key)

    return GateDecision(
        accepted=not reasons,
        rule_version=RULE_VERSION,
        reason_codes=tuple(reasons or ["all_checks_passed"]),
        evidence=checks,
    )


__all__ = [
    "ALLOWED_AGE_BANDS",
    "ALLOWED_INSURERS",
    "ALLOWED_KCD_CODES",
    "ALLOWED_OUTCOMES",
    "GateDecision",
    "RULE_VERSION",
    "evaluate",
]
