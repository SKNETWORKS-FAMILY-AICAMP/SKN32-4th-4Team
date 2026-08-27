> 공개 정리본 · 원본: docs/reports/2026-08-25_postgresql_운영기본값_pool_및_회귀검증.md
> 이 문서는 실행 당시의 측정 기록입니다. 같은 항목의 문서가 여러 개면 날짜가 늦은 문서를 현재 결과로 읽으세요.

# PostgreSQL 운영 기본값·커넥션 풀·회귀 검증 보고서

작성일: 2026-08-25

## 완료

- `Settings.CLAUSE_STORE`를 추가하고 production 템플릿에 `CLAUSE_STORE=pg`를 추가했다.
- production validator와 `verify_release.py`가 `CLAUSE_STORE=pg`, 모든 persistence PostgreSQL, `VERIFIED_COHORT_STORE=postgres`, SQLite legacy 비활성화를 검사한다.
- `db/postgres/pool.py`를 추가해 DSN별 `psycopg_pool.ConnectionPool`을 재사용하고 lease 종료 시 연결을 반환한다.
- 보험 repository, auth, ops, agent, demo, pgvector 연결을 공용 풀 진입점으로 전환했다.
- pgvector strict connection type 검사와 pool lease의 호환성도 수정했다.
- runtime dependency를 `psycopg[pool,binary]==3.3.4`로 고정했다.

## 실측 검증

- 로컬 PostgreSQL `127.0.0.1:5433`에서 풀 생성·재사용·반환·close_all을 확인했다.
- `insurance_real`, `insurance_agent`, `insurance_demo`, `mall_vec` adapter smoke가 통과했다.
- 실제 로컬 DSN과 production 환경 변수로 `verify_release --strict --json`가 `ok=true`를 반환했다.
- 변경 관련 targeted pytest: `38 passed`.
- 전체 비-LLM/비-ML/비-MCP/비-legacy_data pytest: 수집 오류 없이 100% 완료, exit code `0`.
- Anaconda의 누락된 `Library/bin/sqlite3.dll`을 로컬 conda cache의 동일 버전 패키지에서 복구한 뒤 전체 회귀가 완료됐다.

## 외부 상태로 남은 것

- 실제 운영 DSN/비밀값 주입은 운영 비밀 저장소에서 해야 하며 저장소에는 넣지 않았다.
- `insurance_real.core` 12개 핵심 테이블은 현재 모두 0행이다. `data/structured`와 `mall_vec` 자료는 승인 문서·제품·정책·조항 관계를 임의 추정할 수 없어 bulk import하지 않았다.
- `mall_vec`는 vector/RAG 전용 DB이며 `insurance_real.core`와 FK/view로 연결되어 있지 않다. core 적재는 원천 매핑과 승인 상태를 확정한 별도 dry-run/import 단계가 필요하다.
- x600은 연산용 SSH로만 유지하며 PostgreSQL 본진이나 운영 데이터를 이동하지 않는다.

## 호환성 판단

기존 adapter의 `with connection` 형태를 보존해 호출부 변경 위험은 낮다. pool 도입으로 연결 수명과 commit/rollback 경계가 바뀌므로 쓰기 경로는 기존 transaction 테스트와 로컬 PostgreSQL smoke를 함께 통과시켰다. pool 의존성이 없는 개발 환경에서는 direct connection fallback이 동작하지만 production dependency에는 pool extra가 포함된다.

## 2026-08-25 원천 매핑·core cutover 후속 검증

- `scripts/db/import_insurance_core.py`와 `tests/test_insurance_core_import.py`를 추가했다.
- 원천 manifest 2,139건은 전부 `unidentified`이고, S7 구조화 236건은 전부 `candidate`이므로 importer가 실제 DB 쓰기 전에 중단한다.
- dry-run 실측은 structured 236건, clause 22,565건, annex 1,184건, unique content 9,052건이다.
- `insurance_real.core` 12개 테이블은 계속 0행이다. 이는 누락이 아니라 승인 전 데이터 보호를 위한 의도된 차단이다.
- `mall_vec`는 별도 vector/RAG DB로 유지되며 현재 content 64,608 / chunk 122,773 / occurrence 368,920이다.
- 신규 core schema verifier, production 설정 strict release, 전체 선택 회귀 테스트가 모두 통과했다.

## mall_vec 고아 occurrence 원인 감사

- read-only 실측: 고아 occurrence 38,326건.
  - `s5-mixed`: 22,436건
  - `s6/clause`: 8,707건
  - `s6/annex`: 7,183건
- 고아 행은 모두 `policy_clause_chunk`도 없었다. 따라서 현재 벡터 검색 후보에는 직접 올라오지 않지만 저장공간과 정합성을 오염시킨다.
- 원인 후보가 코드에서 확인됐다. `scripts/index/build_clause_index.py:264`가 `upsert_occurrences()`를 먼저 호출하고, `db/postgres/pgvector_clause_index.py:693`에서 occurrence를 별도 commit한다. 그 후 content/chunk 적재가 시작된다.
- 적재 프로세스가 중간 종료되면 occurrence만 영속화되는 구조이며, 현재 고아 분포와 일치한다.
- 최근 pool/smoke/회귀 테스트는 이 인덱스 적재기를 실행하지 않았고 고아 수는 이전 감사와 같으므로, 이번 pool 작업에서 새로 만든 결함은 아니다. 다만 과거 프로젝트 적재 경로에서 발생했을 가능성은 높다.
- 삭제는 아직 하지 않았다. 먼저 적재 순서를 content/chunk 선행 또는 하나의 transaction으로 고친 뒤, 고아 행 백업·건수 검증·정확한 조건 삭제를 진행해야 한다.

## 2026-08-25 11:57 현재 DB 재감사

- PostgreSQL 16.14, 전체 9개 DB가 존재한다.
- 현재 접속 세션 0개, 대기 lock 0개다. 감사 시점에는 다른 세션의 쓰기 작업이 실행 중이지 않았다.
- `mall_vec`: occurrence 368,919 / content 64,607 / chunk 122,772, 고아 occurrence 38,326으로 이전과 동일하다.
- `insurance_real.core`: 12개 테이블 모두 0행, migration 최신은 016이다.
- `insurance_real.app/ops`에는 smoke·readiness 데이터가 남아 있으며 `ops.knowledge_gap` 23행, `ops.run_event` 36행이다.
- 임시 DB `insurance_repo_verify_fb0381c9f241`는 여전히 존재하고 접속 0개다.
- `insurance_pytest`에는 현재 `pytest_*` 스키마 7개가 있다. 빈 스키마 4개와 데이터가 있는 스키마 3개가 확인되어, 이전 보고서보다 시험 찌꺼기가 늘었다.
