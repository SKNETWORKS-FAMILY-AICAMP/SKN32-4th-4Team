"""파일 저장소 ↔ PG 조항 색인 **판정 동등성**.

결함 이력: `docs/reports/2026-08-25_1530_pg판정경로_복구_완료보고.md`

★**왜 이 테스트가 필요한가**

    조항 저장소는 `CLAUSE_STORE` **환경변수 하나**로 갈린다. 두 구현이 같은 포트를
    만족한다고 해서 **같은 판정**을 내는 것은 아니다. 실제로 2026-08-25 에
    PG 경로가 세 겹으로 어긋나 있었다:

      1. 응답 스키마가 "모름"을 못 담아 **HTTP 500**
      2. `doc_stats()` 가 `parse_status` 를 하드코딩해 **전건 기권**
      3. 색인에 **수록 순번이 없어** 인용 검증이 전건 실패

    셋 다 **단위 테스트를 통과하는 상태**였다. 어댑터 선택만 검증했지
    "고른 어댑터로 판정을 끝까지 내 본" 테스트가 없었기 때문이다. 그 구멍을 막는다.

★`pg` 마커다 — 실제 색인이 있어야 돈다. 기본 CI 에서 제외된다.
  실행: `pytest tests/test_clause_store_parity.py -m pg`
"""

from __future__ import annotations

import pytest

from app.core.domain.precheck_result import PrecheckInput
from app.core.errors import ArtifactMissing, InfraError

pytestmark = pytest.mark.pg

#: 면책 조항이 실제로 걸리는 것과 안 걸리는 것을 섞는다.
#: `F20.0`(조현병)·`O00.0`(자궁외임신)은 실손 약관 면책 범위에 자주 들어 있고,
#: `S72.0`(대퇴골 골절)·`K80.2`(담낭결석)는 보통 면책 목록에 없다.
_CODES = ["F20.0", "S72.0", "K80.2", "O00.0"]

#: 문서를 몇 건까지 대조할지. 전량을 돌면 시간이 오래 걸린다.
#: ★표본이 작으면 **차이를 못 본다.** 5건일 때는 색인 미적재 문서가 안 걸려서
#:   `citation_unverified` 결함이 통과했다. 보험사를 골고루 담도록 늘렸다.
_MAX_DOCS = 10


def _adapters():
    from app.adapters import file_clause_store, manifest_policy_resolver
    from db.postgres import pg_clause_store

    return manifest_policy_resolver, file_clause_store, pg_clause_store


def _sample_versions(policies, n: int):
    """서로 다른 보험사에서 하나씩. 한 회사에 쏠리면 동등성을 못 본다."""
    seen: set[str] = set()
    out = []
    for v in policies.load_versions_cached():
        if v.insurer in seen:
            continue
        seen.add(v.insurer)
        out.append(v)
        if len(out) >= n:
            break
    return out


def _outcome(clauses, policies, v, code):
    from app.core.usecases import precheck as uc

    req = PrecheckInput(
        insurer=v.insurer,
        enrolled_on=v.sale_start,
        kcd_codes=[code],
        product_name=v.product_name,
    )
    try:
        o = uc.run(req, policies=policies, clauses=clauses)
    except ArtifactMissing:
        #: ★"색인에 없다"는 **양쪽 다 기권**이어야 한다. 여기까지 올라오면
        #:   유스케이스가 기권으로 바꾸지 못한 것이므로 그 자체가 결함이다.
        return ("ARTIFACT_MISSING_RAISED", None, None)
    except InfraError as e:  # 저장소 장애는 시험 대상이 아니다
        pytest.skip(f"조항 저장소 장애로 대조 불가: {type(e).__name__}: {e}")
    return (str(o.verdict), str(o.reason_code), len(o.citations))


#: 「가진 것이 다르다」를 뜻하는 기권 사유들.
#:
#: ★두 저장소가 **아는 사실 자체가 다를 수 있다** — 파일 저장소는 산출물을 갖고 있으나
#:   `parse_status` 가 미심쩍어 `document_not_reliable` 로 기권하고, PG 는 그 문서가
#:   **색인에 아예 없어** `no_evidence` 로 기권한다. 둘 다 정직한 기권이고 결론도 같다.
#:   이걸 불일치로 잡으면 테스트가 **커버리지 격차**를 동등성 결함으로 오인한다.
#:   ★그래도 **verdict 와 인용 개수는 반드시 같아야 한다**(아래 strict 비교).
_KNOWLEDGE_GAP_REASONS = {
    "ReasonCode.DOCUMENT_NOT_RELIABLE",
    "ReasonCode.NO_EVIDENCE",
    "ARTIFACT_MISSING_RAISED",
}


def test_두_저장소가_같은_판정을_낸다():
    """★핵심 계약 — 저장소를 바꿔도 **결론과 근거 수가 달라지면 안 된다.**

    verdict 와 인용 개수는 **엄격히** 대조한다. 인용 개수를 빼면
    "근거 없이 같은 결론"을 통과시킨다.

    `reason_code` 는 **양쪽이 같은 것을 갖고 있을 때만** 대조한다(위 주석).
    """
    policies, file_store, pg_store = _adapters()
    versions = _sample_versions(policies, _MAX_DOCS)
    assert versions, "확정 판본이 없습니다 — 대조할 대상이 없습니다."

    hard, soft = [], []
    compared = 0
    for v in versions:
        for code in _CODES:
            vf, rf, cf = _outcome(file_store, policies, v, code)
            vp, rp, cp = _outcome(pg_store, policies, v, code)
            compared += 1
            #: ★결론과 근거 수 — 여기가 갈리면 **판정이 갈린 것**이다.
            if (vf, cf) != (vp, cp):
                hard.append((v.insurer, code, (vf, rf, cf), (vp, rp, cp)))
            elif rf != rp:
                #: 사유만 다르다. 둘 다 "가진 것이 다르다" 계열이면 허용한다.
                if not ({rf, rp} <= _KNOWLEDGE_GAP_REASONS):
                    hard.append((v.insurer, code, (vf, rf, cf), (vp, rp, cp)))
                else:
                    soft.append((v.insurer, code, rf, rp))

    assert compared, "대조한 조합이 없습니다."
    assert not hard, (
        "저장소에 따라 판정이 갈립니다 (insurer, code, file, pg):\n  "
        + "\n  ".join(repr(m) for m in hard)
    )
    #: ★허용한 차이도 **숨기지 않는다.** 커버리지 격차가 얼마나 되는지 보이게 남긴다.
    if soft:
        print(f"\n[허용된 사유 차이] {len(soft)}/{compared}건 — 색인 커버리지 격차:")
        for s in soft[:5]:
            print("   ", s)


def test_pg_행은_수록_식별자를_갖는다():
    """★인용 검증이 "정확히 한 행"을 특정하려면 `occurrence_id` 가 있어야 한다.

    비어 있으면 검증이 fail-closed 로 거부해 **인용을 댄 판정이 전부 기권**한다
    (2026-08-25 에 실제로 그랬다).
    """
    policies, _file, pg_store = _adapters()
    checked = 0
    for v in _sample_versions(policies, _MAX_DOCS):
        try:
            rows = pg_store.load_clauses(v.sha256)
        except ArtifactMissing:
            continue  # 색인에 없는 문서는 이 시험의 대상이 아니다
        for r in rows:
            assert r.occurrence_id, (
                f"수록 식별자가 비었습니다: {v.insurer} {r.qualified_no} "
                f"(ordinal={r.ordinal!r}, release_id={r.release_id!r})"
            )
            checked += 1
    assert checked, "PG 색인에서 조항을 하나도 읽지 못했습니다."


def test_수록_식별자는_문서_안에서_유일하다():
    """★같은 식별자가 둘이면 "정확히 한 행"이 성립하지 않는다.

    인용 검증은 중복 키를 **아예 못 쓰는 것**으로 표시하므로, 중복이 있으면
    그 조항은 근거로 쓸 수 없게 된다.
    """
    policies, _file, pg_store = _adapters()
    for v in _sample_versions(policies, _MAX_DOCS):
        try:
            rows = pg_store.load_clauses(v.sha256, usable_only=False)
        except ArtifactMissing:
            continue
        ids = [r.occurrence_id for r in rows if r.occurrence_id]
        dupes = {i for i in ids if ids.count(i) > 1}
        assert not dupes, f"{v.insurer}: 수록 식별자 중복 {sorted(dupes)[:3]}"


def test_색인에_없는_문서는_기권이지_장애가_아니다():
    """★`ArtifactMissing` 이 유스케이스에서 **기권**으로 바뀌어야 한다.

    전에는 PG 가 다른 문구로 `InfraError` 를 던져 문자열 대조를 통과하지 못했고,
    그래서 **같은 상황에서 파일은 200 기권 · PG 는 503** 이었다.
    """
    from app.core.usecases.precheck import _is_missing_artifact

    assert _is_missing_artifact(ArtifactMissing("이 약관의 조항 기록이 없습니다: abc"))
    #: ★문구가 달라도 타입만 맞으면 통과해야 한다 — 그게 타입으로 가르는 이유다.
    assert _is_missing_artifact(ArtifactMissing("무슨 문구든"))
    #: 저장소 장애는 기권이 아니다. 이걸 기권으로 바꾸면 DB 가 죽어도 사용자는 모른다.
    assert not _is_missing_artifact(InfraError("PostgreSQL 에 연결할 수 없습니다"))
