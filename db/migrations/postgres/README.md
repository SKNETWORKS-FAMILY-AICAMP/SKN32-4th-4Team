# PostgreSQL 마이그레이션

이 디렉터리가 PostgreSQL 스키마 SQL의 단일 원본이다.

- core 보험 스키마: `001_*.sql` ~ `016_*.sql`
- 합성 제출 DB: `demo/`
- 등록 에이전트 DB: `agent/`

적용·검증 Python 도구는 `scripts/db/`에 두되, SQL을 복사하지 않고 이 디렉터리를
참조한다. `public.schema_migration`의 기존 ledger filename과 checksum 계약은
그대로 유지한다.
