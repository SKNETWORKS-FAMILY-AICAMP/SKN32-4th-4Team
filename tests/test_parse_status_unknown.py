"""`parse_status` 가 "모른다"(`None`)일 때도 판정 응답을 끝까지 만들 수 있어야 한다.

결함: `docs/reports/debugs/2026-08-25_1120_CLAUSE_STORE_pg로_켜면_판정API가_전부_500이다.md`

★**무엇을 막는 테스트인가**

    PG 조항 색인은 문서 파싱 상태를 저장하지 않아 `parse_status=None`("모른다")을 돌려준다
    (`db/postgres/pg_clause_store.py:241` — "없는 것을 ok 로 채우면 호출부가
    '이 문서는 파싱됐다'고 믿는다"는 이유로 일부러 그렇게 고쳤다).

    그런데 응답 스키마가 `str` 이라 그 정직한 값을 담지 못해, `CLAUSE_STORE=pg` 로 켜면
    `POST /v1/prechecks` 가 **전부 500** 이었다. 두 정직함이 부딪힌 자리다.

★**왜 기존 테스트가 못 잡았나** — `tests/test_pg_clause_store.py` 는 **어느 어댑터가 붙는지**만
  본다. 고른 어댑터로 **응답을 끝까지 만들어 보는** 테스트가 없었다. 그 구멍을 여기서 막는다.

이 테스트는 DB 없이 돈다 — 결함이 직렬화 계층에 있었기 때문이다.
"""

from __future__ import annotations

import pytest

from app.core.domain.precheck_result import AppliedPolicyInfo as DomainPolicy
from app.routers.precheck import _policy
from app.schemas.precheck import AppliedPolicy as SchemaPolicy


def _domain(parse_status):
    return DomainPolicy(
        insurer="테스트화재",
        product_name="테스트 실손의료비보험",
        sale_start="20200101",
        sha256="a" * 64,
        parse_status=parse_status,
    )


@pytest.mark.parametrize("value", [None, "ok", "unknown", "page_fallback"])
def test_어떤_파싱상태든_응답을_만들_수_있다(value):
    """★`None` 이 핵심이다. 나머지는 회귀 방지용 동반 케이스."""
    dto = _policy(_domain(value))
    assert isinstance(dto, SchemaPolicy)
    assert dto.parse_status == value


def test_모름은_ok_로_바뀌지_않는다():
    """★가장 중요한 계약 — "모른다"를 "괜찮다"로 승격하지 않는다(`CLAUDE.md` §0).

    여기서 `"ok"` 로 메우면 클라이언트는 **확인한 적 없는 문서를 파싱됐다고 믿는다.**
    """
    dto = _policy(_domain(None))
    assert dto.parse_status is None, "모르는 파싱 상태를 값으로 메웠다"


def test_직렬화된_응답에도_null_로_나간다():
    """JSON 으로 나갈 때까지 "모른다"가 살아 있어야 한다."""
    dto = _policy(_domain(None))
    assert dto.model_dump()["parse_status"] is None
    assert '"parse_status":null' in dto.model_dump_json().replace(" ", "")


def test_기본값은_여전히_ok_다():
    """★타입만 넓혔다. 안 주면 `"ok"` 라는 기존 동작은 그대로다."""
    assert SchemaPolicy(
        insurer="x", product_name="y", sale_start="20200101"
    ).parse_status == "ok"
    assert DomainPolicy(
        insurer="x", product_name="y", sale_start="20200101"
    ).parse_status == "ok"
