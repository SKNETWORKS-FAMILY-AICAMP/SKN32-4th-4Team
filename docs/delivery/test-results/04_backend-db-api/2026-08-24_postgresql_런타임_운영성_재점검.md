> 공개 정리본 · 원본: docs/reports/2026-08-24_postgresql_런타임_운영성_재점검.md
> 이 문서는 실행 당시의 측정 기록입니다. 같은 항목의 문서가 여러 개면 날짜가 늦은 문서를 현재 결과로 읽으세요.

# PostgreSQL 런타임 운영성 재점검

- 작성일: 2026-08-24
- 목적: “pgvector를 PostgreSQL로 사용하므로 전체 앱도 PostgreSQL 운영형이다”라는 가정과 실제 코드 대조

## 실측 결과

### API 실행 모델

- route handler: 51개
- 동기 handler: 45개
- 비동기 handler: 6개
- 동기 FastAPI handler 자체는 정상적으로 threadpool에서 실행될 수 있다.
- 따라서 동기라는 사실만으로 오류는 아니지만, blocking DB/파일/외부 호출이 많으면 threadpool 포화 위험이 있다.

### PostgreSQL 연결 관리

- `psycopg.connect()` 호출 지점: 6곳
- 대상: auth, ops, pgvector, agent, demo, insurance repository
- 공용 `ConnectionPool`, `AsyncConnectionPool`, SQLAlchemy pool은 확인되지 않음
- 대부분 adapter method 호출 때 새 connection을 여는 구조
- 결론: 기능적으로는 PostgreSQL을 사용하지만 production 고동시성용 connection pooling은 아직 미완성

### precheck 경로

- `/v1/prechecks` route는 동기 `def`
- `_graph().invoke(_to_input(body))`로 규칙·정책·인용 검증을 수행
- 현재 precheck 핫패스에 LLM 호출은 확인되지 않음
- LLM이 없다는 것은 오류가 아니라 규칙 기반 판정 설계의 결과임
- 다만 persistence는 설정에 따라 별도 동작하며, 기본 개발값은 `PRECHECK_PERSISTENCE=off`

### 실제 기본값

`app/core/config.py` 기본값은 개발·호환 모드다.

- `AUTH_PERSISTENCE=sqlite`
- `OPS_PERSISTENCE=sqlite`
- `PRECHECK_PERSISTENCE=off`
- `OUTCOME_PERSISTENCE=file`
- `VERIFIED_COHORT_STORE=file`
- `DEMO_STORE_BACKEND=file`
- `PGVECTOR_DSN`은 PostgreSQL `mall_vec`를 가리킴

따라서 pgvector 연결이 PostgreSQL이라는 사실만으로 auth/ops/precheck/outcome/demo까지 PostgreSQL인 것은 아니다.

### 조항 store 기본값

- `app/composition.py`의 `CLAUSE_STORE` 기본값은 `file`
- `CLAUSE_STORE=pg`를 명시해야 PostgreSQL clause store를 선택
- 의미검색 쪽 `build_clause_search_deps()`는 pgvector clause index를 사용하지만, 정책·조항 전체 저장소 선택은 별도 설정임

### 실행·배포 방식

- Dockerfile 없음
- docker-compose 파일 없음
- `fastapi run` 또는 `fastapi-cli` 사용 흔적 없음
- 대신 `scripts/run_customer_server.py`, `scripts/run_admin_server.py`, `scripts/run_agent_server.py`가 `uvicorn.run()`으로 기동
- 그러므로 “fastapi run이 안 된다”는 지적은 맞지만, 현재 저장소가 uvicorn custom launcher를 쓰는 구조라는 설명이 함께 필요함

## 판단

해당 표는 수치상 대부분 맞다. 다만 “PostgreSQL을 안 쓴다”는 식으로 해석하면 부정확하다.

정확한 표현은 다음과 같다.

> 현재 저장소는 PostgreSQL·pgvector adapter와 production용 PostgreSQL 경로를 구현했지만, 개발 기본값은 아직 SQLite/file이고, 조항 store는 file 기본이며, DB connection pool과 표준 container 배포 경로가 없다.

## 우선순위

1. production 환경에서 모든 persistence와 `CLAUSE_STORE`를 PostgreSQL로 명시
2. PostgreSQL connection pool 도입
3. 운영 launcher·health/readiness·graceful shutdown 정리
4. Dockerfile/배포 방식이 필요하면 별도 추가
5. 실제 `insurance_real.core` data cutover 후 end-to-end 검증
