"""인증 사용자·저장소의 **런타임 안전한** 타입 이름.

★**왜 따로 있나** — PostgreSQL 로 전환했는데도 앱이 **레거시 SQLite 스택을
무조건 적재**하고 있었다(실측 2026-08-26):

```
AUTH_PERSISTENCE=postgres  OPS_PERSISTENCE=postgres  SQLITE_LEGACY_ENABLED=false
→ sqlalchemy 모듈 121개 적재 · db.sqlite_legacy 3개 전부 적재
```

원인은 기능이 아니라 **타입 이름**이었다. `app/auth/security.py` 등이 시그니처에 쓰려고
`from db.sqlite_legacy.models import User` 와 `from sqlalchemy.orm import Session` 을
**모듈 최상단에서** 불러왔다. 그러면 SQLite 를 한 줄도 안 쓰는 배포에서도 SQLAlchemy 가
통째로 딸려 온다.

★그냥 무거운 정도가 아니라 **기동을 막은 적이 있다** — x600 에서 `import sqlalchemy` 가
  420초를 넘겨도 끝나지 않아 앱이 못 떴다. 그때 PostgreSQL 어댑터
  (`db.postgres.auth_repository`)는 **즉시 적재됐다**(sqlalchemy 0개).
  즉 **필요하지도 않은 의존성 때문에 못 뜬 것**이다.
  → `docs/reports/debugs/2026-08-26_remote_who가_x600에서_멈추고_경고가_깨졌다.md`

## 쓰는 법

시그니처에는 여기 이름을 쓴다. **레거시 모델을 직접 import 하지 않는다.**

```python
from app.auth.user_types import AuthStore, AuthUser

def require_admin(user: AuthUser = Depends(get_current_user)) -> AuthUser: ...
```

레거시 SQLite **동작**이 실제로 필요한 자리(쿼리·모델 생성)에서는
그 분기 **안에서** 늦게 import 한다 — 그 분기를 안 타면 적재되지 않는다.

★타입 검사기는 `TYPE_CHECKING` 가지를 보므로 정밀도를 잃지 않는다.
  런타임에는 `Any` 라 FastAPI 가 `Depends` 주입만 하고 검증하지 않는다(원래도 그랬다).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 타입 검사 전용
    from sqlalchemy.orm import Session

    from db.postgres.auth_repository import PgAuthStore, PgUser
    from db.sqlite_legacy.models import User as LegacyUser

    #: 인증된 사용자 — 레거시 SQLite 행이거나 PostgreSQL 행이다.
    AuthUser = LegacyUser | PgUser
    #: 사용자 조회 대상 — 레거시 세션이거나 PostgreSQL 저장소다.
    AuthStore = Session | PgAuthStore
else:
    AuthUser = Any
    AuthStore = Any

__all__ = ["AuthStore", "AuthUser"]
