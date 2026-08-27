> 공개 정리본 · 원본: docs/reports/2026-08-05_1213_postgresql_보험도메인_S2_repository_완료.md
> 이 문서는 실행 당시의 측정 기록입니다. 같은 항목의 문서가 여러 개면 날짜가 늦은 문서를 현재 결과로 읽으세요.

# PostgreSQL 보험 도메인 S2 Repository 완료 리포트

작성: 2026-08-05 12:13
계획: `docs/plans/2026-08-04_2051_postgresql_보험도메인_통합실행계획_체크리스트.md` S2

## 결과

보험 실제 원장용 repository 경계를 기존 SQLite SQLAlchemy `Base`와 분리해 구현했다. 실제 PostgreSQL 16.14에서 runtime/admin/migration role을 각각 분리하고, 업무 사슬 저장·조회·rollback과 ops 멱등성·최소권한을 검증했다.

## 구현

- `app/core/ports/insurance_repository.py`: 프레임워크를 모르는 transaction Protocol과 조회 snapshot
- `app/adapters/pg_insurance_repository.py`: psycopg 명시 SQL runtime/admin repository
- `scripts/db/006_ops_runtime_integrity.sql`: consent 상태전이 함수, active uniqueness, 익명 interaction 멱등 인덱스, ops CHECK·권한
- `app/core/config.py`, `.env.example`: `INSURANCE_PG_DSN`, `INSURANCE_ADMIN_PG_DSN`; 빈 값은 fail-closed
- `app/core/errors.py`: serialization/deadlock/lock timeout/연결 자원 문제를 `TransientInfraError`로 분리

SQLAlchemy 모델을 추가하지 않은 이유는 기존 `app.db.Base`와 `DATABASE_URL`이 SQLite 커머스·인증 경로에 결합돼 있기 때문이다. 보험 원장은 PostgreSQL native FK·jsonb·함수·권한을 그대로 쓰고, 기존 SQLite metadata/import 경로와 물리적으로 격리했다.

## transaction과 권한

- 판정 결과 write 구간은 연결 하나·transaction 하나로 원자 저장한다.
- 성공 시 commit, 도메인·PostgreSQL·애플리케이션 예외는 rollback, 모든 경로에서 connection close한다.
- SQLSTATE는 validation, conflict, forbidden, transient infra, infra로 구분한다.
- runtime LOGIN role은 `insurance_app`만 상속하며 schema CREATE와 consent 직접 INSERT, audit UPDATE 권한이 없다.
- admin LOGIN role은 `insurance_owner`를 상속하고 agent client 등록·키 hash 회전·비활성화만 수행한다.
- 실제 API key 원문이나 실제 개인정보는 테스트·설정 파일에 기록하지 않았다.

## 검증 결과

```text
fresh migration: 001~006 applied
reapply: 001~006 all skipped
non-superuser migrator: 6 migrations applied

subject → holding → coverage_review → diagnosis → assessment → citation
        → claim → outcome → evidence → consistency → verification
cohort n after verified: 1
transaction rollback residue: 0
FK error mapping: ValidationErr

consent grant/revoke retry: same id
registered interaction retry: same id, duplicate=true
anonymous interaction retry: same id, duplicate=true
same event/different payload: ConflictErr
runtime core/app/ops CREATE: false
runtime consent INSERT or audit UPDATE: false
temporary DB/roles remaining: 0
```

- S2 집중 비-PG 회귀: 57 passed
- 생성형 PostgreSQL repository 통합: 3 passed
- 전체 schema verifier: 정상 종료 코드 0
- py_compile 및 변경 파일 diff-check: 통과

## Claude checkpoint

앞선 두 읽기 전용 재검토는 timeout됐지만, repository 설계 요약만 전달한 세 번째 요청은 51.5초에 응답했다.

- 채택: 레거시 SQLAlchemy Base에 보험 ORM을 섞지 않고 explicit psycopg SQL 유지
- 채택: `40001`, `40P01` 등 재시도 가능한 SQLSTATE를 별도 오류로 구분
- 채택: S3에서 약관 읽기·판정은 write transaction 밖에서 수행하고 저장 구간만 짧게 유지
- 기충족: 일반 예외도 `finally`에서 connection close
- 기충족: migration ledger/checksum/advisory lock
- 비적용: `insurance` 단일 schema CREATE 지적은 실제 `core/app/ops` 구조와 달라 세 schema 권한을 직접 검사

## 남은 경계

S2 repository 자체는 완료했다. 아직 API가 이 repository를 호출하지 않으므로 “실제 요청이 DB에 남는 상태”는 S3에서 완성한다. 다음 단계는 `/v1/prechecks` 결과를 `subject → assessment_clause_citation`으로 원자 저장하고, 저장 실패를 성공 응답으로 위장하지 않도록 조립하는 것이다.
