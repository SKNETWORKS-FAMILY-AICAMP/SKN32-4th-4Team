> 공개 정리본 · 원본: docs/reports/2026-08-08_1200_postgresql_보험도메인_S7_local_insurance_real_smoke.md
> 이 문서는 실행 당시의 측정 기록입니다. 같은 항목의 문서가 여러 개면 날짜가 늦은 문서를 현재 결과로 읽으세요.

# PostgreSQL 보험 도메인 S7 local insurance_real smoke

## 결과

- `insurance_real`에 migration 001~012 적용 완료
- `insurance_runtime`가 `insurance_app` membership으로 runtime 연결
- runtime readiness PASS
- `insurance_agent` → `insurance_real` agent mirror dry-run drift 0
- strict release baseline PASS

## 실제 저장 smoke

약관 원문 seed 없이 anonymous API 경로를 사용해 PII 없는 abstain precheck를 저장하고, 이어서 paid outcome/evidence를 저장했다.

| 항목 | 최초 요청 | 동일 요청 재실행 |
|---|---:|---:|
| precheck | `duplicate=false` | `duplicate=true` |
| outcome/evidence | `duplicate=false` | `duplicate=true` |

확인된 row는 subject 1, coverage_review 1, assessment 1, claim 1, outcome 1, evidence 1이다. registered-agent interaction/audit는 anonymous 경로이므로 0이다.

기본 `.env`는 변경하지 않았다. PostgreSQL persistence는 `.env.example`의 DSN와 `PRECHECK_PERSISTENCE=postgres`, `OUTCOME_PERSISTENCE=postgres` 등을 명시적으로 켠 실행에서 사용한다. 전역 기본값 전환 시 legacy registered-agent 호환성 테스트가 실패하는 것을 확인했기 때문이다.

## registered-agent smoke

- `insurance_agent`에 임시 smoke client 생성
- `insurance_real.ops.agent_client` mirror 적용 및 drift 0 확인
- subject reference hash와 `insurance.precheck` consent 저장
- registered-agent precheck/outcome/evidence 저장
- interaction 2건, agent audit 2건 확인
- 동일 요청 재실행에서 precheck/outcome 모두 `duplicate=true`
- raw key를 보관하지 않는 임시 client는 검증 후 source/real 모두 `disabled` 상태로 재동기화

## API 회귀

- public precheck PostgreSQL 원자 저장·멱등·conflict: PASS
- registered-agent outcome PostgreSQL 연결 및 interaction/audit 검증: PASS
- targeted PostgreSQL API tests: 2 passed

## x600 환경 구성

- repo 동기화: `F:\_proj\unified_mall_3_20260808`
- 최초 C 임시 배포본은 F 복사본의 파일 수·용량·가상환경 import 검증 후 제거했으며, 이후 모든 x600 검증은 F 경로에서 수행했다.
- Python 3.14 전체 requirements는 FastAPI/Starlette와 MCP dependency 충돌로 실패
- x600의 uv Python 3.12.13으로 `.venv312` 재구성
- `insightface`는 MSVC Build Tools 부재로 제외한 S7 환경 설치 PASS
- FastAPI/Starlette/psycopg/pytest/torch/transformers import PASS
- x600 로컬 PostgreSQL 5433은 미가동이므로 DB readiness는 실행 불가
- DB 독립 release verifier 테스트 3개 PASS
- SSH reverse tunnel로 x600에서 노트북 PostgreSQL에 연결한 strict baseline PASS
- x600 원격 public precheck PostgreSQL API test PASS
- x600 원격 registered-agent interaction/audit PostgreSQL test PASS
- F 경로 relocation 후 strict verifier 및 public precheck/registered-agent API 회귀 2건 재실행 PASS

reverse tunnel은 검증 명령 동안만 유지했고, x600에 PostgreSQL을 설치하거나 운영 DB 설정을 변경하지 않았다.

x600에는 `.git`을 복사하지 않은 경량 동기화라 remote verifier의 git revision은 unavailable로 보고된다. 대신 migration 001~012 파일별 SHA256은 로컬과 일치한다. release revision까지 원격에서 표시하려면 다음 동기화부터 별도 release manifest를 함께 전달하면 된다.

## 외부 운영 접속 시도

- 현재 세션·`.env`에 외부 `INSURANCE_PG_DSN`/`AGENT_PG_DSN`은 없음
- SSH config의 `x600` 접속은 인증되지만 GPU/repo 환경이 계획과 불일치
- SSH config의 `runpod-gpu`(`213.173.108.41:29685`)는 BatchMode에서 `Connection refused`
- 따라서 외부 DSN strict check와 A/B/C manifest 수집은 실행하지 않았으며, 추정 경로로 접속하지 않았다.

문서에 남아 있는 A/B/C 후보도 재확인했다: x600 `Yeon@<INTERNAL_HOST>`은 SSH PASS, RunPod `root@157.157.221.29:56918` 및 `root@213.173.110.200:11599`는 모두 connection refused였다. 현재 `runpod-gpu` SSH config(`213.173.108.41:29685`)와 문서의 과거 RunPod 주소(`213.173.108.100:29946`)도 동일하게 사용 불가하다.

2026-08-09 재확인에서도 동일했다. x600 F 경로 strict verifier는 insurance readiness PASS, agent readiness PASS, mirror drift 0으로 통과했다.

- PostgreSQL repository integration 전체 회귀 11개 PASS
- persistence/use-case·agent mirror·PG adapter·health/readiness·release verifier 통합 회귀 38개 PASS
- customer/admin 2서버를 현재 PostgreSQL 설정으로 재기동하고 8080·8081 `/api/health/ready` 200/ready=true 및 admin route 분리(8080=404, 8081=401)를 확인했다.

전체 회귀 환경 경계: 기본 전체는 격리된 legacy CS dataset 부재, `legacy_data` 제외는 미기동 local LLM(`127.0.0.1:8002`)에서 중단됐다. 두 marker를 제외한 전체는 5분 제한 내 종료되지 않았으며 PostgreSQL 실패 출력은 없었다.

## 로컬 운영 설정 health 검증

- 명시적 `insurance_real`/`insurance_agent` DSN 및 PostgreSQL persistence 설정으로 `/api/health/ready` HTTP 200, `ready=true`
- insurance/agent PostgreSQL readiness, vector/clause index, candidate-fact source, demo store 모두 준비 상태
- health/readiness/release-verifier focused regression 8개 PASS
