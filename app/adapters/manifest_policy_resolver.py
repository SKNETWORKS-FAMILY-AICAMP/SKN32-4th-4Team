"""가입일 → **적용 약관 버전**을 확정한다.

★이게 판정의 첫 단추다

    같은 질병·같은 병원비라도 세대마다 자기부담금이 다르다.
    1세대는 사실상 전액 보상, 4세대는 급여 20%·비급여 30%를 본인이 낸다.
    **어느 약관이 적용되는지를 먼저 정하지 않으면 나머지가 다 틀린다.**

★현행 약관으로 폴백하지 않는다

    가입 시점 약관을 못 찾았을 때 "일단 최신 약관으로 답한다"는
    **가장 위험한 폴백**이다. 사용자는 자기 계약이 아닌 약관으로 안내받는다.
    못 찾으면 `NotResolved` 를 돌려준다 — "확인 불가"가 정답이다.

★판정에서 제외하는 것

    · `excluded_reason` 이 있는 문서 (사업방법서·여행실손·비의료실손)
    · `date_confidence == "unknown"` (판매시점을 모른다)
    · `parse_status != "ok"` (조항 구조화 실패 — 근거를 댈 수 없다)

    셋 다 "쓸 수 있는데 안 쓰는" 게 아니라 **쓰면 틀리는** 것들이다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.domain.policy_naming import looks_like_rider, normalize as _norm
from app.core.errors import InfraError, ValidationErr
from app.core.ports.precheck import NotResolved, PolicyVersionRow

_ROOT = Path(__file__).resolve().parents[2]
_MANIFESTS = _ROOT / "data" / "raw" / "manifests"
#: ★확정 원장. **매니페스트와 분리한다.**
#:
#:   매니페스트는 「우리가 무엇을 받았나」(수집 기록)이고 크롤러가 다시 쓴다.
#:   확정은 「이게 무엇인지 사람이 정했다」(결정)다. 같은 파일에 두면
#:   **수집을 다시 돌리는 순간 사람의 결정이 덮인다.**
#:   ERD 는 이걸 `policy_version.confirmed_document_id` NOT NULL FK 로 두었다 —
#:   이 원장은 DB 적재 전 단계의 같은 장치다.
#:
#:   만드는 법: `python -m scripts.confirm.identify_documents --apply --confirmed-by ...`
_LEDGER = _ROOT / "config" / "confirmed_documents.jsonl"

_DATE = re.compile(r"^\d{8}$")


def load_ledger() -> dict[str, dict]:
    """sha256 → 확정 기록. 없으면 빈 dict(=확정 0건)."""
    if not _LEDGER.exists():
        return {}
    out: dict[str, dict] = {}
    for line in _LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        sha = e.get("sha256") or ""
        #: ★64자가 아니면 **버린다.** 앞자리만 맞는 sha 로 다른 문서를 확정할 수 있다.
        #:   같은 사고가 색인 적재에서 이미 있었다(20자 sha → 조회 전멸).
        if len(sha) != 64:
            raise InfraError(
                f"확정 원장의 sha256 이 64자가 아닙니다: {sha[:20]!r} — "
                "짧은 해시로 확정하면 다른 문서가 확정될 수 있습니다."
            )
        out[sha] = e
    return out



def _row_to_version(r: dict) -> PolicyVersionRow:
    return PolicyVersionRow(
        insurer=r.get("insurer", ""),
        product_name=r.get("product_name", "") or r.get("original_name", ""),
        sale_start=(r.get("sale_start") or "").strip(),
        sale_end=(r.get("sale_end") or "").strip(),
        generation=r.get("generation"),
        generation_label=r.get("generation_label", ""),
        product_line=r.get("product_line", ""),
        sha256=r.get("sha256", ""),
        #: ★★**기본값이 `"exact"` 였다 — 모르는 것을 안다고 하는 fail-open 이다.**
        #:
        #:   실측 2026-08-04: 매니페스트 2,121행 중 **1,702행에 이 키가 아예 없다**
        #:   (`exact` 270 · `month` 149 만 실제 값이다). 그 1,702행이 전부
        #:   "판매시점을 정확히 안다"로 둔갑하고 있었다.
        #:
        #:   지금은 `identification`·`generation_review` 게이트가 먼저 0건을 만들어
        #:   **드러나지 않는다.** 그래서 더 위험하다 — 확정 절차가 붙는 순간
        #:   (6-5a 데모 확정이 바로 그것이다) 조용히 새는 문이 된다.
        #:   같은 종류의 구멍을 바로 아래 `generation_review` 주석이 경고하고 있었다.
        #:
        #:   ★키가 없으면 `"unknown"` 이다. 그러면 `usable_for_judgment` 가 막는다(§0).
        date_confidence=r.get("date_confidence") or "unknown",
        generation_confidence=r.get("generation_confidence", ""),
        generation_review=r.get("generation_review", ""),
        #: ★확정 여부. 없으면 미확정으로 본다(fail-closed).
        identification=r.get("identification") or "unidentified",
    )


def count_collected() -> int:
    """판정 후보가 될 수 있는 **수집 문서 수**(제외사유 없는 고유 sha).

    ★확정 건수의 **분모**다. 분모 없이 "10건 확정"만 말하면 전량으로 읽힌다.
    """
    if not _MANIFESTS.exists():
        raise InfraError(f"매니페스트 폴더가 없습니다: {_MANIFESTS}")
    seen: set[str] = set()
    for m in sorted(_MANIFESTS.glob("*.jsonl")):
        for line in m.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if (r.get("excluded_reason") or "").strip():
                continue
            sha = r.get("sha256", "")
            if sha:
                seen.add(sha)
    return len(seen)


def load_versions() -> list[PolicyVersionRow]:
    """판정에 쓸 수 있는 약관 버전 전부.

    ★확정 원장을 매니페스트 **위에 덮는다.** 원장에 없는 문서는 손대지 않으므로
      기본값은 여전히 `unidentified` — 즉 **아무것도 안 하면 판정 0건**이다(fail-closed).
    """
    if not _MANIFESTS.exists():
        raise InfraError(f"매니페스트 폴더가 없습니다: {_MANIFESTS}")
    from app.core.domain import identification_mode

    mode = identification_mode.current()
    ledger = load_ledger()
    seen: set[str] = set()
    out: list[PolicyVersionRow] = []
    for m in sorted(_MANIFESTS.glob("*.jsonl")):
        for line in m.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if (r.get("excluded_reason") or "").strip():
                continue
            sha = r.get("sha256", "")
            if not sha or sha in seen:
                continue
            seen.add(sha)
            conf = ledger.get(sha)
            #: ★★엄격 모드에서는 **사람 승인 전 항목을 덮지 않는다.**
            #:   덮지 않으면 매니페스트의 기본값 `unidentified` 가 남고,
            #:   `usable_for_judgment` 가 아래에서 걸러 낸다(fail-closed).
            #:   모드를 여기서 읽는 이유: 원장을 덮는 이 자리가 **유일한 확정 지점**이라
            #:   다른 곳에 게이트를 또 두면 두 판단이 어긋난다.
            if conf and not mode.auto_approve and identification_mode.is_pending_signoff(conf):
                conf = None
            if conf:
                #: ★**확정 관련 필드만** 덮는다. 상품명·판매일까지 원장이 이기게 하면
                #:   원장이 사실상 두 번째 매니페스트가 되고, 둘이 어긋났을 때
                #:   어느 쪽이 맞는지 아무도 모른다. 원장은 **판단**만 담는다.
                r = {**r,
                     "identification": conf.get("identification") or "unidentified",
                     "generation_review": conf.get("generation_review") or ""}
            v = _row_to_version(r)
            if not v.usable_for_judgment:
                continue
            out.append(v)
    return out


#: ★★버전 목록 캐시 — **입력 파일이 바뀌면 스스로 다시 읽는다.**
#:
#:   전에는 `precheck_graph.build()` 가 기동 시 한 번 읽어 **클로저에 가뒀다.**
#:   그래서 (1) 판정 모드를 바꿔도, (2) 확정 원장에 문서를 추가해도
#:   **서버를 재시작하기 전까지 반영되지 않았다**(2026-08-04 실측:
#:   자동승인으로 되돌렸는데 판정은 계속 0건이었다).
#:
#:   캐시를 없애면 매 요청마다 매니페스트 1,367줄을 다시 읽는다. 그래서
#:   **파일 변경 시각을 지문으로** 쓴다 — 안 바뀌면 재사용, 바뀌면 다시 읽는다.
_CACHE: list[PolicyVersionRow] | None = None
_CACHE_STAMP: tuple | None = None


def _inputs_stamp() -> tuple:
    """버전 목록에 영향을 주는 파일들의 변경 시각."""
    from app.core.domain import identification_mode

    def mtime(p) -> float:
        try:
            return p.stat().st_mtime if p.exists() else 0.0
        except OSError:
            return 0.0

    manifests = tuple(sorted(mtime(p) for p in _MANIFESTS.glob("*.jsonl"))) \
        if _MANIFESTS.exists() else ()
    return (mtime(_LEDGER), mtime(identification_mode._MODE_FILE), manifests)


def load_versions_cached() -> list[PolicyVersionRow]:
    """`load_versions()` 의 캐시판. **모드·원장·매니페스트가 바뀌면 자동 갱신.**"""
    global _CACHE, _CACHE_STAMP
    stamp = _inputs_stamp()
    if _CACHE is None or _CACHE_STAMP != stamp:
        _CACHE = load_versions()
        _CACHE_STAMP = stamp
    return _CACHE


def resolve(
    *,
    insurer: str,
    enrolled_on: str,
    product_name: str | None = None,
    versions: list[PolicyVersionRow] | None = None,
) -> PolicyVersionRow | NotResolved:
    """`enrolled_on` 시점에 적용되는 약관 버전을 고른다.

    Args:
        insurer: 보험사명.
        enrolled_on: 가입일 `YYYYMMDD`.
        product_name: 상품명(알면 좁혀진다).
        versions: 미리 읽어 둔 목록(테스트·성능용).

    Returns:
        확정되면 `PolicyVersion`, 아니면 `NotResolved`.
    """
    if not _DATE.match(enrolled_on or ""):
        raise ValidationErr(f"가입일 형식이 잘못됐습니다(YYYYMMDD): {enrolled_on!r}")

    pool = versions if versions is not None else load_versions()

    #: ★**"없다"와 "아직 확정 안 됐다"를 구분한다.**
    #:
    #:   확정 게이트(`usable_for_judgment`)가 켜져 있고 확정이 0건이면 `pool` 이 통째로 빈다.
    #:   그 상태에서 "'DB손해보험' 약관을 보유하고 있지 않습니다"라고 답하면 **거짓말이다** —
    #:   DB손보 약관만 236건 갖고 있다. 수집이 아니라 **식별**이 안 끝난 것이다.
    #:
    #:   CLAUDE.md §3 — 오류 메시지가 사실을 잘못 전하지 않게 한다.
    #:   (통신 실패를 "robots 거부"로 보고해 한참 헤맨 적이 있다. 같은 종류다.)
    #:
    #:   ★사용자가 할 일도 다르다. 확정 대기면 **기다리면 되고**,
    #:     미보유면 **다른 곳을 찾아야 한다.** 같은 문구로 답하면 안 된다.
    if not pool:
        return NotResolved(
            reason_code="documents_not_confirmed",
            message=(
                "판정 가능한 약관이 아직 0건입니다. 약관을 보유하고 있지 않은 것이 아니라, "
                "'이 파일이 무엇인가'를 사람이 확정하는 절차가 남아 있습니다. "
                "확인 안 된 약관으로 보장 여부를 답하지 않습니다."
            ),
        )

    ins = _norm(insurer)
    cand = [v for v in pool if _norm(v.insurer) == ins]
    if not cand:
        return NotResolved(
            reason_code="insurer_not_supported",
            message=f"'{insurer}' 약관을 보유하고 있지 않습니다.",
        )

    if product_name:
        pn = _norm(product_name)
        narrowed = [v for v in cand if pn in _norm(v.product_name)]
        if narrowed:
            cand = narrowed
        else:
            #: ★★**말없이 무시하지 않는다.** 여기가 `if narrowed: cand = narrowed` 뿐이었다 —
            #:   상품명이 하나도 안 맞으면 **그 입력을 통째로 버리고** 보험사 전체 후보로
            #:   판정을 계속했다.
            #:
            #:   실측 2026-08-04 — `product_name="있지도않은상품명XYZ"` 를 넣어도
            #:   상품명을 **안 준 것과 똑같은 답**이 나왔다. 사용자는 「내 상품을 지정했다」고
            #:   믿는데 그 입력은 판정에 **아무 영향도 주지 않았다.** 조용한 폴백이다(§0).
            #:
            #:   ★자동완성을 붙이면 이 위험이 **커진다** — 목록에서 골랐으니 더 확신한다.
            #:   그래서 자동완성보다 이걸 먼저 고친다(코덱스 지적).
            #:
            #:   ★막되 **길을 알려준다.** 후보를 함께 돌려주므로 사용자가 고를 수 있다.
            #:   상품명은 「좁히는 힌트」지만, 힌트가 **하나도 안 맞는다는 사실 자체**가
            #:   「입력이 틀렸거나 우리가 그 약관을 확정하지 못했다」는 정보다.
            return NotResolved(
                reason_code="product_not_matched",
                message=(
                    f"'{product_name}' 과(와) 이름이 맞는 약관을 {insurer} 에서 찾지 못했습니다. "
                    "상품명을 비우고 다시 시도하거나, 아래 후보 중에서 골라 주세요. "
                    "확정되지 않은 약관은 여기 나오지 않습니다."
                ),
                candidates=tuple(sorted(cand, key=lambda v: v.sale_start, reverse=True)[:5]),
            )

    #: 가입일 이전에 판매를 시작한 것 중 **가장 늦은 것**이 적용 약관이다.
    #: 종료일을 아는 경우 가입일이 그 안에 들어야 한다.
    applicable = [
        v
        for v in cand
        if v.sale_start <= enrolled_on and (not v.sale_end or enrolled_on <= v.sale_end)
    ]
    if not applicable:
        #: ★현행 약관으로 대신하지 않는다.
        return NotResolved(
            reason_code="no_version_at_date",
            message=(
                f"{insurer} 의 {enrolled_on} 시점 약관을 찾지 못했습니다. "
                "보유 중인 약관의 판매기간 밖입니다."
            ),
            candidates=tuple(sorted(cand, key=lambda v: v.sale_start)[-3:]),
        )

    #: ★본약관을 먼저 본다. 특약은 본계약에 붙는 것이라 "가입 상품"이 아니다.
    #:
    #:   상품명을 주면 특약도 후보로 두던 때가 있었는데, 그랬더니
    #:   `product_name="실손의료비"` 에 **`실손의료비 안정화 추가할인 특별약관`** 이
    #:   골라졌다. 그 특약에는 면책 코드가 없어 판정이 `no_evidence` 로 끝났다.
    #:   상품명은 **좁히는 용도**지 "특약을 달라"는 뜻이 아니다.
    #:
    #:   사용자가 정말 특약을 지목한 경우(이름에 '특약'이 들어감)에만 특약을 본다.
    wants_rider = bool(product_name and looks_like_rider(product_name))
    if not wants_rider:
        main_only = [v for v in applicable if not v.is_rider]
        if main_only:
            applicable = main_only

    applicable.sort(key=lambda v: v.sale_start)
    best = applicable[-1]

    #: ★상품 라인이 갈리면 **반드시 되묻는다.**
    #:   일반 실손 / 노후실손 / 유병력자실손은 자기부담금 체계가 아예 다르다.
    #:   시작일이 우연히 하나만 최신이라고 노후실손을 골라 버리면,
    #:   일반 실손 가입자에게 노후실손 기준으로 답하게 된다.
    #:
    #:   ★상품명을 줬다고 건너뛰면 안 된다. `"실손의료비"` 는
    #:     `무배당노후실손의료비보장보험` 에도 들어 있어 **라인을 못 가린다.**
    #:     실제로 현대해상에 "실손의료비"를 물었더니 노후실손이 골라졌다.
    #:     좁힌 뒤에도 라인이 둘 이상이면 물어야 한다.
    lines = {v.product_line for v in applicable if v.product_line}
    if len(lines) > 1:
        latest_per_line: dict[str, PolicyVersionRow] = {}
        for v in applicable:
            cur = latest_per_line.get(v.product_line)
            if cur is None or v.sale_start > cur.sale_start:
                latest_per_line[v.product_line] = v
        return NotResolved(
            reason_code="ambiguous_product_line",
            message=(
                "일반 실손·노후실손·유병력자실손 중 어느 상품인지 알려주세요. "
                "상품에 따라 자기부담금이 다릅니다."
            ),
            candidates=tuple(latest_per_line.values()),
        )

    #: 같은 시작일이 여럿이면 상품을 특정하지 못한 것이다.
    same = [v for v in applicable if v.sale_start == best.sale_start]
    if len(same) > 1 and not product_name:
        return NotResolved(
            reason_code="ambiguous_product",
            message="같은 시점에 해당하는 상품이 여럿입니다. 상품명을 알려주세요.",
            candidates=tuple(same[:5]),
        )
    return best
