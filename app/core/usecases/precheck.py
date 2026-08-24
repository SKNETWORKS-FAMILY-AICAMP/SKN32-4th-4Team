"""보장 사전판정 파이프라인.

★단계

    1. resolve_policy   가입일 → 적용 약관 확정      못 하면 → abstain
    2. gate_document    그 문서가 판정에 쓸 만한가    아니면 → abstain
    3. retrieve         면책·보상 조항에서 코드 범위 수집
    4. assess           코드별 판정(규칙 기반)
    5. build_citations  근거 조항을 원문 위치까지 붙임

★이 모듈은 **프레임워크도 바깥 계층도 모른다**(클린아키텍처 2단계 안쪽).

    파일을 읽지 않는다. 필요한 것은 **포트로 주입받는다.**
    `policies` 는 `PolicyVersionSourcePort`, `clauses` 는 `ClauseSourcePort` 다.
    구현은 `app/adapters/` 가 한다.

★지금은 **LLM 이 없다.** 규칙만으로 판정한다.

    이유는 두 가지다.
      · 약관이 KCD 코드를 직접 쓰므로 규칙으로 답이 나온다(실측 80% 문서)
      · 규칙 판정이 먼저 서야 LLM 답을 **대조**할 수 있다
    LLM 은 설명문 생성과 애매한 사례에 붙이고, 그때 `citation_guard` 로 검증한다.

★`covered` 를 함부로 내지 않는다

    면책 목록에 없다 = 보장된다, 가 아니다. 보장은 '보상하는 사항' 조항이 정한다.
    지금 단계에서 확실히 말할 수 있는 것은 **면책 여부**뿐이므로,
    면책이 아니면 `unknown`(근거 부족)으로 둔다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict

from app.core.errors import InfraError, ValidationErr
from app.core.domain import kcd_ranges as kcd
from app.core.ports.precheck import (
    ClauseRow,
    ClauseSourcePort,
    NotResolved,
    PolicyVersionRow,
    PolicyVersionSourcePort,
)
from app.core.domain.insurance import Verdict
from app.core.domain.precheck_result import (
    AppliedPolicyInfo,
    CitationRef,
    CodeVerdict,
    EvidenceTier,
    PrecheckInput,
    PrecheckOutcome,
    ReasonCode,
)

RULE_ENGINE_VERSION = "rules-2026.08.02"

#: 판정 근거로 쓸 수 있는 문서 상태. ★`suspect` 는 쓰지 않는다.
_USABLE_PARSE_STATUS = {"ok"}


def _assessment_message(verdict: Verdict, assessments: list[CodeVerdict]) -> str:
    """채팅 요약에 사용할 판정 설명을 만든다.

    상세 근거는 ``per_code``와 ``citations``로 별도 제공하지만, 성공 응답의
    ``message``를 비워 두면 프론트가 의미 없는 기본 문구로 대체하게 된다.
    판정의 의미와 다음 행동을 한 문단에 고정해 HTTP·채팅 양쪽에서 같은 설명을
    사용한다.
    """
    codes = ", ".join(a.code for a in assessments) or "입력한 질병기호"
    if verdict is Verdict.UNLIKELY:
        return (
            f"{codes}에 대해 약관의 면책 조항과 일치하는 내용이 확인되었습니다. "
            "면책 가능성이 있는 결과이며, 아래에서 질병기호별 판단과 약관 원문 근거를 확인하세요. "
            "최종 지급 여부는 실제 사고 내용과 청구 서류에 따라 달라질 수 있습니다."
        )
    if verdict is Verdict.NEEDS_DOCUMENTS:
        return (
            f"{codes}에 대해 면책 예외 조건과 관련된 조항이 확인되었습니다. "
            "요양급여 해당 여부 등 추가 서류와 조건을 확인해야 하며, 아래에 세부 근거를 표시했습니다."
        )
    return (
        f"{codes}는 현재 확인한 면책 조항만으로는 보장 여부를 확정할 수 없습니다. "
        "면책 목록에 없다는 사실만으로 보장된다고 단정하지 않으며, 아래의 근거와 약관 정보를 확인하세요."
    )


def _trace_id(req: PrecheckInput) -> str:
    """같은 요청이면 같은 값. 감사·재현에 쓴다."""
    raw = json.dumps(
        {
            "i": req.insurer,
            "d": req.enrolled_on,
            "c": sorted(req.kcd_codes),
            "p": req.product_name or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _to_applied(v: PolicyVersionRow, parse_status: str = "") -> AppliedPolicyInfo:
    return AppliedPolicyInfo(
        insurer=v.insurer,
        product_name=v.product_name,
        sale_start=v.sale_start,
        sale_end=v.sale_end,
        generation=v.generation,
        generation_label=v.generation_label,
        product_line=v.product_line,
        sha256=v.sha256,
        date_confidence=v.date_confidence,
        generation_confidence=v.generation_confidence,
        parse_status=parse_status,
    )


#: ★"산출물이 없다"와 "저장소가 죽었다"를 가른다.
#:   전자는 **사실**이라 기권이 맞고, 후자는 **장애**라 503 으로 올려야 한다.
#:   메시지로 가르는 것은 임시방편이다 — 어댑터가 별도 예외 타입을 던지는 편이 낫다.
#:   (DB 적재 때 `ArtifactMissing` 을 만들어 교체한다)
_MISSING_HINTS = ("찾지 못했습니다", "산출물이 없습니다", "지정되지 않았습니다")


def _is_missing_artifact(e: Exception) -> bool:
    return any(h in str(e) for h in _MISSING_HINTS)


def _reason(code: str | None) -> ReasonCode | None:
    """`NotResolved.reason_code`(문자열) → `ReasonCode`.

    ★**조용한 스킵을 만들지 않는다** (CLAUDE.md §3).

        전에는 `_REASON_MAP.get(code)` 였다. 표에 없는 코드가 오면 **소리 없이 `None`**
        이 됐고, 에이전트는 `reason_code` 로 분기하므로 **왜 기권했는지 알 수 없어진다.**

        실제로 걸렸다 — `documents_not_confirmed` 를 추가했더니 응답의 `reason_code` 가
        `None` 으로 나갔다. 표를 같이 안 고쳤기 때문이다. **표가 하나 더 있는 구조 자체가
        결함**이라, 표를 없애고 enum 을 직접 쓴다.

        그래도 못 맞추면 `ValidationErr` 를 낸다 — 기권 응답에 `reason_code` 가 비는 것보다
        **개발 중에 터지는 편이 낫다.** 이 값은 사용자 입력이 아니라 우리 코드가 만든다.
    """
    if not code:
        return None
    try:
        return ReasonCode(code)
    except ValueError as e:  # noqa: PERF203
        raise ValidationErr(
            f"알 수 없는 reason_code '{code}' — ReasonCode enum 에 추가하세요."
        ) from e


def run(
    req: PrecheckInput,
    *,
    policies: PolicyVersionSourcePort,
    clauses: ClauseSourcePort,
    versions: list[PolicyVersionRow] | None = None,
) -> PrecheckOutcome:
    """사전판정 한 건.

    Args:
        policies: 약관 버전 출처(포트). 어댑터가 주입한다.
        clauses: 조항 출처(포트).
        versions: 미리 읽어 둔 목록(성능·테스트용).
    """
    if not req.kcd_codes:
        raise ValidationErr("질병기호가 비어 있습니다.")

    #: ★★**판정 한 건 안에서는 릴리스를 한 벌로 고정한다.**
    #:   도중에 승인 포인터가 바뀌면 조항은 새 세대, 벡터는 옛 프로필에서 와
    #:   **서로 다른 판의 근거가 한 답에 섞인다**(코덱스 라운드2 지적).
    #:
    #:   ★호출부가 이미 `pinned()` 안이면 **그 스냅샷을 물려받는다** —
    #:     그래프(`app/workflow/precheck_graph.py`)가 판정 뒤 인용 검증을 하는데,
    #:     거기가 밖이면 검증만 새 릴리스를 읽는다(코덱스 라운드3 지적).
    from app.core import release

    with release.pinned(release.current()):
        return _run(req, policies=policies, clauses=clauses, versions=versions)


def _run(
    req: PrecheckInput,
    *,
    policies: PolicyVersionSourcePort,
    clauses: ClauseSourcePort,
    versions: list[PolicyVersionRow] | None = None,
) -> PrecheckOutcome:
    trace = _trace_id(req)
    base = {
        "rule_engine_version": RULE_ENGINE_VERSION,
        "trace_id": trace,
    }

    # ── 1) 적용 약관 확정 ────────────────────────────────────────
    pool = versions if versions is not None else policies.load_versions()
    got = policies.resolve(
        insurer=req.insurer,
        enrolled_on=req.enrolled_on,
        product_name=req.product_name,
        versions=pool,
    )
    if isinstance(got, NotResolved):
        #: ★현행 약관으로 때우지 않는다. 못 정하면 못 정했다고 답한다.
        return PrecheckOutcome(
            verdict=Verdict.NEEDS_EXPERT,
            abstained=True,
            reason_code=_reason(got.reason_code),
            message=got.message,
            candidates=[_to_applied(c) for c in got.candidates],
            **base,
        )

    # ── 2) 그 문서를 판정에 쓸 수 있나 ────────────────────────────
    #: ★저장소 장애를 **정상 기권으로 삼키지 않는다.**
    #:
    #:   `except Exception` 으로 전부 잡아 `needs_expert`(HTTP 200)로 바꾸고 있었다.
    #:   그러면 DB 가 죽어도, 코드에 오타가 있어도 클라이언트는
    #:   **"근거가 없나 보다"** 라고 읽는다. 장애와 기권을 못 가린다.
    #:   라우터는 저장소 장애를 503 이라 규정하는데 여기서 200 으로 만들어 버린 것이다.
    #:
    #:   `InfraError` 는 올려보내고(라우터가 503 으로 바꾼다), 그 밖의 예외는
    #:   **숨기지 않는다** — 프로그래밍 오류가 기권으로 둔갑하면 버그를 못 찾는다.
    #:
    #:   ★"산출물이 아직 없다"는 **장애가 아니라 사실**이므로 기권이 맞다.
    try:
        st = clauses.stats(got.sha256)
    except InfraError as e:
        if not _is_missing_artifact(e):
            raise
        return PrecheckOutcome(
            verdict=Verdict.NEEDS_EXPERT,
            abstained=True,
            reason_code=ReasonCode.NO_EVIDENCE,
            message="이 약관은 아직 조항 구조화가 되지 않아 근거를 댈 수 없습니다.",
            applied_policy=_to_applied(got),
            **base,
        )

    applied = _to_applied(got, st["parse_status"])
    if st["parse_status"] not in _USABLE_PARSE_STATUS:
        #: ★구조화가 미심쩍은 문서로 "보장됩니다"라고 말하지 않는다.
        return PrecheckOutcome(
            verdict=Verdict.NEEDS_EXPERT,
            abstained=True,
            reason_code=ReasonCode.DOCUMENT_NOT_RELIABLE,
            message=(
                f"이 약관은 조항 구조화 상태가 '{st['parse_status']}' 라 "
                "근거를 정확히 대기 어렵습니다. 사람이 확인해야 합니다."
            ),
            applied_policy=applied,
            **base,
        )

    # ── 3) 코드 언급 수집 ────────────────────────────────────────
    found = clauses.load_clauses(got.sha256)
    mentions: list[tuple[kcd.CodeMention, ClauseRow]] = []
    for c in found:
        for m in kcd.scan_clause(c.text):
            mentions.append((m, c))
    if not mentions:
        return PrecheckOutcome(
            verdict=Verdict.NEEDS_EXPERT,
            abstained=True,
            reason_code=ReasonCode.NO_EVIDENCE,
            message="이 약관에서 질병기호로 적힌 조항을 찾지 못했습니다.",
            applied_policy=applied,
            **base,
        )

    # ── 4) 코드별 판정 ──────────────────────────────────────────
    per_code: list[CodeVerdict] = []
    all_cites: list[CitationRef] = []
    for code in req.kcd_codes:
        judged = kcd.judge(code, [m for m, _ in mentions])
        if judged["status"] == "invalid_code":
            per_code.append(
                CodeVerdict(
                    code=code,
                    verdict=Verdict.NEEDS_EXPERT,
                    reason_code=ReasonCode.INVALID_CODE,
                    note="질병기호 형식이 아닙니다(예: F32, S72.0).",
                )
            )
            continue

        parsed = kcd.CodeRef.parse(code)
        hit_pairs = [(m, c) for m, c in mentions if m.range.contains(parsed)]
        #: 같은 조항 안의 큰 면책 범위와 예외 범위가 모두 코드를 포함할 수 있다.
        #: 근거 조항은 하나이므로 코드별 응답에서도 한 번만 내보낸다.
        cites = _dedupe(_citations(hit_pairs, judged["status"]))
        all_cites.extend(cites)

        if judged["status"] == "excluded":
            v, rc, note = Verdict.UNLIKELY, ReasonCode.EXCLUDED_BY_CLAUSE, "면책 조항에 해당합니다."
        elif judged["status"] == "exception":
            v, rc, note = (
                Verdict.NEEDS_DOCUMENTS,
                ReasonCode.EXCEPTION_APPLIES,
                "면책의 예외에 해당합니다. 요양급여 해당 여부 등 조건을 확인해야 합니다.",
            )
        else:
            #: ★면책 목록에 없다 ≠ 보장된다.
            v, rc, note = (
                Verdict.NEEDS_EXPERT,
                ReasonCode.NO_EVIDENCE,
                "면책 조항에는 없습니다. 다만 보장 여부는 '보상하는 사항' 조항이 정하므로 "
                "이 단계에서 보장된다고 단정할 수 없습니다.",
            )
        per_code.append(
            CodeVerdict(code=judged["code"], verdict=v, reason_code=rc, citations=cites, note=note)
        )

    # ── 5) 전체 결론 ────────────────────────────────────────────
    verdicts = {a.verdict for a in per_code}
    if Verdict.UNLIKELY in verdicts:
        overall, rc = Verdict.UNLIKELY, ReasonCode.EXCLUDED_BY_CLAUSE
    elif Verdict.NEEDS_DOCUMENTS in verdicts:
        overall, rc = Verdict.NEEDS_DOCUMENTS, ReasonCode.EXCEPTION_APPLIES
    else:
        overall, rc = Verdict.NEEDS_EXPERT, ReasonCode.NO_EVIDENCE

    warnings: list[str] = []
    if applied.date_confidence == "month":
        warnings.append("판매시점을 월까지만 확인했습니다. 경계 시점이면 세대가 다를 수 있습니다.")
    if applied.generation_confidence == "ambiguous":
        warnings.append("세대 판정이 경계에 걸쳐 있습니다.")

    return PrecheckOutcome(
        verdict=overall,
        abstained=overall == Verdict.NEEDS_EXPERT,
        reason_code=rc,
        message=_assessment_message(overall, per_code),
        applied_policy=applied,
        per_code=per_code,
        citations=_dedupe(all_cites),
        extractor=st.get("extractor", ""),
        warnings=warnings,
        **base,
    )


def verify_explanation(
    *,
    cited_clauses: list[str],
    evidence: list[ClauseRow],
    answer_text: str = "",
    quotes: dict | None = None,
) -> tuple[bool, ReasonCode | None, str]:
    """LLM 이 만든 설명의 인용을 검증한다. `(통과, 사유코드, 메시지)`.

    ★규칙 엔진이 판정을 소유하고 LLM 은 **설명만** 만든다.

        이 함수는 그 설명을 받아 인용이 우리 근거 안에 있는지 본다.
        통과하지 못하면 **설명을 버린다** — 판정(verdict)은 규칙이 이미 정했으므로
        설명이 없어도 답할 수 있다. 다만 근거를 못 대는 설명을 내보내면 안 된다.

    ★`ambiguous` 를 어떻게 다루나

        "어느 조항인지 특정할 수 없다"는 **통과도 폐기도 아니다.**
        같은 번호가 여러 특약에 있어 우리가 못 가리는 상황이다.
        그때는 `AMBIGUOUS_CITATION` 으로 기권한다 — 사람이 봐야 한다.

    ★아직 호출부가 없다(정직 기록)

        `run()` 은 지금 LLM 없이 규칙만으로 판정하므로 검증할 설명이 없다.
        LLM 을 붙일 때 이 함수를 통과한 설명만 응답에 싣는다.
        미리 만들어 두는 이유는 **경계를 코드로 못박아 두기 위해서**다 —
        나중에 급할 때 "일단 그냥 내보내자"가 되지 않도록.
    """
    from app.core.domain import citation_guard as cg

    ev = cg.make_handles(
        [cg.EvidenceClause(qualified_no=c.qualified_no, text=c.text) for c in evidence]
    )
    r = cg.verify(
        cited_clauses=cited_clauses,
        evidence=ev,
        answer_text=answer_text,
        quotes=quotes,
    )
    if r.ok:
        return True, None, ""
    code = (
        ReasonCode.AMBIGUOUS_CITATION
        if r.reason_code == "ambiguous_citation"
        else ReasonCode.CITATION_UNVERIFIED
    )
    return False, code, r.reason


_SCOPE_FROM_EXCLUSION = re.compile(
    r"생긴\s+(.{2,60}?)(?:은|는)\s+보상하지\s+않습니다"
)


def _citation_scope(text: str) -> str:
    """반복되는 `제4조` 카드가 어느 담보 조항인지 원문에서 드러낸다."""
    compact = " ".join((text or "").split())
    match = _SCOPE_FROM_EXCLUSION.search(compact)
    if not match:
        return ""
    scope = match.group(1).strip(" '‘’\"“”")
    return scope if len(scope) <= 40 else ""


def _citations(pairs, status: str) -> list[CitationRef]:
    """근거 조항 → 인용. ★성격이 불명한(`mention`) 것은 근거로 내지 않는다."""
    want = {"excluded": {"exclude"}, "exception": {"exception", "exclude"}}.get(status, set())
    out: list[CitationRef] = []
    for m, c in pairs:
        if m.kind not in want:
            continue
        out.append(
            CitationRef(
                clause_id=c.clause_id,
                qualified_no=c.qualified_no,
                section=c.section,
                title=c.title,
                scope=_citation_scope(c.text),
                quote=m.context[:300],
                page_from=c.page_from,
                page_to=c.page_to,
                #: ★★**수록 식별자를 반드시 싣는다.** 안 실으면 인용 검증이
                #:   "정확히 한 행" 을 확인할 방법이 없어 기권한다(코덱스 지적).
                #:   비어 있으면 그대로 비운다 — 지어내지 않는다.
                occurrence_id=getattr(c, "occurrence_id", ""),
                tier=EvidenceTier.POLICY_CLAUSE,
            )
        )
    return out


def _dedupe(cites: list[CitationRef]) -> list[CitationRef]:
    """같은 인용을 한 번만 남긴다.

    ★`clause_id` 하나로 접으면 **서로 다른 조항이 조용히 사라진다.**
      `{sha12}/{qualified_no}` 는 31,085건 충돌한다(문서의 86%) —
      부 탐지 입도가 특약보다 굵어 다른 특약이 한 라벨에 뭉치기 때문이다.
      `clause_id` 에 내용 해시를 붙여 고쳤지만, 여기서도 **페이지를 함께 본다.**
      같은 내용이 다른 쪽에 또 실렸으면 그건 다른 인용이다.
    """
    seen: set[tuple[str, int, int]] = set()
    out: list[CitationRef] = []
    for c in cites:
        key = (c.clause_id, c.page_from, c.page_to)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
