"""코호트 집계 — 파일에서 읽는 어댑터.

★빈 저장소의 실제 데이터는 **0건**이다. 저장소 누락과 구분한다.

    `CohortStatsPort` 구현체가 테스트의 `_FakePort` 뿐이라 엔드포인트가 없었다.
    저장소 디렉터리는 있고 **`verified` 증빙이 하나도 없으면 `n=0` 을 돌려준다.**
    그게 정직한 답이다 — 없는 통계를 만들어 내지 않는다.
    반대로 디렉터리 자체가 없으면 산출물 미배포이므로 `InfraError` 로 실패한다.

★합성과 실제를 **물리적으로 나눈다**

    `is_synthetic` 같은 불리언 컬럼으로 가르지 않는다. 그게 사고의 원인이다 —
    쿼리 하나에서 `WHERE` 를 빠뜨리면 섞인다.

        data/cohort/verified_real/events.jsonl    실제 (지금 0건)
        data/cohort/synthetic/events.jsonl        합성 (데모용)

    호출할 때 `data_source` 를 **반드시** 주고, 어댑터는 그 폴더만 본다.
    ★두 폴더를 합치는 코드를 만들지 않는다.

★`verified` 만 센다

    `/v1/observations` 로 들어온 보고는 `unverified` 다.
    검증 전에는 통계에 **넣지 않는다** — 넣으면
    "우리 서비스로 청구하면 90% 나옵니다" 같은 수치가 근거 없이 만들어진다.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.domain.insurance import CohortStats, DataSource, KcdCode
from app.core.errors import InfraError
from app.core.usecases.cohort import DEFAULT_MIN_SAMPLE

_ROOT = Path(__file__).resolve().parents[2]
_BASE = _ROOT / "data" / "cohort"

#: ★집계에 넣을 검증 등급. `unverified` 는 **넣지 않는다.**
#:
#: ★`admin_attested` 추가(2026-08-04) — 관리자 교차검증. 계획서 §3 이
#:   "발행처 API 확인이 불가능하면 관리자 교차검증을 필수 경로로 두고
#:   그 사실을 응답에 명시한다"고 한 그 경로다. **집계에는 들어가되
#:   등급을 따로 세어 응답에 싣는다**(`by_verification`) — 합치기만 하면
#:   "n=5" 가 "5건 발행처 확인됨"으로 읽힌다.
_COUNTED = {
    "document_backed", "confirmed", "admin_attested",
    "synthetic_consistency", "synthetic_admin_review",
}

#: 폴더 이름 — `DataSource` 값과 1:1. 섞이지 않게 물리적으로 나눈다.
_DIR = {
    DataSource.VERIFIED_REAL: "verified_real",
    DataSource.SYNTHETIC: "synthetic",
}


def _events(source: DataSource) -> list[dict]:
    d = _BASE / _DIR[source]
    if not d.exists():
        #: 빈 폴더는 "검증된 사례 0건"일 수 있지만 폴더 자체가 없으면 산출물이
        #: 배포되지 않은 것과 구분할 수 없다. 정상 0건으로 위장하지 않는다.
        raise InfraError("코호트 데이터 저장소가 준비되지 않았습니다.")
    if not d.is_dir():
        raise InfraError("코호트 데이터 저장소 경로가 디렉터리가 아닙니다.")
    event_files = sorted(d.glob("*.jsonl"))
    if not event_files:
        #: 빈 JSONL은 의도적으로 준비된 0건 저장소다. 파일 자체가 없으면 배포
        #: 누락이므로 같은 n=0으로 처리하지 않는다.
        raise InfraError("코호트 이벤트 파일이 배포되지 않았습니다.")
    out: list[dict] = []
    for p in event_files:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError as e:
            raise InfraError(f"코호트 이벤트를 읽을 수 없습니다: {p.name}") from e
        for line in lines:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise InfraError(f"코호트 이벤트가 깨졌습니다: {p.name} — {e}") from e
    return out


def _matches(ev: dict, *, code: str, product_id: str, age_band: str | None) -> bool:
    if code and code not in (ev.get("kcd_codes") or []):
        return False
    if product_id and ev.get("product_id") != product_id:
        return False
    #: ★연령대는 주어졌을 때만 좁힌다. 없으면 전체를 본다.
    if age_band and ev.get("age_band") != age_band:
        return False
    return True


def fetch(
    *,
    kcd_code: KcdCode,
    product_id: str,
    age_band: str | None,
    data_source: DataSource,
) -> CohortStats:
    """코호트 집계. **`verified` 증빙만** 센다.

    ★표본이 `MIN_SAMPLE` 미만이면 `CohortStats` 가 비율 계산을 거부한다.
      여기서 비율을 미리 계산해 주지 않는다 — 그 판단은 도메인의 몫이다.
    """
    rows = [
        e
        for e in _events(data_source)
        if (e.get("verification") in _COUNTED)
        and _matches(e, code=kcd_code.code, product_id=product_id, age_band=age_band)
    ]
    approved = sum(1 for e in rows if e.get("outcome") == "paid")
    denied = sum(1 for e in rows if e.get("outcome") == "denied")

    #: ★등급별 내역. 어떤 등급이 몇 건인지 숨기지 않는다.
    grades: dict[str, int] = {}
    for e in rows:
        g = e.get("verification") or "?"
        grades[g] = grades.get(g, 0) + 1

    #: ★구조적 경고(제보 편향 등)는 **유스케이스가 붙인다.** 여기서 중복해 넣지 않는다.
    #:   어댑터는 저장소 사실만 말한다.
    #: ★합성 표시도 **유스케이스가 붙인다**(`cohort.py`). 여기서도 넣었더니
    #:   화면에 "합성 데이터입니다" 가 **두 번** 나왔다. 실제로 열어 보고 알았다.
    #:   경고가 겹쳐 보이면 읽는 쪽이 경고 자체를 흘려보낸다.
    warnings: list[str] = []

    return CohortStats(
        n=len(rows),
        approved_n=approved,
        denied_n=denied,
        data_source=data_source,
        min_sample=DEFAULT_MIN_SAMPLE,
        warnings=tuple(warnings),
        by_verification=tuple(sorted(grades.items())),
    )


__all__ = ["fetch"]
