# backend — 백엔드 관련 자료

`develop`(483파일)에서 **백엔드에 해당하는 310개 + 핸드오프 문서 6개**를 남긴 브랜치다.

> ★★**`develop` 에 그대로 머지하지 말 것.** 머지하면 여기 없는 **173개 파일이 삭제된다**
> (수집·전처리·평가·임베딩 스크립트, 프론트 정적 자산 등).
> 올릴 것이 생기면 그 파일만 `cherry-pick` 하거나 경로를 지정해 옮긴다.

---

## 들어 있는 것 (310 + docs 6)

| 갈래 | 수 | 내용 |
|---|---:|---|
| `app/` | 122 | 라우터 13 · `core` 42 · 어댑터 22 · `obs` 9 · 인증 5 · `outer` 5 · 스키마 4 · 서비스 4 · `db` 3 |
| `tests/` | 105 | 계약·라우터·권한·DB 시험 |
| `db/` | 36 | `migrations/postgres/` 22 · `postgres/` 11 · `sqlite_legacy/` 3 |
| `scripts/` | 24 | 서버 실행(`run_*_server`) · 운영(`ops`) · `db/apply.py` · `manage` · `pg` |
| `config/` | 12 | 승인 릴리스 · 세대 프로필 · 환경 예시 |
| `requirements/` | 6 | 런타임·개발·색인·전처리 분리 |
| `docs/handoff/` | 6 | 아래 |

### 뺀 것 (173)

수집 `scripts/crawl` 40 · 평가 `scripts/eval` 60 · 전처리 `scripts/extract` 11 ·
**임베딩 `scripts/index` 9**(→ `embedding` 브랜치) · 파인튜닝 5 ·
프론트 `app/static` 8 · 모델 `app/ml` 4 · `data/raw` 매니페스트 12 등.

---

## 백엔드 핸드오프 문서

| 문서 | 무엇 | 상태 |
|---|---|---|
| `07_계약_백엔드.md` | DB · API · 감사 인터페이스 계약 | **경로 갱신함**(2026-08-25) |
| `02_ERD_및_스키마.md` | ERD · 테이블 정의 | 대조 통과 |
| `16_DB_스키마_적재_의뢰.md` | 스키마 완성·적용 의뢰 | **경로 갱신함** |
| `06_계약_Agent.md` | 외부 에이전트 · MCP 계약 | **부재 1건 명시** |
| `03_에이전트_데이터_축적_설계.md` | 에이전트 데이터 수집·재사용 | 대조 통과 |
| `01_데이터_현황.md` | 무엇이 있고 무엇을 믿을 수 있나 | **부재 1건 명시** |

### ★올리기 전에 문서를 코드와 대조했다

문서가 가리키는 경로를 **이 브랜치의 실제 파일 목록과 기계로 맞춰 봤다.**
어긋난 것은 고치거나, 고칠 수 없으면 **문서 안에 적어 두었다.**

| 어긋남 | 처리 |
|---|---|
| `scripts/db/*.sql` → `db/migrations/postgres/*.sql` | **경로 고침**(4곳) |
| `app/obs/audit.py` → `app/obs/agent_audit.py` | **경로 고침** — 이름이 바뀌어 있었다 |
| `app/db/models_insurance.py` | **없음** — `app/db/models.py` 가 그 자리. 문서에 명시 |
| `scripts/db/load_clauses.py` · `tests/test_load_clauses.py` | **없음** — 계약만 적히고 구현되지 않았다. 문서에 명시 |
| `app/routers/a2a.py` | **없음** — A2A 경로 미구현. 문서에 명시 |
| `config/precheck_mode.json` | 런타임 상태 파일이라 공개본에 없다. 문서에 명시 |

★**지우지 않고 적었다.** 「계약에는 있는데 구현되지 않은 것」이 어디인지가
이 문서들의 값어치다. 없는 것을 조용히 지우면 그 정보가 사라진다.

---

## 구조 — 두 앱을 일부러 가른다

```
scripts/run_customer_server.py  → :8080  고객   경로 33 · 관리자 경로 0
scripts/run_admin_server.py     → :8081  운영   경로 60 · 관리자 경로 21
```

`app/main.py` 의 `create_app(role)` 이 역할에 따라 라우터를 다르게 싣는다.
**고객 앱에는 `/api/admin/*` 이 아예 실리지 않는다** — 인증으로 막는 게 아니라
존재하지 않게 한다. 무인증 노출 표면을 줄이려는 것이므로 **하나로 합치지 말 것.**

## DB 계층

```
db/migrations/postgres/   001_core · 002_grants · 003_embedding · … · 016   (forward-only)
db/postgres/              어댑터 — pg_clause_store · pg_insurance_repository · pgvector_*
db/sqlite_legacy/         옛 런타임. 지우지 않고 남긴다
scripts/db/apply.py       번호 SQL 적용기
```

★`002_grants.sql`·`005_integrity_and_privileges.sql` 이 **외부 스키마**를 만든다 —
런타임 역할 `insurance_app` 은 `core` 를 **읽기만** 하고, 감사 로그는 `INSERT` 만 되며
`UPDATE`·`DELETE` 는 회수돼 있다(append-only). 권한이 곧 도메인 규칙이다.

---

## 띄우는 법

```bash
pip install -r requirements/runtime.txt
python -m scripts.db.apply                 # 스키마 적용(forward-only)
python -m scripts.run_customer_server      # :8080
python -m scripts.run_admin_server         # :8081
pytest -q                                  # 시험(테스트 DB 는 postgres 를 쓴다)
```

★시험 하네스가 **sqlite 가 아니라 postgres** 를 쓴다. 전용 DB(`insurance_pytest`)에
프로세스마다 새 스키마를 만들었다 지운다 — `tests/conftest.py` 참고.
