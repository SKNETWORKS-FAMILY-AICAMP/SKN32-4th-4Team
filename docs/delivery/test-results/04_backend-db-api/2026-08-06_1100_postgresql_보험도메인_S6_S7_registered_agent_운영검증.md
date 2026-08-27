> 공개 정리본 · 원본: docs/reports/2026-08-06_1100_postgresql_보험도메인_S6_S7_registered_agent_운영검증.md
> 이 문서는 실행 당시의 측정 기록입니다. 같은 항목의 문서가 여러 개면 날짜가 늦은 문서를 현재 결과로 읽으세요.

# PostgreSQL 보험 도메인 S6/S7 진행 리포트

## 완료

- registered-agent precheck → subject hash/consent → outcome/claim/evidence PostgreSQL 경로 연결
- precheck/outcome 최초 저장 시 PII 없는 `ops.interaction_log`와 `ops.audit_log` append
- disabled/unknown agent client 저장 차단
- `AGENT_REAL_LEDGER_ENABLED=false` 기본 fail-closed 유지
- `PgInsuranceRepository.readiness()` 추가
  - 필수 보험 원장 테이블
  - `public.schema_migration`
  - 최신 migration
- `/api/health/ready`에서 PostgreSQL persistence/agent API 활성화 상태를 readiness에 반영
- migration `012_runtime_migration_readiness_grant.sql` 추가

## 검증

- registered-agent PostgreSQL end-to-end fresh DB: 1 passed
- interaction 2건, audit 2건, claim/evidence 연결 확인
- readiness fresh DB: 1 passed
- readiness/health/config: 16 passed
- schema verifier: migration 001~012 fresh/reapply/checksum/lock/rollback/non-superuser PASS

## 남음

- 실제 운영 DSN에서 `sync-real` drift=0 및 `/api/health/ready` 확인
- retention 기간의 조직 정책값 확정 및 만료 row 정리 job/권한 검증
- SSH A/B/C의 remote_who, revision, manifest, migration checksum을 같은 release 기준으로 수집
