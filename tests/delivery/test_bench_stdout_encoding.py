"""벤치 드라이버가 **자기 출력 때문에 죽지 않는지** 고정한다.

★실측 2026-08-26 — x600(Windows) 콘솔이 `cp1252` 라 한글 진행 로그에서 터졌다:

    UnicodeEncodeError: 'charmap' codec can't encode characters in position 1-2

측정 8회가 전부 실패했는데 호출 스크립트가 **성공으로 보고**해 한동안 몰랐다.
도구가 재는 대상이 아니라 **자기 자신** 때문에 실패하면 안 된다.
"""

from __future__ import annotations

import io

import pytest

pytestmark = pytest.mark.delivery

from delivery.bench.load import _ensure_utf8_stdout  # noqa: E402


class _Cp1252Stream(io.StringIO):
    """UTF-8 이 아닌 콘솔. `reconfigure` 를 받아 준다(진짜 콘솔처럼)."""

    encoding = "cp1252"

    def reconfigure(self, *, encoding: str) -> None:
        type(self).encoding = encoding


class _StubbornStream(io.StringIO):
    """인코딩을 못 바꾸는 스트림(파이프·리다이렉트가 이렇다)."""

    encoding = "cp1252"

    def reconfigure(self, *, encoding: str) -> None:
        raise io.UnsupportedOperation("cannot reconfigure")


def test_utf8가_아니면_바꾸고_바꿨다고_말한다(monkeypatch) -> None:
    stream = _Cp1252Stream()
    monkeypatch.setattr("sys.stdout", stream)
    try:
        note = _ensure_utf8_stdout()
    finally:
        _Cp1252Stream.encoding = "cp1252"

    assert note is not None, "바꿔 놓고 아무 말도 안 하면 조용한 폴백이다"
    assert "utf-8" in note


class _Utf8Stream(io.StringIO):
    """이미 UTF-8 인 스트림. `io.StringIO.encoding` 은 대입이 안 되어 서브클래스로 둔다."""

    encoding = "UTF-8"

    def reconfigure(self, *, encoding: str) -> None:  # pragma: no cover - 불려선 안 된다
        raise AssertionError("이미 UTF-8 인데 reconfigure 를 불렀다")


def test_이미_utf8이면_건드리지_않고_조용하다(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdout", _Utf8Stream())

    assert _ensure_utf8_stdout() is None


def test_못_바꾸면_숨기지_않고_사유를_돌려준다(monkeypatch) -> None:
    """★여기서 조용히 넘어가면 다음 사람이 같은 자리에서 또 죽는다."""
    monkeypatch.setattr("sys.stdout", _StubbornStream())

    note = _ensure_utf8_stdout()

    assert note is not None
    assert "PYTHONIOENCODING" in note, "고칠 방법을 함께 알려줘야 한다"


def test_한글_진행로그가_cp1252_스트림에서도_나간다(monkeypatch) -> None:
    """회귀의 본체 — 바꾼 뒤에는 실제로 써져야 한다."""
    stream = _Cp1252Stream()
    monkeypatch.setattr("sys.stdout", stream)
    try:
        _ensure_utf8_stdout()
        print("[진행] 동시성=8 회차=1 시나리오=noop_async")
    finally:
        _Cp1252Stream.encoding = "cp1252"

    assert "진행" in stream.getvalue()
