"""PostgreSQL 전용 배포가 **레거시 SQLite 스택을 적재하지 않는지** 고정한다.

★**왜 필요한가** — 저장소는 PostgreSQL 로 전환했는데도 앱이 SQLAlchemy 를 통째로
끌어오고 있었다(실측 2026-08-26):

```
AUTH_PERSISTENCE=postgres  OPS_PERSISTENCE=postgres  SQLITE_LEGACY_ENABLED=false
→ sqlalchemy 모듈 121개 · db.sqlite_legacy 3개 적재
```

원인은 기능이 아니라 **타입 이름**이었다. 시그니처에 쓰려고 `from sqlalchemy.orm import
Session` 과 `from db.sqlite_legacy.models import User` 를 **모듈 최상단에서** 부른 것이다.

★단순히 무거운 게 아니라 **기동을 막은 적이 있다.** x600 에서 `import sqlalchemy` 가
  420초를 넘겨도 끝나지 않아 앱이 못 떴다. 그때 PostgreSQL 어댑터는 즉시 적재됐다 —
  **필요하지도 않은 의존성 때문에** 못 뜬 것이다.

★**이 결합은 조용히 되돌아온다.** 시그니처에 `Session` 이나 `User` 를 한 번만 적으면
  끝이라 리뷰에서 눈에 안 띈다. 그래서 테스트로 잠근다.
  타입이 필요하면 `app/auth/user_types.py` 의 `AuthStore`·`AuthUser` 를 쓴다.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: 이 조합이 「PostgreSQL 전용 배포」다. 레거시를 쓸 이유가 하나도 없다.
PG_ONLY_ENV = {
    "SECRET_KEY": "test-legacy-decoupling",
    "CLAUSE_STORE": "file",
    "AUTH_PERSISTENCE": "postgres",
    "OPS_PERSISTENCE": "postgres",
    "SQLITE_LEGACY_ENABLED": "false",
}

_PROBE = """
import sys
import app.main  # noqa: F401  -- 적재 자체가 시험 대상이다
sa = sorted(m for m in sys.modules if m == "sqlalchemy" or m.startswith("sqlalchemy."))
lg = sorted(m for m in sys.modules if m.startswith("db.sqlite_legacy"))
print("SQLALCHEMY=" + str(len(sa)))
print("LEGACY=" + ",".join(lg))
print("FIRST_SQLA=" + (sa[0] if sa else ""))
"""


def _run_probe() -> dict[str, str]:
    #: ★별도 프로세스로 돌린다. `sys.modules` 는 전역이라 같은 프로세스에서는
    #:   **다른 테스트가 이미 적재해 둔 것**을 보게 되어 시험이 무의미해진다.
    env = {**os.environ, **PG_ONLY_ENV, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"probe 실패:\n{proc.stdout}\n{proc.stderr}"
    return dict(
        line.split("=", 1)
        for line in proc.stdout.splitlines()
        if "=" in line and line.split("=", 1)[0].isupper()
    )


def test_postgres_전용이면_sqlalchemy를_적재하지_않는다() -> None:
    out = _run_probe()
    loaded = int(out["SQLALCHEMY"])
    assert loaded == 0, (
        f"PostgreSQL 전용 설정인데 sqlalchemy 모듈 {loaded}개가 적재됐다.\n"
        f"처음 끌어온 모듈: {out.get('FIRST_SQLA')!r}\n"
        "★시그니처에 `Session`/레거시 모델을 최상단 import 하지 않았는지 본다 — "
        "타입이 필요하면 `app/auth/user_types.py` 의 AuthStore·AuthUser 를 쓴다."
    )


def test_postgres_전용이면_레거시_sqlite_패키지를_적재하지_않는다() -> None:
    out = _run_probe()
    legacy = [m for m in out["LEGACY"].split(",") if m]
    assert legacy == [], (
        f"PostgreSQL 전용 설정인데 레거시 SQLite 모듈이 적재됐다: {legacy}\n"
        "★레거시 동작이 실제로 필요한 자리라면 **그 분기 안에서** 늦게 import 한다."
    )
