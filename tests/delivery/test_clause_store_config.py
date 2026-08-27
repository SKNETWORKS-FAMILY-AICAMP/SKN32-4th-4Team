"""`CLAUSE_STORE` 설정 배선 — 회귀 고정.

계획: `docs/plans/2026-08-25_0955_전달계층_Django분리_계획.md` P0
결함: `docs/reports/debugs/2026-08-24_1815_운영템플릿에_CLAUSE_STORE가_없다.md`

★**무엇을 막는 테스트인가**

    `_clause_store_kind()` 가 `os.getenv` 로만 읽던 시절, `.env` 에 `CLAUSE_STORE=pg` 라고
    적어도 **조용히 무시되고 파일 어댑터로 떨어졌다.** 이 저장소에는 `load_dotenv()` 호출이
    없고 pydantic-settings 는 `.env` 를 Settings 필드 해석에만 쓰기 때문이다.

    그런데도 판정은 정상적으로 돈다 — 그래서 아무도 눈치채지 못한다.
    그 상태로 성능을 재면 "pgvector 성능"이라는 이름으로 **파일 I/O 를 잰다.**
"""

from __future__ import annotations

import pytest

from app.core.errors import ConfigError

pytestmark = pytest.mark.delivery


def _kind() -> str:
    from app.composition import _clause_store_kind

    return _clause_store_kind()


def test_미설정이면_file_이다(monkeypatch):
    """★기본이 pg 면 적재 안 된 기계에서 판정이 통째로 죽는다. 기본은 file 이 맞다."""
    monkeypatch.delenv("CLAUSE_STORE", raising=False)
    assert _kind() == "file"


def test_환경변수로_pg_를_고를_수_있다(monkeypatch):
    monkeypatch.setenv("CLAUSE_STORE", "pg")
    assert _kind() == "pg"


def test_대소문자와_공백을_흡수한다(monkeypatch):
    monkeypatch.setenv("CLAUSE_STORE", "  PG  ")
    assert _kind() == "pg"


def test_오타는_ConfigError_로_실패한다(monkeypatch):
    """★조용히 file 로 떨어지면 오타 하나로 다른 저장소를 쓰는 줄 모른다.

    ★Settings 가 `Literal["file","pg"]` 라 pydantic 이 먼저 막는데,
      그 `ValidationError` 가 그대로 새어 나가면 호출부가 이 프로젝트의 오류 계약
      (`ConfigError`)으로 처리하지 못한다. 여기서 변환을 고정한다.
    """
    monkeypatch.setenv("CLAUSE_STORE", "postgre")
    with pytest.raises(ConfigError) as e:
        _kind()
    assert "CLAUSE_STORE" in str(e.value)


def test_env_파일에_적어도_먹는다(tmp_path, monkeypatch):
    """★이게 2026-08-25 에 고친 결함 그 자체다.

    환경변수는 비우고 `.env` 에만 값을 둔다. 예전 구현(`os.getenv`)은 여기서 `file` 을
    돌려줬다 — 사용자는 `pg` 로 설정했다고 믿는 상태였다.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("CLAUSE_STORE=pg\n", encoding="utf-8")

    import app.core.config as cfg

    real_settings = cfg.Settings
    #: `_clause_store_kind()` 는 함수 안에서 `from app.core.config import Settings` 하므로
    #: 모듈 속성을 갈아끼우면 그대로 잡힌다(reload 불필요 — 뒤 테스트를 오염시키지 않는다).
    monkeypatch.setattr(
        cfg, "Settings", lambda **kw: real_settings(_env_file=str(env_file), **kw)
    )
    monkeypatch.delenv("CLAUSE_STORE", raising=False)

    assert _kind() == "pg"


def test_운영_env_예시에_CLAUSE_STORE_가_있다():
    """★운영 템플릿에서 빠져 있어서 생긴 결함이다. 다시 빠지면 여기서 잡는다.

    다른 6개 저장소는 전부 `postgres` 로 박혀 있는데 이것만 없으면,
    템플릿 그대로 띄운 운영이 **판정 근거만 파일에서 읽는다.**
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    text = (root / "config" / "production.env.example").read_text(encoding="utf-8")
    lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip().startswith("CLAUSE_STORE=")
    ]
    assert lines, "config/production.env.example 에 CLAUSE_STORE 가 없습니다."
    #: ★중복도 결함이다 — env 파일은 뒤에 오는 값이 조용히 이긴다.
    assert len(lines) == 1, f"CLAUSE_STORE 가 {len(lines)}번 나옵니다: {lines}"
