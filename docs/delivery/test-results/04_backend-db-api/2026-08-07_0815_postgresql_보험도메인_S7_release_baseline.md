> 공개 정리본 · 원본: docs/reports/2026-08-07_0815_postgresql_보험도메인_S7_release_baseline.md
> 이 문서는 실행 당시의 측정 기록입니다. 같은 항목의 문서가 여러 개면 날짜가 늦은 문서를 현재 결과로 읽으세요.

# PostgreSQL 보험 도메인 S7 release baseline

## 목적

운영 DB를 변경하지 않고 release 직전 상태를 한 번에 점검하는 기준 명령을 추가했다.

```powershell
python -m scripts.ops.verify_release --json
```

검증 항목은 다음과 같다.

- 현재 git revision, branch, worktree dirty 여부
- `scripts/db/*.sql` migration 수, 파일별 SHA256, expected latest
- `INSURANCE_PG_DSN`이 있으면 보험 runtime readiness
- `AGENT_PG_DSN`이 있으면 agent runtime readiness
- `INSURANCE_ADMIN_PG_DSN`과 agent source/admin DSN이 있으면 agent mirror drift

모든 DB 검증은 read-only다. mirror는 내부적으로 `apply=False`로만 실행하며 migration을 적용하지 않는다.

## 로컬 실행 결과

- revision: `5e0a472f40243544025987b861f40c59f9d3db24`
- branch: `main`
- worktree: dirty (기존 작업 및 이번 변경 포함)
- migration: 12개
- latest: `012_runtime_migration_readiness_grant.sql`
- latest 일치: PASS
- 보험 readiness: `INSURANCE_PG_DSN` 미설정으로 skipped
- agent runtime readiness: 기본 agent DSN 연결 및 필수 ops 테이블 확인 PASS
- agent mirror: `INSURANCE_ADMIN_PG_DSN` 미설정으로 skipped
- 전체 baseline: PASS (미설정 연결을 허용하는 기본 모드)

배포 직전에는 다음처럼 strict 모드로 실행한다.

```powershell
python -m scripts.ops.verify_release --strict --json `
  --insurance-dsn "$env:INSURANCE_PG_DSN" `
  --insurance-admin-dsn "$env:INSURANCE_ADMIN_PG_DSN" `
  --agent-source-dsn "$env:AGENT_ADMIN_PG_DSN"
```

`--strict`에서는 DSN 누락, readiness 실패, mirror drift, migration latest 불일치를 실패로 처리한다. 이 명령도 migration 적용이나 mirror 변경은 하지 않는다.

## 남은 확인

- 로컬 PostgreSQL에는 현재 `insurance_real` 고정 운영 DB가 없어 실제 운영 DSN readiness는 실행하지 못했다.
- SSH 3대의 호스트·계정·키/프록시 정보가 아직 없어 원격 `remote_who`와 rollback rehearsal은 대기 중이다.
- 다음 작업은 실제 DSN으로 strict baseline을 실행하고, mirror drift가 0인지 확인한 뒤 SSH A/B/C에 동일 revision·migration checksum을 수집하는 것이다.

## 원격 접속 사전 점검 결과

- `<GPU_SSH_HOST>`: BatchMode SSH 인증 PASS. hostname은 `x600_251214`, 계정은 `playdata\playdata2`.
- 해당 호스트에서는 `nvidia-smi`가 없고 `F:\`, `C:\pagejob`, 예상 repo 경로가 없어 계획서의 GPU box/release 노드와 불일치한다.
- 원격 `python -m scripts.ops.verify_release --json`은 `scripts` 모듈을 찾지 못해 실행하지 않았다. 파일을 전송하거나 경로를 추정해 실행하지 않고 중단했다.
- RunPod `root@213.173.108.100:29946`: BatchMode 연결 거부. 원격 변경은 하지 않았다.

따라서 현재 확인된 SSH 결과는 “접속 가능 호스트 식별” 단계이며, A/B/C release 수집 PASS가 아니다. 실제 운영 노드의 호스트·repo 경로·DB DSN 제공 후 수집기를 실행해야 한다.

## SSH 수집기 준비

접속정보를 받은 뒤 아래처럼 먼저 명령만 확인할 수 있다.

```powershell
.\scripts\ops\collect_remote_release.ps1 `
  -Node "A=user@host-a|C:\repo;B=user@host-b|C:\repo;C=user@host-c|C:\repo" `
  -KeyFile "C:\keys\ops_ed25519" -DryRun -Json
```

실제 수집은 `-DryRun`을 제거한다. 원격에서는 `python -m scripts.ops.verify_release --json`만 실행하며 migration 적용, mirror apply, 데이터 변경은 하지 않는다.

## 로컬 실제 DB 구성 및 strict 검증

- `insurance_real` 신규 DB 생성
- `insurance_runtime` LOGIN role 생성 및 `insurance_app` membership 부여
- 기존 migration 001~012 적용 완료
- runtime DSN readiness PASS
- `insurance_agent` → `insurance_real` mirror dry-run: source 0 / target 0 / drift 0
- strict baseline PASS: insurance readiness, agent readiness, migration latest, mirror in-sync

기본 `.env`에는 이 설정을 강제로 넣지 않았다. 전역 기본값을 PostgreSQL로 바꾸면 기존 legacy registered-agent 호출이 `AGENT_REAL_LEDGER_ENABLED` 게이트에서 실패하는 호환성 이슈가 확인되므로, 실제 실행 시 `.env.example`의 DSN/persistence 항목을 명시적으로 활성화하는 방식으로 유지한다.

## 실제 persistence smoke

약관 원문 seed 없이 저장 가능한 PII-free anonymous 경로로 실제 `insurance_real`에 smoke를 실행했다.

- abstain precheck 저장: `duplicate=false`
- 같은 precheck 재실행: `duplicate=true`
- paid outcome + evidence 저장: `duplicate=false`
- 같은 outcome 재실행: `duplicate=true`
- 최종 row 수: subject 1, coverage_review 1, assessment 1, claim 1, outcome 1, evidence 1
- registered-agent interaction/audit row: 0 (anonymous API 경로이므로 정상)
