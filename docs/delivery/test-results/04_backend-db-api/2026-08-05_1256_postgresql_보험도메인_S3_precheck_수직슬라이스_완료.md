> 공개 정리본 · 원본: docs/reports/2026-08-05_1256_postgresql_보험도메인_S3_precheck_수직슬라이스_완료.md
> 이 문서는 실행 당시의 측정 기록입니다. 같은 항목의 문서가 여러 개면 날짜가 늦은 문서를 현재 결과로 읽으세요.

# PostgreSQL 보험 도메인 S3 Precheck 수직 슬라이스 완료

작성: 2026-08-05 12:56
계획: `docs/plans/2026-08-04_2051_postgresql_보험도메인_통합실행계획_체크리스트.md` S3

## 결과

`/v1/prechecks`의 기존 판정 결과를 실제 PostgreSQL `core/app` UUID와 연결하고, 응답을 보내기 전에 anonymous subject부터 assessment citation까지 한 transaction으로 저장하는 첫 수직 슬라이스를 완료했다. 기본 설정은 기존 동작을 보존하고, `PRECHECK_PERSISTENCE=postgres`를 명시한 환경에서만 새 저장 계약을 강제한다.

## 구현 계약

- 입력: PostgreSQL 저장 모드에서 `incident_on`과 `Idempotency-Key` 필수
- subject: 개인정보 없는 요청 단위 anonymous subject 생성, 기존 사람과 추정 병합 금지
- FK 해소: 문서 SHA→policy version, 사고일→KCD code, occurrence의 source kind/ordinal→accepted extraction clause를 정확히 1건으로 해소
- 기권: policy를 못 정한 정상 기권은 holding/policy version을 NULL로 저장하고 `abstain_reason`과 raw KCD를 보존
- 원자성: `subject → policy_holding? → coverage_review → diagnosis → assessment → citation` 및 최초 응답 snapshot을 한 write transaction에서 커밋
- 멱등성: caller scope HMAC과 canonical payload SHA-256을 분리하고 원문 키는 저장하지 않음
- 재생: 같은 키·같은 payload는 최초 응답을 그대로 반환하고 행을 늘리지 않음; 같은 키·다른 payload는 409
- 장애: PostgreSQL 저장 실패를 성공 응답으로 위장하지 않으며 transient 오류는 `Retry-After`와 구조화 오류 코드를 보존

## 주요 변경

- `scripts/db/007_policy_clause_source_kind.sql`: 같은 ordinal의 조항/부록을 구분하는 `source_kind`
- `scripts/db/008_precheck_persistence_contract.sql`: policy 미해소 기권, 요청 HMAC/payload hash/response snapshot, partial unique index
- `scripts/db/009_diagnosis_raw_code.sql`: KCD UUID를 못 찾은 경우에도 입력 원문을 정직하게 보존
- `app/core/usecases/persist_precheck.py`: UUID 정확 해소, 멱등 replay, 짧은 원자 write transaction
- `app/adapters/pg_insurance_repository.py`: 실제 PostgreSQL read/write 구현과 SQLSTATE 오류 매핑
- `app/routers/precheck.py`: 설정 게이트, 입력 계약, 응답 전 필수 저장
- `app/routers/agent.py`, `app/application/agent_facade.py`: 기존 façade 호환을 유지한 선택적 멱등키 전달
- `app/core/config.py`, `.env.example`: 실제 원장 DSN, persistence mode, 별도 HMAC secret

## 호환성 판단

- 기본값 `PRECHECK_PERSISTENCE=off`에서는 기존 요청에 `incident_on`과 헤더를 요구하지 않는다.
- PostgreSQL 모드에서는 불완전한 저장을 허용하지 않기 위해 둘 다 필수다.
- 기존 SQLite SQLAlchemy `Base`에는 보험 모델을 추가하지 않아 커머스·인증 metadata와 분리했다.
- registered-agent 인증 원장은 별도 `insurance_agent` DB에 있다. 실제 원장의 `ops.agent_client`와 동기화가 없으므로 S5 전까지 registered-agent PostgreSQL 저장은 명시적 503으로 차단한다.
- upstream 결과에 구조화된 `missing_documents` 필드가 없으므로 메시지에서 문서명을 추측해 만들지 않고 NULL을 유지한다.
- `007`은 기존 `core.policy_clause` 행이 있는 DB에서 source kind를 추측 백필하지 않고 중단한다. 실제 데이터 적재 전에 적용해야 하며 S6 컷오버 사전검사 대상이다.

## Claude 교차검토

파일 단위 첫 요청은 timeout됐고, 구현 계약만 전달한 축약 검토는 94.5초에 완료됐다.

- 채택: transient infra 신호와 `Retry-After` 보존
- 채택: agent client 두 원장 동기화 전 registered-agent 저장 fail-closed
- 기충족 확인: key HMAC/payload digest 분리, 응답과 원장 동일 transaction, 기권 사유, unique index backstop
- 후속: transient 실패 후 같은 키 재시도 fault-injection은 S7 장애 리허설에 포함

## 검증

```text
schema verifier:
  fresh 001~009 applied / reapply skipped
  checksum conflict=1 / advisory lock=1 / partial rollback=22012
  non-superuser migrator applied=9
  exit code 0

PostgreSQL integration: 6 passed
  repository full chain / rollback / ops privileges
  precheck UUID resolution / idempotent replay / abstention NULL FK
  public API response replay / single ledger row / changed payload 409

non-PG focused final: 81 passed
py_compile: passed
git diff --check: whitespace error 0 (line-ending warnings only)
```

임시 검증 DB와 role은 fixture/verifier 종료 시 제거됐다. 전체 프로젝트 CI와 운영 장애·재기동 리허설은 S7 완료 게이트로 남긴다.

## 다음 단계

S4에서 claim/outcome/evidence 입력을 실제 API와 연결하고, consistency와 사람 verification을 분리한 뒤 PostgreSQL cohort 조회를 런타임에 연결한다. S5에서는 별도 agent registry와 실제 원장의 client 동기화 계약을 정한 뒤 현재 fail-closed 가드를 해제한다.
