> 공개 정리본 · 원본: docs/reports/2026-08-03_DDL_LangGraph_3라운드_교차검증.md
> 이 문서는 실행 당시의 측정 기록입니다. 같은 항목의 문서가 여러 개면 날짜가 늦은 문서를 현재 결과로 읽으세요.

# DDL 구현 · LangGraph 완성도 — 코덱스 3라운드 교차검증

2026-08-03 · 대상 `core` 12테이블 DDL 신설 / `app/workflow/precheck_graph.py` 점검

---

## 0. 결과

| 작업 | 이전 | 지금 |
|---|---|---|
| **27테이블 DDL** | **한 줄도 없었다** (alembic 0 · `.sql` 0) | `core` 12 + `ops` 2 **작성·적용·시험 완료** |
| **LangGraph** | 운영 경로에 연결돼 돌고 있음 | **완성 아님.** 치명 결함 1건 포함 7건 진단 |

DDL 은 만들었고, LangGraph 는 **진단만 했다.** 고치지 않았다.

---

## 1. DDL — 왜 멈춰 있었나

실측: `alembic/` 없음 · `scripts/db/` 없음 · `.sql` 0개.
계획서 `2026-08-02_0700_ERD_현행_v4_통합.md` §9-3 이 *"DDL 파일이 아직 없다"* 고 적어둔 그대로였다.

### 그런데 DB 는 이미 돌고 있었다 — 설계와 다른 모양으로

```
mall_vec (PG16, 5433)
  policy_clause_occurrence  353,803   ← s6 186,615 + s5-mixed 158,186 + s6/annex 9,002
  policy_clause_chunk        46,319
  policy_clause_content      14,239
```

- **스키마 접두어가 없다.** 평평한 이름
- `pgvector_clause_index.py` 가 ad-hoc 으로 만든 것
- ★**`s5-mixed` 와 `s6` 가 한 테이블에 섞여 있다.** 우리 `document_extraction` +
  `approval='accepted'`(문서당 1건) 가 막으려던 상황이 **이미 벌어져 있었다**

### 차단요인 판정

| 후보 | 진짜 차단인가 | 근거 |
|---|---|---|
| 확정 문서 0건 | **아니다** | DDL 작성과 적재는 다른 일이다. 빈 스키마를 만드는 데 확정 문서가 필요 없다 |
| DB 합의 미완(PG vs MySQL) | **부분** | 팀원 설계문서가 `TINYINT`(MySQL). `core` 는 우리 소관이라 진행 가능하나 `app` 은 막힌다 |
| 이름 충돌(`case`·`product`) | 아니다 | `app.*` 를 이번 범위에서 뺐다 |
| 테이블 수 미확정 | 아니다 | 22 → 27 로 개정된 것이 기록돼 있다 |

**결론: 막혀 있지 않았다. 그냥 시작되지 않았다.**

---

## 2. 만든 것

```
scripts/db/001_core.sql       스키마 3 + core 12 + ops 2 (admin_user, audit_log)
scripts/db/002_grants.sql     소유자 롤 분리 + 최소 권한
scripts/db/003_embedding.sql  확장 + 낱말 인덱스. 임베딩 컬럼은 모델 확정 후
scripts/db/apply.py           번호순 적용기 — checksum·advisory lock·트랜잭션·forward-only
```

### 범위를 좁힌 근거

| 뺀 것 | 이유 |
|---|---|
| `app.*` 10 | P1~P2. 컬럼·보존정책 미확정 + 팀원 설계문서와 이름 충돌 미해소 |
| `ops.consent` | ★**설계문서의 모순.** P0-b 에 넣어놨는데 `consent.subject_id → app.subject` 이고 `subject` 는 P2다 (코덱스 1라운드 발견) |
| 임베딩 컬럼·ANN | 모델이 바뀌면 전량 재구축. 차원이 스키마에 박힌다 |

### alembic 대신 번호 SQL

저장소에 alembic 이 없고 되돌릴 이력도 없다. 도입 자체가 일이다.
되돌리기가 필요하면 롤백 파일을 만들지 말고 **보정 마이그레이션을 앞으로 더한다**(forward-only).

---

## 3. 코덱스가 잡은 것 — 3라운드

1·2라운드는 계획, **3라운드는 실물 DDL 을 적대적으로 검토**시켰다.
판정: **"이 상태로 커밋하면 안 된다."** 8건 중 치명 2·높음 4.

| # | 지적 | 조치 |
|---|---|---|
| ⑤ | **DDL 과 이력이 다른 트랜잭션.** `.sql` 안의 `COMMIT` 때문에 DDL 이 먼저 커밋되고 이력 INSERT 가 그다음이다 → 사이에 죽으면 **"적용됐는데 이력 없음"** | `.sql` 에서 `BEGIN/COMMIT` 제거, 적용기가 한 트랜잭션으로 감쌈 |
| ⑥ | **`clause_chunk.policy_version_id` 가 모델과 모순.** `content_hash` 하나가 최대 170개 버전에 실리는데 컬럼은 한 값만 담는다. `UNIQUE(content_hash, chunk_index)` 때문에 버전 하나만 임의로 기록된다 | 컬럼 제거. 버전 필터는 `policy_clause` 조인으로 |
| ⑦ | `document_extraction_id` 와 `policy_version_id` 가 **서로 다른 확정문서**여도 통과 | `confirmed_document_id` 를 두고 **복합 FK** 로 강제 |
| ⑧ | `insurance_app` 에 `admin_user` INSERT·UPDATE 를 줬다 — **"관리자 승격은 CLI 전용"을 DB 권한이 정면으로 깨뜨린다** | SELECT 만 |
| ② | `policy_clause.paragraphs jsonb` — 58만 항을 occurrence 마다 저장해 `clause_content` 중복제거(65.4%)를 되돌린다 | **`clause_content` 로 옮김.** 항은 내용의 성질이지 수록의 성질이 아니다 |
| ④ | `REVOKE` 는 **소유자**를 막지 못한다 | `insurance_owner`(NOLOGIN) 를 소유자로 분리 |
| ① | 임베딩을 뺐는데 `vector`·`pg_trgm` 을 001 에서 설치 | 003 으로 이동 |
| ③ | `UNIQUE(id, citeable)` 가 중복 아닌가 | **오탐** — PG16 에서 복합 FK 대상 키로 필요하다. 부분 UNIQUE 는 FK 대상이 못 된다 |

그 밖에 반영: `resolved` ↔ `target_clause_id` 일치 CHECK · 음수 방지 · 날짜 순서 ·
KCD `lo<=hi` · `confidence 0..1` · sha256 hex CHECK · `target_clause_id` 인덱스 ·
재적재 멱등용 자연키 · `IF NOT EXISTS` 제거(drift 은폐 방지) · checksum 전체 저장 ·
dry-run 이 DB 를 건드리지 않게.

내가 추가로 잡은 것: 이력 테이블이 `ops` 에 있으면 001 의 `CREATE SCHEMA ops` 와
충돌한다 → `public.schema_migration` 으로 이동.

---

## 4. 시험 — 만들어졌다 ≠ 작동한다

시험용 DB `ddl_smoke3`(PG16). **불변식 21건 전부 의도대로.**

```
적용        001·002·003 성공 · 재실행 skip(멱등) · dry-run 은 DB 무변경(스키마 0개)
권한        ops.audit_log → insurance_app 에 INSERT,SELECT 만
            ops.admin_user → SELECT 만 · core.* → SELECT 만
```

**차단돼야 하는 것 (15건 전부 차단)**
```
문서당 accepted 2건 · parse_status 누락 · parse_status 에 ok_maybe
확정문서 없이 policy_version · 같은 extraction 에 ordinal 중복
line 에 medical_indemnity · generation 6 · resolution_status 에 maybe
★extraction 과 version 이 다른 문서 · ★paragraphs 개수 불일치
★clause_chunk 에 policy_version_id · resolved 인데 target NULL
sha256 이 hex 64 아님 · ordinal 음수 · valid_from > valid_to
```

**허용돼야 하는 것 (6건 전부 허용)**
```
ordinal 다른 값 · target NULL 준용(ambiguous) · generation NULL(모름)
line 기본값 unknown · 같은 문서면 extraction+version 조합 · ambiguous 준용
```

---

## 5. 적재는 아직 하지 않는다

코덱스 2라운드 판정: **기존 평평한 테이블을 옮기지 말고 원본에서 새로 적재**한다.
평평한 테이블에는 `document_extraction_id` 가 없어 **옮겨도 정보가 안 생긴다.**
백필은 있는 정보를 옮기는 것인데 옮길 정보가 없다.

그런데 지금 적재할 수 없다:

```
data/structured/dbins/s6_pymupdf-1.28.0   137파일 (s5 는 236)
나머지 11개 보험사              s6 없음
```

**s6 재추출이 진행 중이다.** 전량 완료·검증 전에는 전환하지 않는다.
그때까지 기존 `public.*` 을 읽기 경로로 유지한다.

---

## 6. LangGraph — 완성이 아니다

`app/workflow/precheck_graph.py` 311줄. `app/routers/precheck.py:57` 이 부른다. **운영 경로다.**

### 잘된 것

어댑터를 주입받아 흐름과 저장소가 안 얽힌다 · `verdict` 를 그래프가 바꾸지 않는다 ·
재시도 상한이 코드에 박혀 있다(자율 ReAct 아님) · 질병기호를 해시로 담는다 ·
`ARCH-003` 위반을 피해 `app/workflow/` 에 둔 판단과 그 기록.

### ★치명 — 인용이 자기 자신을 검증한다

```python
evidence = [ClauseRow(... text=c.quote ...) for c in outcome.citations]
return uc.verify_explanation(cited_clauses=[...], evidence=evidence, ...)
```

`_verify` 가 **`outcome.citations` 로 evidence 를 만들어 같은 citations 를 검증**한다
(`precheck_graph.py:287-305`). 저장소 원본과 대조하지 않는다.
**조작된 인용도 원칙적으로 자기 인증된다.** 코덱스 발견이고 코드에서 확인했다.

### 나머지 진단

| # | 결함 | 심각도 |
|---|---|---|
| 1 | 자기 인증 (위) | **치명** |
| 2 | **인용 0건 fail-open** — `if not outcome.citations: return True` (284-286). 근거 없는 양성 판정이 통과한다 | 높음 |
| 3 | 기권 시 `st.clauses` 를 안 비운다 — 응답과 내부 상태가 갈라진다 | 높음 |
| 4 | `build_langgraph()` 의 dict state 에 원문 `body` 가 들어간다 → 체크포인트·트레이싱에 KCD 원문이 남을 수 있다 | 높음 |
| 5 | **`retarget` 이 운영에서 항상 `None`** — 독스트링 다이어그램의 "표적 검색 1회" 는 죽은 경로 | 높음 |
| 6 | `trail` 이 실행되지 않은 노드 5개를 기록 — 감사 로그가 부정확 | 중간 |
| 7 | 운영은 `invoke()` 순차 실행기. `build_langgraph()` 는 테스트에서만 — **"LangGraph 로 만들었다"는 주장과 실제가 다르다** | 중간 |
| 8 | `invoke()` 에는 `guard` 이중 잠금이 있는데 LangGraph 쪽에는 없다 | 중간 |

### 오늘 / 발표 후

기준: **지금 상태로 시연하면 틀린 답이 사용자에게 나가나.**

| 오늘 | 발표 후 |
|---|---|
| ① 자기 인증 — 저장소에서 원문을 다시 읽어 대조 | ⑤ retarget 구현 |
| ② 인용 0건 fail-closed (단, **이미 기권한 결과를 덮지 않는다**) | ⑥ trail 정직화 |
| ③ 기권 시 `st.clauses=()` | ⑦ LangGraph 일원화 또는 명칭 정리 |
| ④ raw `body` 를 그래프 상태에서 제외 | ⑧ 안전장치 대칭 |

코덱스 추산: 운영 코드 40~65줄 + 테스트 45~75줄, **반나절.**

★자기 인증 수정 시 주의 — `qualified_no` 만 비교하면 부족하다(문서 내 중복 31,085건).
`applied_policy.sha256` 원문을 다시 읽어 **clause_id + 쪽 범위 + qualified_no + 정규화 quote 포함**
을 모두 확인해야 한다.

★그리고 대조 원본으로 **지금 PG 를 쓰면 안 된다** — s5/s6 가 섞여 있다.
시연은 accepted 로 고정한 파일 저장소에 맞춘다.

---

## 7. 남은 것

| 순위 | 항목 |
|---|---|
| **P0** | LangGraph 오늘분 4건 |
| **P0** | DB 합의(PG vs MySQL) — 팀원 설계문서 `TINYINT`. `app.*` 는 이것 없이 못 만든다 |
| P1 | s6 전량 완료 후 `core.*` 적재 스크립트 |
| P1 | `app.*` DDL — 이름 충돌(`case`·`product`) 해소 후 |
| P1 | KCD 분류표 확보 — `kcd_version`·`kcd_code` 적재원이 저장소에 없다 |
| P2 | 기존 평평한 테이블 정리 — 새 경로 안정화 후 legacy 스키마로 |

## 참조

- `scripts/db/001_core.sql` · `002_grants.sql` · `003_embedding.sql` · `apply.py`
- 02_ERD_및_스키마 (내부 원본 참조: `../handoff/02_ERD_및_스키마.md`) — 27테이블 설계
- ERD 핸드오프 교차검증 (내부 원본 참조: `2026-08-02_ERD_핸드오프_교차검증_리포트.md`)
