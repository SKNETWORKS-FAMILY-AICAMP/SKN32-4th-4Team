> 공개 정리본 · 원본: docs/reports/2026-08-05_1327_postgresql_보험도메인_S4_outcome_verification_cohort_완료.md
> 이 문서는 실행 당시의 측정 기록입니다. 같은 항목의 문서가 여러 개면 날짜가 늦은 문서를 현재 결과로 읽으세요.

# PostgreSQL 보험 도메인 S4 Outcome·Verification·Cohort 완료

작성: 2026-08-05 13:27
계획: `docs/plans/2026-08-04_2051_postgresql_보험도메인_통합실행계획_체크리스트.md` S4

## 결과

기존 `/v1/observations`를 실제 PostgreSQL의 `claim → outcome → evidence` 사슬에 연결하고, 관리자 교차검증 전에는 코호트에 반영하지 않다가 append-only verification 후에만 `/v1/cohorts`에서 집계하는 두 번째 수직 슬라이스를 완료했다.

기본 파일 경로는 유지한다. `OUTCOME_PERSISTENCE=postgres`, `VERIFIED_COHORT_STORE=postgres`를 명시한 환경에서만 실제 원장을 사용하며, 장애 시 파일 통계로 폴백하지 않는다.

## 입력·연결 계약

- PostgreSQL 모드는 `paid`, `partial`, `denied` 최종 결과만 저장한다. `pending`을 임의의 outcome으로 바꾸지 않는다.
- `claimed_on`, `decided_on`, 금액, 증빙 종류·SHA-256·보관 참조를 구조화 입력으로 받는다.
- `decided_on >= claimed_on`을 유스케이스와 DB trigger 양쪽에서 강제한다.
- observation 멱등키는 caller scope HMAC, payload는 별도 SHA-256으로 저장하며 원문 키는 저장하지 않는다.
- trace만 아는 제3자가 claim을 선점하지 못하도록 최초 precheck 멱등키도 요구하고, `coverage_review(trace_id, request_key_hash)`가 함께 일치해야 한다.
- claim/outcome/evidence는 한 transaction에서 커밋한다. 같은 요청은 동일 submission으로 재생하고, 변경 payload는 409다.
- 최초 evidence에 submission id를 별도로 묶어 이후 같은 outcome에 증빙이 추가돼도 replay·검수 대상이 바뀌지 않는다.

## 검증·권한 계약

- 공개·외부 제출은 항상 unverified이며 직접 verification을 만들 수 없다.
- `/api/admin/verifications`는 기존 ADMIN 인증과 별도 owner-member `INSURANCE_ADMIN_PG_DSN`을 모두 요구한다.
- SQLite 관리자 username과 `ops.admin_user.login`이 정확히 일치하고 원장 role이 reviewer/admin인 경우만 검수한다.
- consistency 기록 후 `app.record_evidence_verification`으로 append-only verification을 만들고 같은 transaction에서 audit를 남긴다.
- 재검수는 같은 basis일 때 같은 verification을 반환하고, 다른 basis는 409다.
- DB `result=verified`는 검증 상태 전이를 뜻한다. 사실성 등급은 `verification_method=admin_attested`로 별도 보존하고 cohort 응답의 `by_verification`에 노출한다.

## Cohort

`app.cohort_stats`는 판정 당시 assessment policy version을 유지하면서 age band와 verification method를 차원으로 추가했다. verified evidence가 존재하는 outcome만 세며 합성 트랙은 기존 별도 저장소·endpoint를 유지한다.

실측 상태 전이는 다음과 같다.

```text
precheck persisted                           n=0
claim/outcome/evidence submitted             n=0
admin consistency + admin_attested verified n=1
same verification replay                    n=1
by_verification                             {admin_attested: 1}
```

## 주요 변경

- `scripts/db/010_outcome_verification_cohort_contract.sql`
- `app/core/usecases/persist_outcome.py`
- `app/core/usecases/verify_evidence.py`
- `app/adapters/pg_insurance_repository.py`
- `app/adapters/pg_insurance_cohort_stats.py`
- `app/adapters/cohort_stats.py`
- `app/routers/precheck.py`, `app/routers/admin.py`, `app/routers/cohort.py`
- `app/schemas/precheck.py`, `app/schemas/agent.py`
- `app/core/config.py`, `.env.example`

## 호환성·잔여 위험

- 기존 파일 observation·실제 cohort는 설정 기본값에서 그대로 동작한다.
- registered-agent는 별도 `insurance_agent`와 실제 원장의 client 동기화가 없으므로 PostgreSQL outcome 저장을 S5까지 명시적으로 거절한다.
- 공개 claim 연결은 trace+precheck key 두 capability를 요구한다. 인터넷 운영 노출 전 서버 발급 claim capability 또는 rate limit/WAF를 S7에서 확정해야 한다.
- `admin_attested`는 발행처 확인이 아니다. UI/API가 이 등급명을 숨기거나 단순 `verified`로 바꾸면 안 된다.
- 실제 증빙 본문은 object storage 참조만 저장한다. 보관 암호화·삭제 요청·retention 절차는 S7 운영 계약에 남아 있다.

## Claude 교차검토

S4 축약 계약에 대한 읽기 전용 요청은 124초 제한 동안 출력 없이 timeout됐다. S3의 성공한 교차검토 결과는 유지하되, S4 완료 판단은 로컬 보안 대조와 실제 PostgreSQL 검증에만 근거했다.

로컬 대조에서 trace 단독 선점 위험과 다중 증빙 추가 시 replay 불안정을 발견해 각각 precheck key HMAC 결합과 evidence submission identity로 보정했다.

## 검증

```text
schema verifier:
  fresh 001~010 applied / reapply skipped
  checksum conflict=1 / advisory lock=1 / partial rollback=22012
  non-superuser migrator applied=10
  exit code 0

PostgreSQL integration: 7 passed
  wrong precheck capability: 404
  observation first/replay/changed payload: 202/202/409
  cohort before/after verification: 0/1
  verification replay/changed basis: 201/409
  final claim/outcome/evidence/verification row count: each 1

non-PG focused regression: 171 passed
py_compile: passed
git diff --check: whitespace error 0 (line-ending warnings only)
temporary DB/roles: fixture and verifier cleanup
```

전체 프로젝트 CI, DB down·재기동·복구 리허설, 실제 data load는 각각 S7과 S6 게이트로 남긴다.

## 다음 단계

S5에서 별도 agent registry와 실제 원장의 `ops.agent_client` 동기화 방식, auth/interaction retention, consent의 실사용자 원자 생성·철회·만료 전파를 연결한다. 그 전까지 registered-agent PostgreSQL write는 fail-closed다.
