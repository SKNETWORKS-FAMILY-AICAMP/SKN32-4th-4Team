> 공개 정리본 · 원본: docs/reports/2026-08-05_1400_postgresql_보험도메인_S5_agent_mirror_완료.md
> 이 문서는 실행 당시의 측정 기록입니다. 같은 항목의 문서가 여러 개면 날짜가 늦은 문서를 현재 결과로 읽으세요.

# PostgreSQL 보험 도메인 S5 — agent client mirror

## 결론

`insurance_agent`를 에이전트 인증 정본으로 두고, `insurance_real.ops.agent_client`는 보험 업무 원장의 FK/runtime identity mirror로 사용하도록 연결했다. registered-agent 업무 데이터의 PostgreSQL 저장 gate는 아직 열지 않았다.

## 구현

- `PgAgentAccess.list_client_mirror_snapshots()` 추가
  - raw API key는 반환하지 않음
  - `agent_client_id`, name, `api_key_hash`, rate limit, status만 비교
- `PgInsuranceAdminTransaction.list_agent_clients()` 및 `sync_agent_client_mirror()` 추가
  - owner DSN 변경 경계 유지
  - active/disabled 및 `disabled_at` 반영
- `SyncAgentClients` use case 추가
  - missing, differing, extra drift report
  - 기본 dry-run
  - `--apply` 명시 시 source 값으로 upsert
  - `--disable-extras` 명시 시 source에 없는 real mirror를 disabled 처리
  - extra record는 삭제하지 않음
- CLI: `python -m scripts.agent_clients sync-real`

## 검증

- `tests/test_sync_agent_clients.py`: 2 passed
- `tests/test_pg_insurance_repository_integration.py::test_agent_client_mirror_uses_source_hash_and_syncs_runtime_fk`: 1 passed
- fresh PostgreSQL에서 source 등록 → mirror 생성 → runtime FK 조회 → 키 교체 mirror 반영 → source 비활성화 mirror 반영 확인
- `py_compile`, `git diff --check` 통과

## 호환성/위험

- 기존 `list`, create/rotate/disable CLI 계약은 유지했다.
- source에 없는 real row를 삭제하지 않아 FK 참조와 운영 이력의 파괴적 변경을 피했다.
- source와 real DB 사이에 아직 원자적 distributed transaction은 없다. 따라서 mirror drift가 0인지 확인되기 전까지 registered-agent PostgreSQL 업무 저장을 계속 fail-closed로 유지한다.

## 다음 단계

1. 실제 운영 DSN에서 `sync-real` dry-run으로 drift 0 확인
2. registered-agent subject hash 및 consent purpose 계약 추가
3. precheck/outcome/consent/interaction/audit 연결 후 PostgreSQL gate를 별도 설정으로 단계 개방

## S5 follow-up — registered-agent precheck gate

- migration `011_registered_agent_subject_consent.sql`로 raw subject 대신 HMAC subject reference hash를 저장하고 active subject를 재사용한다.
- `consent_purpose`를 요청 계약에 추가하고 precheck transaction에서 `ops.grant_consent()`를 호출한다.
- `AGENT_REAL_LEDGER_ENABLED=false`가 기본이며, mirror drift 확인/운영 승인 전에는 registered-agent PostgreSQL write가 계속 차단된다.
- active mirror client 확인과 subject hash/consent 누락 검사를 write 전에 수행한다.
- 검증: focused non-PG 41 passed, fresh PostgreSQL subject/consent 1 passed, schema verifier migration 001~011 및 non-superuser 11 passed.
- 남은 호환성 작업: registered-agent outcome의 동일 subject/consent 연결, interaction/audit 실제 write 경로와 retention/PII masking 검증.
- registered-agent outcome facade는 PostgreSQL path에 연결했으며, `AGENT_REAL_LEDGER_ENABLED=false`에서는 기존 fail-closed가 유지된다. 실제 precheck→outcome API smoke는 다음 검증 단계로 남겼다.
- registered-agent precheck/outcome 최초 저장 시 PII 없는 interaction/audit append를 같은 transaction에 포함했다. `retention_until`이 주어지면 subject/review/consent에 전달한다.
