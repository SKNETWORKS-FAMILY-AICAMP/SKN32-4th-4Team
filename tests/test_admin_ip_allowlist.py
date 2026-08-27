"""관리자 콘솔 IP 허용목록.

★이 파일이 지키는 명제

    1. 목록이 비어 있으면(파일 없음 포함) **전체 허용**이다(잠금 방지 기본값).
    2. 목록이 있으면 그 안의 IP/CIDR만 허용한다.
    3. 개별 IP도 CIDR도 저장할 수 있고, 저장된 값은 정규화된 문자열이다.
    4. 허용목록이 깨져 있으면 "전체 허용"으로 조용히 때우지 않고 실패한다.
"""

from __future__ import annotations

import json

import pytest

from app.core.domain import admin_ip_allowlist as gate
from app.core.errors import InfraError, ValidationErr


@pytest.fixture
def allowlist_file(tmp_path, monkeypatch):
    f = tmp_path / "admin_ip_allowlist.json"
    monkeypatch.setattr(gate, "_ALLOWLIST_FILE", f)
    return f


def test_파일이_없으면_빈목록이고_전체허용이다(allowlist_file):
    assert gate.current() == []
    assert not allowlist_file.exists()
    assert gate.is_allowed("1.2.3.4") is True
    assert gate.is_allowed("203.0.113.99") is True


def test_목록을_저장하면_그_IP만_허용한다(allowlist_file):
    gate.set_allowlist(["203.0.113.5"], actor="tester")
    assert gate.is_allowed("203.0.113.5") is True
    assert gate.is_allowed("203.0.113.6") is False


def test_CIDR도_받는다(allowlist_file):
    gate.set_allowlist(["203.0.113.0/24"], actor="tester")
    assert gate.is_allowed("203.0.113.200") is True
    assert gate.is_allowed("203.0.114.1") is False


def test_저장된_값은_파일에_남아_다른_프로세스도_같은_값을_본다(allowlist_file):
    gate.set_allowlist(["198.51.100.1"], actor="tester")
    saved = json.loads(allowlist_file.read_text(encoding="utf-8"))
    assert saved["ips"] == ["198.51.100.1/32"]
    assert saved["changed_by"] == "tester"
    assert saved["changed_at"]


def test_빈_목록으로_되돌리면_다시_전체허용이다(allowlist_file):
    gate.set_allowlist(["203.0.113.5"], actor="tester")
    assert gate.is_allowed("9.9.9.9") is False
    gate.set_allowlist([], actor="tester")
    assert gate.is_allowed("9.9.9.9") is True


def test_형식이_아닌_IP는_거절한다(allowlist_file):
    with pytest.raises(ValidationErr):
        gate.set_allowlist(["not-an-ip"], actor="tester")


def test_바꾼_사람_없이는_바꿀_수_없다(allowlist_file):
    with pytest.raises(ValidationErr):
        gate.set_allowlist(["203.0.113.5"], actor="")


def test_깨진_파일은_전체허용으로_때우지_않고_실패한다(allowlist_file):
    allowlist_file.parent.mkdir(parents=True, exist_ok=True)
    allowlist_file.write_text("{not json", encoding="utf-8")
    with pytest.raises(InfraError):
        gate.current()


def test_형식이_오염된_ips는_실패한다(allowlist_file):
    allowlist_file.parent.mkdir(parents=True, exist_ok=True)
    allowlist_file.write_text(json.dumps({"ips": "not-a-list"}), encoding="utf-8")
    with pytest.raises(InfraError):
        gate.current()


def test_유효하지_않은_클라이언트_IP는_불허한다(allowlist_file):
    gate.set_allowlist(["203.0.113.5"], actor="tester")
    assert gate.is_allowed("garbage") is False
