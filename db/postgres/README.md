# PostgreSQL 구현

이 디렉터리는 PostgreSQL 연결·저장소·검색 구현을 둔다.

`app.adapters.*`에 남아 있는 동일 이름의 모듈은 기존 import 호환을 위한 얇은 래퍼이며,
새 코드는 `db.postgres.*`를 사용한다. `app.core`의 도메인·유스케이스·포트는 이 디렉터리를
직접 import하지 않는다.
