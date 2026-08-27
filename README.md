# 📑올바른 보험비서 — KCD 질병기호 × 실손보험 약관 사전판정

> 진료비 내역서의 **질병기호(KCD)** 와 **실손보험 약관**을 매칭해, 내가 청구했을 때 보장 가능한지를
> 약관 근거와 함께 미리 확인해주는 RAG 기반 보험 상담 서비스입니다.

<br/>

## 1. 팀 소개 — 팀명 "비서단"

| <img src="https://github.com/kimji9663.png" width="120"> | <img src="https://github.com/cls15rn.png" width="120"> | <img src="https://github.com/changlike.png" width="120"> | <img src="https://github.com/jjh430.png" width="120"> | <img src="https://github.com/roroblack.png" width="120"> |
|:---:|:---:|:---:|:---:|:---:|
| 김지혜 | 서유현 | 송채영 | 정재희 | 최연우 |
|<a href="https://github.com/kimji9663"><img src="https://img.shields.io/badge/kimji9663-181717?style=for-the-badge&logo=github&logoColor=white"></a>|<a href="https://github.com/cls15rn"><img src="https://img.shields.io/badge/cls15rn-181717?style=for-the-badge&logo=github&logoColor=white"></a>|<a href="https://github.com/changlike"><img src="https://img.shields.io/badge/changlike-181717?style=for-the-badge&logo=github&logoColor=white"></a>|<a href="https://github.com/jjh430"><img src="https://img.shields.io/badge/jjh430-181717?style=for-the-badge&logo=github&logoColor=white"></a>|<a href="https://github.com/roroblack"><img src="https://img.shields.io/badge/roroblack-181717?style=for-the-badge&logo=github&logoColor=white"></a>|
|     | 데이터 처리 및 검수 <br> 산출물 제작 | 데이터 처리 및 검수 <br> 산출물 제작 |      |       |
<br/>

## 2. WBS

| 단계 | 주요 작업 |
|---|---|
| 1. 기획 | 주제 선정, 사용자 시나리오, 요구사항 정의 |
| 2. 데이터 수집 | 보험사별·세대별 약관 PDF, KCD 질병기호, 공공데이터 수집 | 
| 3. 데이터 전처리 | 약관 구조화, 조항·부록 분리, 표·OCR 후보 복원, 품질 게이트 | 
| 4. 시스템 설계 | 아키텍처, DB 스키마, API와 팀 간 데이터 계약 설계 | 
| 5. 백엔드/DB 개발 | FastAPI, 인증·권한, PostgreSQL·pgvector, 관리자 기능 | 
| 6. RAG 파이프라인 개발 | 청킹, Arctic-ko 임베딩, Hybrid Retrieval, 리랭킹 | 
| 7. 프론트엔드 개발 | 보험정보 입력, 사전판정, 관리자·마이페이지 화면 | 
| 8. 통합 테스트 | 검색·판정 품질, 인용·보안·release 회귀 테스트 | 
| 9. 발표 준비 | 발표자료, 시연 시나리오, 팀 전달 패키지 | 

<br/>

## 3. 프로젝트 개요

실손보험은 **세대별(1~5세대)·보험사별로 약관 구조와 보장 조건이 달라** 소비자가 본인이 받은 진료의 보장 여부를 스스로 판단하기 어렵습니다. 

**올바른 보험비서**는 사용자가 보험 상품 정보와 진료비 내역의 질병기호를 입력하면 해당 판본의 약관을 검색하고, 보장·면책 근거와 추가로 확인할
사항을 함께 안내합니다.

<br/>

## 4. 프로젝트 소개

1. **보험정보 등록** — 상품명·보험사명·보험계약일 입력 → 보험 세대와 적용 약관 판본 확인
2. **질병기호 기반 보장 확인** — KCD 코드 또는 병명으로 관련 보장·면책 조항 검색
3. **약관 챗봇 상담** — 검색된 원문 조항을 근거로 답변하고 어려운 보험·의학 용어 설명
4. **OCR·표 정보 활용** — 자기부담금처럼 본문 추출이 어려운 표를 복원하고 사람 승인 후 반영
5. **관리자 대시보드** — 인덱스 상태, 문의 로그, 미해결 질의, 검증 큐와 운영 보고서 확인
6. **무폴백 판정** — 문서 판본·인덱스·근거가 불완전하면 임의 답변 대신 확인 불가 상태 반환

<br/>

## 5. 프로젝트 배경

- 실손보험 약관은 세대와 보험사마다 보장 범위·문서 양식이 달라 일반 소비자가 실제 보장 여부를
  판단하기 어렵습니다.
- 진료비 내역서에는 질병기호가 표기되지만, 이 코드를 가입한 상품의 정확한 약관 판본과 연결해
  설명하는 과정은 복잡합니다.
- 약관의 표·부록·각주에는 자기부담금, 지급률, 질병분류처럼 판정에 필요한 정보가 많지만 일반적인
  PDF 텍스트 추출만으로는 행·열 관계가 손실될 수 있습니다.
- 이에 팀 비서단은 **약관 문서(비정형) + KCD 코드(정형) + 사람 승인 OCR facts**를 연결해,
  사용자가 자신의 보험으로 청구 가능한지를 근거 중심으로 확인할 수 있는 서비스를 개발했습니다.

<br/>

## 6. 주요 기능

| 기능 | 설명 | 
|---|---|
| 세대별·보험사별 약관 RAG | 가입 상품과 계약일에 해당하는 약관 판본 안에서 관련 조항 검색 | 
| 질병명 → 질병코드 매칭 | KCD 코드를 모르는 사용자를 위한 병명·코드 검색 |
| 보장 사전판정 | 질병기호, 상품 세대, 보장·면책 조항을 조합해 판정 상태 제공 |
| 근거·인용 검증 | 검색 조각의 부모 조항을 복원하고 인용 가능성과 문서 신선도 검사 |
| Hybrid Retrieval | Arctic-ko dense 검색과 pg_trgm lexical 검색을 RRF로 결합 | 
| Qwen3 리랭킹 | Qwen3-Reranker-4B로 top-k 후보 재정렬 | 
| OCR 표 복원 | 자기부담금 등 표 후보를 복구하고 사람 승인된 facts만 검색에 반영 |
| 관리자 대시보드 | 인덱스 상태, 문의·이벤트, 지식갭, 검증 큐, PDF 보고서 |
| 음성·화상 상담 | STT/TTS 상담과 얼굴 로그인 2차 인증 |
| 장해 지급률·지연이자 후보 | B8/F4 후보 8,622 facts를 shadow로 검증 |

<br/>

## 7. 기술 스택

| 영역 | 기술 |
|---|---|
| 언어 | Python 3.12 |
| 백엔드 | FastAPI, Pydantic, SQLAlchemy | 
| RAG 오케스트레이션 | LangChain, LangGraph, 자체 포트·어댑터 계층 | 
| 임베딩 | `dragonkue/snowflake-arctic-embed-l-v2.0-ko` | 
| 리랭커 | `Qwen/Qwen3-Reranker-4B` | 
| 벡터 검색 | PostgreSQL + pgvector HNSW, FAISS | 
| 어휘 검색 | PostgreSQL `pg_trgm` | 
| 정형 DB | PostgreSQL, SQLite 개발 환경 | 
| PDF·OCR 전처리 | PyMuPDF 기반 구조 추출 + 선별 OCR 파이프라인 | 
| 프론트엔드 | HTML, CSS, Vanilla JavaScript | 
| 인증·보안 | JWT, RBAC, 얼굴 2차 인증, fail-closed gate | 
| 테스트·CI | pytest, GitHub Actions | 
| LLM | 로컬 Gemma 4 E4B Instruct Q4_0, OpenAI/Gemini 선택 구성 | 

<br/>

## 8. 프로젝트 구조

```text
app/
├─ application/         유스케이스와 포트
├─ adapters/            pgvector·파일·LLM·리랭커 어댑터
├─ core/                도메인 규칙, release, eligibility
├─ routers/             FastAPI REST API
├─ static/              고객·관리자 프론트엔드
├─ mcp/                 MCP 서버
└─ ml/                  음성·얼굴·의도 모델
config/                 승인 release와 모델·추출 설정
data/                   카탈로그, 평가셋, manifest
docs/handoff/           팀 간 데이터·API 계약과 인수인계
scripts/
├─ extract/             PDF·조항·표 전처리
├─ index/               청킹·임베딩·pgvector 적재
├─ eval/                검색·OCR·리랭커 평가
└─ manage.py            DB migration·ingest·운영 관리
tests/                  회귀·보안·계약 테스트
```

<br/>

## 9. 시스템 아키텍처

```mermaid
flowchart TB
  subgraph UI["사용자·운영 화면"]
    U1["보험정보·KCD 입력"]
    U2["약관 챗봇·음성/화상 상담"]
    U3["관리자 대시보드"]
  end

  subgraph API["FastAPI"]
    A1["보험 사전판정 API"]
    A2["RAG·용어 설명 API"]
    A3["인증·RBAC·운영 API"]
  end

  subgraph DOMAIN["도메인·안전 게이트"]
    D1["보험 세대·약관 판본 확정"]
    D2["KCD 매핑·보장 규칙"]
    D3["release·citation·freshness 검증"]
  end

  subgraph RAG["검색·생성"]
    R1["Arctic-ko Dense Retrieval"]
    R2["pg_trgm Lexical Retrieval"]
    R3["RRF Fusion"]
    R4["Qwen3-Reranker-4B"]
    R5["부모 조항 복원·근거 답변"]
  end

  subgraph DATA["데이터"]
    P1["보험 약관·부록·표"]
    P2["KCD 매핑 DB"]
    P3["PostgreSQL + pgvector"]
    P4["승인 OCR facts"]
    P5["미승인 shadow candidates"]
  end

  U1 --> A1
  U2 --> A2
  U3 --> A3
  A1 --> D1
  A1 --> D2
  A2 --> D3
  D1 --> P1
  D2 --> P2
  D3 --> R1
  D3 --> R2
  R1 --> R3
  R2 --> R3
  R3 --> R4
  R4 --> R5
  P3 --> R1
  P4 --> P3
  P5 -.사람 승인 전 차단.-> D3
  R5 --> U2
  A1 -.근거 없음·판본 불명.-> U3
```

<br/>

## 10. 데이터 파이프라인

1. **수집** — 보험사별 실손보험 약관 PDF, 상품·판매기간, KCD 질병기호와 공공데이터 수집
2. **원본 보존** — 문서 SHA-256, 보험사, 상품명, 판매기간, 출처 URL과 원문 PDF 보존
3. **구조 추출** — 페이지 텍스트·좌표, 조항·항·호, 별표·붙임, 표 후보 추출
4. **선별 OCR** — 일반 추출로 충분한 페이지는 통과시키고 OCR이 필요한 표 후보만 GPU 처리
5. **품질 게이트** — 조항 경계, 표 축·금액·출처, citation eligibility와 중복 레이아웃 검증
6. **사람 승인** — candidate fact를 대표 패턴으로 축소해 원문 검수 후 승인·격리
7. **청킹·임베딩** — 승인 조항과 facts를 Arctic-ko로 임베딩해 pgvector에 적재
8. **검색·리랭킹** — dense+lexical 후보를 결합하고 Qwen3-Reranker-4B로 재정렬
9. **근거 답변** — 부모 조항과 원문 위치를 함께 반환하며 근거가 없으면 지식갭으로 기록

<br/>

## 11. 실행 파이프라인

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. 환경 설정
cp .env.example .env

# 3. DB 준비
python -m scripts.manage migrate

# 4. 기본 문서 인덱스
python -m scripts.manage ingest

# 5. PostgreSQL + pgvector와 합성 검수 DB 사용 시
python -m scripts.pg start
python -m scripts.pg init       # 실제 약관/RAG DB
python -m scripts.pg init-demo  # 분리된 insurance_demo DB
python -m scripts.pg init-agent # 분리된 insurance_agent 인증·감사 DB
python -m scripts.index.build_clause_index

# 기존 파일 기반 합성 제출물 이전(먼저 검증, 그다음 적용)
python -m scripts.migrate_demo_files_to_pg
python -m scripts.migrate_demo_files_to_pg --apply

# 6. API 실행
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 7. 준비 상태 확인
curl http://127.0.0.1:8000/api/health/ready
```

합성 에이전트 제출물을 PostgreSQL에 저장하려면 `.env`에 아래처럼 지정합니다.

```dotenv
DEMO_STORE_BACKEND=postgres
DEMO_PG_DSN=host=127.0.0.1 port=5433 user=postgres dbname=insurance_demo
```

관리자 화면의 **자동 합성 정합성 검사**는 ID·보험사·판매일·KCD 단일 코드·연령대·결과값·증거 형식을
결정론적 규칙으로 검사한 뒤 통과한 합성 건만 승격합니다. 이는 보험금 지급 여부나 실제 문서의 진실성을
승인하는 판정기가 아닙니다. 실제 보장 사전판정은 `/v1/prechecks` 및 같은 유스케이스를 호출하는 MCP
`precheck`가 약관 규칙과 출처를 근거로 별도 수행합니다.

LLM은 `.env`의 `LLM_PROVIDER`로 선택합니다. 실행 스크립트가 이 값을 덮어쓰지 않습니다.

```bash
# 로컬 Gemma 4 E4B
python scripts/download_gemma.py      # 최초 1회
python scripts/local_model_server.py  # http://127.0.0.1:8002/v1

# 또는 .env에서 LLM_PROVIDER=openai / gemini와 해당 API 키 설정

# 선택한 provider 실제 연결·최소 생성 확인
python scripts/llm_smoke.py

# MCP stdio에서 같은 LLM 용어 설명까지 왕복 확인
python scripts/mcp_smoke.py
```

`GET /api/health`의 provider 설정값과 `GET /api/health/llm`의 실제 연결 결과를 구분해 확인합니다.

MCP는 `precheck`, `explain_term`, `cohort_stats`, `submit_observation` 4개 도구를 제공합니다.
`explain_term`은 고객 `/v1/chat` 라우터를 그대로 호출하므로 브라우저와 MCP가 같은 provider·근거·안전 게이트를 사용합니다.

등록 외부 에이전트 REST는 고객 UI와 별도 앱·포트에서 실행합니다.

```bash
# .env에서 AGENT_HASH_SECRET(32자 이상 별도 난수)을 설정한 뒤
python -m scripts.pg init-agent
python -m scripts.agent_clients create --client-id partner-a --name "Partner A" \
  --scope precheck:read --scope terms:read --scope cohort:read \
  --scope observations:write --rate-limit 60

# 발급 키를 안전한 비밀 저장소에 옮긴 뒤 명시적으로 활성화
# AGENT_API_ENABLED=true
python -m scripts.run_agent_server  # 기본 http://127.0.0.1:8082

# 운영 관리(키 원문은 create/rotate 성공 시 한 번만 출력)
python -m scripts.agent_clients list
python -m scripts.agent_clients rotate --client-id partner-a
python -m scripts.agent_clients disable --client-id partner-a
python -m scripts.agent_clients prune
```

보호 경로는 `/v1/agent/*`이며 `Authorization: Bearer <agent-key>`와 개인정보가 아닌
`X-Agent-Subject` opaque 참조가 필요합니다. observation에는 `Idempotency-Key`도 필수입니다.
비-loopback bind는 `ALLOW_REMOTE_AGENT_BIND=true`를 명시하기 전에는 기동이 거부됩니다.
이 옵션은 TLS·방화벽을 대신하지 않습니다. 실제 DNS/TLS 배포와 키 전달은 별도 운영 작업입니다.

프론트엔드 실행:

```bash
python -m scripts.run_customer_server   # http://127.0.0.1:8080
python -m scripts.run_admin_server      # http://127.0.0.1:8081
```

### 관리자 계정 만들기 (새 PC에서 반드시 필요)

> **계정은 저장소에 포함되지 않습니다.** 사용자 DB(`data/db/insurance.sqlite3`)는
> `.gitignore` 대상이라 커밋되지 않습니다. 그래서 **PC를 옮기면 계정이 없습니다** —
> 다른 사람이 쓰던 아이디·비밀번호는 그 PC에서 통하지 않습니다. 아래를 그 PC에서 한 번 실행하세요.

```bash
# 0) .env 에 SECRET_KEY 가 있어야 한다 — 없으면 회원가입·로그인이 명시적으로 거부된다
#    (임의 기본키로 조용히 넘어가지 않는 설계. 아무 값이나 길게 넣으면 된다)
cp .env.example .env          # 이미 있으면 건너뜀

# 1) 테이블 생성(멱등)
python -m scripts.manage migrate

# 2) 계정 생성 — 관리자 서버를 띄운 뒤 화면의 "계정 만들기" 로 가입하거나 API 로
python -m scripts.run_admin_server        # 다른 터미널에서 실행해 둔다
curl -X POST http://127.0.0.1:8081/auth/signup ^
     -H "Content-Type: application/json" ^
     -d "{\"username\":\"demo_admin\",\"password\":\"바꾸세요\"}"

# 3) ★관리자로 승격 — 이 단계는 CLI 전용이다
python -m scripts.manage promote demo_admin

# 4) 확인
python -m scripts.manage ready
```

**왜 승격이 CLI 전용인가** — 화면에 "관리자로 가입" 버튼을 두면 가입한 누구나 관리자가
됩니다(권한 상승). 그래서 **최초 1명의 부트스트랩만** CLI로 막아 둡니다.
그 뒤로는 관리자가 대시보드의 **사용자 관리** 패널에서 다른 계정을 승격·강등할 수 있습니다.

| 명령 | 하는 일 |
|---|---|
| `python -m scripts.manage promote <username>` | USER → ADMIN |
| `python -m scripts.manage demote <username>` | ADMIN → USER (**마지막 관리자는 거부** — 잠금 방지) |
| `python -m scripts.manage face-reset <username>` | **등록된 얼굴 해제** — 얼굴 2FA로 잠겼을 때 |

> **얼굴 2FA로 잠겼다면** — 얼굴을 등록하면 다음 로그인부터 얼굴이 필요합니다.
> 카메라 없는 PC로 옮기거나 조명·외모가 바뀌어 임계값을 못 넘으면 **해제하려면 로그인해야 하고
> 로그인하려면 얼굴이 필요한** 상태가 됩니다. 그때 `face-reset`으로 풉니다(비밀번호는 그대로).

계정이 하나도 없는 상태에서 관리자 화면(8081)에 접속하면 로그인 게이트만 보입니다.
그건 고장이 아니라 **인증 전에는 데이터를 요청하지 않는** 설계입니다.

<br/>

## 12. RAG 평가

| 평가 항목 | 현재 결과 |
|---|---|
| 평가 질의 | 417 |
| 전체 Hit@1 | 63.79% |
| 검색 가능한 질의 Hit@1 | 84.71% |
| 검색 가능한 질의 MRR@10 | 0.9101 |
| S7.1 top20 후보 쌍 | 8,285 |
| 승인 OCR fact 유입 | 23쌍 · 6질의 |
| 기존 정답 순위 회귀 | 0건 |
| 활성 인덱스 top20 지연 | p50 323ms · p95 364ms |
| 승인 OCR 동일 벡터 검사 | rank 1 · 거리 0 |

평가는 **근거성, 검색 정확도, 재현성, 환각 방지, 인용 가능성, 응답 품질**을 함께 확인합니다.
현재 평가셋은 기존 조항 검색 비회귀를 검증하며, 신규 자기부담금 facts 자체를 직접 평가하는 독립
holdout은 후속 과제로 남아 있습니다.

<br/>

## 13. 향후 계획

- [ ] B8 장해 지급률 26개 고유 패턴 사람 검수·승인
- [ ] F4 지연이자 9쪽 사람 검수·승인
- [ ] 승인 B8/F4 facts의 청크·임베딩·증분 인덱스 적재
- [ ] 신규 OCR facts 전용 독립 holdout 평가셋 구축
- [ ] 보험사·세대별 조항 span precision/recall 확대
- [ ] 동시 사용자 검색·리랭킹 부하 테스트와 운영 모니터링
- [ ] 배포 환경과 비밀정보·모델 캐시 전달 절차 확정

<br/>

## 14. 데이터 출처

- 질병 분류 기호 검색 — [kcdcode.kr](https://kcdcode.kr)
- 공공데이터포털 실손보험정보 API — [data.go.kr](https://www.data.go.kr)
- 보험협회 통합 약관 공시 — [pub.insure.or.kr](https://pub.insure.or.kr)
- 보험사별 상품·약관 공시 페이지
- 참고 유사 서비스 — [koicd.kr](https://koicd.kr)

> 본 서비스의 판정은 약관 원문 확인을 돕는 사전 안내이며, 보험금 지급 여부를 확정하는 법률·의학적
> 판단이 아닙니다. 실제 청구 결과는 보험사 심사와 계약 조건에 따라 달라질 수 있습니다.

<br/>

## 15. 제출 산출물

프로젝트 제출용 문서는 [제출 산출물 인덱스](docs/submission/00_제출산출물_인덱스.md)에서 한 번에 확인할 수 있습니다.
