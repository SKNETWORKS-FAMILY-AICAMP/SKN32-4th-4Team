# 05C. 사용 LLM 모델

> [05_프로젝트_발표_보고서.md](05_프로젝트_발표_보고서.md) §8 의 부록.
> **발표 보고서 제출 요구 항목 「사용 LLM 모델」의 정본이다.**
> 파인튜닝은 별도 문서 → [05D_파인튜닝_모델_설계.md](05D_파인튜닝_모델_설계.md)
> 기준일 2026-08-04

---

## 0. 한 장 요약

| 슬롯 | 선택 모델 | 상태 | 근거 |
|---|---|---|---|
| **답변 생성** | `google/gemma-4-E4B-it-qat-q4_0-gguf` (Q4_0 · llama.cpp) | 기본 활성 · ★**artifact SHA·라이브 승인 미완료** | §2 |
| **약관 임베딩** | `dragonkue/snowflake-arctic-embed-l-v2.0-ko` (1024d) | ★**운영 인덱스 적용 중** · 선정은 예비 후보 단계 | §3 |
| **검색 리랭커** | `Qwen/Qwen3-Reranker-4B` | 오프라인 release 완료 · ★**실시간 기본 OFF** | §4 |
| **저지연 폴백 리랭커** | `BAAI/bge-reranker-v2-m3` | 같은 평가셋에서 측정 · 선언만 | §4 |
| **선택형 외부 생성** | `gpt-4o-mini` · `gemini-2.5-flash` | 키가 있을 때만. 기본값 아님 | §2-4 |
| **OCR / VLM** | 선별 OCR 파이프라인 | ★**전처리 도구다. 답변 LLM 이 아니다** | §5 |

★**파인튜닝한 자체 모델은 없다.** 이번 릴리스의 품질 향상은 ①데이터 정제 ②임베딩 모델 교체
③리랭커 도입 ④사람 승인 OCR facts 로 얻었다. 발표에서 "파인튜닝 완료"라고 말하지 않는다.

---

## 1. 모델을 코드에 흩지 않는다 — `model_registry.yaml`

모델 ID·revision·checksum·검증정보를 **소스가 아니라 데이터**에 둔다
(`REQ-LLM-REG-01` · `tests/test_model_registry.py` 가 강제).

```yaml
profiles:
  - profile_id: local_gemma4_e4b
    provider: local
    provider_model_id: gemma-4-e4b
    revision: bb3b92e6f031fa438b409f898dd9f14f499a0cb0
    artifact_sha256: null        # ★로컬 GGUF 해시 미측정 → verified=false
    runtime: llama-cpp-python
    quantization: q4_0
    verified_at: null            # ★라이브 승인 안 됨
    supported_tasks: [rag_qa, chat]
    max_tested_context: 1024
    memory_peak_mb: null         # ★미측정
    tool_call_verified: false

  - profile_id: local_qwen35_4b   # ★선언된 대체 프로필. 현재 사용 LLM 이 아니다
    provider: local
    provider_model_id: qwen3.5-4b-instruct
    ...
    tool_call_verified: true
```

**규칙 세 가지**

1. `latest` alias 금지 — 어제 답과 오늘 답이 달라지면 재현이 불가능해진다
2. family-only pin 금지 — 반드시 구체 `provider_model_id`
3. `artifact_sha256` / `verified_at` / `memory_peak_mb` 가 `null` 이면 **「선언됨·미검증」**
   → 라이브 승인 전까지는 결정론 테스트(Fake)로만 쓴다

★**`null` 을 비워 둔 채 남겨 뒀다.** 채워 넣으면 "검증했다"가 되고, 그건 거짓이다.

---

## 2. 답변 생성 LLM — Gemma 4 E4B

### 2-1. 무엇을 어떻게 띄우나

```
scripts/local_model_server.py
  repo_id  = google/gemma-4-E4B-it-qat-q4_0-gguf
  filename = gemma-4-E4B_q4_0-it.gguf
  runtime  = llama-cpp-python
  port     = 8002              ← ★앱 개발 서버 8000 과 분리
  n_ctx    = 1024 (기본값)      ← 환경변수 N_CTX 로 조정
  인터페이스 = OpenAI 호환 /v1
```

```env
LLM_PROVIDER=local
LOCAL_BASE_URL=http://127.0.0.1:8002/v1
LOCAL_MODEL=gemma-4-e4b
LOCAL_API_KEY=not-needed
LLM_CHAT_ENABLED=true
LLM_REQUEST_TIMEOUT_SECONDS=120
LLM_HEALTH_TIMEOUT_SECONDS=3
```

애플리케이션은 SDK 를 직접 부르지 않고 **gateway**(`app/adapters/llm_gateway.py`)를 부른다.
그래서 `.env` 한 줄로 OpenAI·Gemini 구성으로 갈아 끼운다.

> ⚠ **문서-코드 불일치 1건**: `scripts/local_model_server.py:14` 주석은 `N_CTX` 기본을 "4096" 이라 적었는데
> 실제 코드(`:62`)는 `"1024"` 다. 레지스트리의 `max_tested_context: 1024` 와 코드가 맞고 **주석이 틀렸다.**

### 2-2. 왜 이 모델인가

| 이유 | 설명 |
|---|---|
| **로컬 우선** | 약관 원문·OCR 결과·검색 결과를 외부로 보내지 않는다(§6 참조) |
| **API 키 없이 실행** | 심사자가 키 없이 그대로 돌릴 수 있어야 한다 |
| **OpenAI 호환 gateway** | 모델 교체가 `.env` 한 줄 |
| **12GB 안에서 상주 가능** | 공식 Q4 텍스트 가중치 5.15GB — §6 예산표 |

### 2-3. ★생성 LLM 이 **하지 않는 일**

이 프로젝트에서 생성 모델은 **보장 여부를 결정하지 않는다.**

```
검색·게이트가 근거 조항을 확정 → 그 근거만 프롬프트에 넣음 → LLM 은 설명만 생성
근거 0건 → ★LLM 을 아예 호출하지 않고 기권
```

판정은 규칙엔진(`rules-2026.08.02`)과 인용 게이트가 한다. LLM 은 **읽기 쉽게 옮겨 적는 역할**이다.
이 분리 덕분에 LLM 이 바뀌어도 판정이 바뀌지 않는다.

### 2-4. 선택형 외부 모델

```env
LLM_PROVIDER=openai   OPENAI_MODEL=gpt-4o-mini    OPENAI_API_KEY=…
LLM_PROVIDER=gemini   GEMINI_MODEL=gemini-2.5-flash  GOOGLE_API_KEY=…
```

기본값이 아니다. 도구호출(tool call) 검증이 필요한 에이전트 경로에서만 고려한다 —
로컬 Gemma 프로필은 `tool_call_verified: false` 이기 때문이다.

### 2-5. ★검증되지 않은 것 (숨기지 않는다)

| 항목 | 상태 | 영향 |
|---|---|---|
| GGUF `artifact_sha256` | **미측정** | 배포본이 평가본과 같은 파일인지 증명 못 함 |
| `memory_peak_mb` | **미측정** | 12GB 예산표(§6)는 공개 스펙 기반 추정이다 |
| `verified_at` (라이브 품질 승인) | **null** | "이 모델로 답변 품질이 좋다"를 주장할 수 없다 |
| `tool_call_verified` | **false** | 도구형 에이전트에는 외부 모델이 필요하다 |
| 최대 context | **1024 까지만 시험** | 긴 조항은 잘릴 수 있다 |

---

## 3. 임베딩 — Arctic-ko

### 3-1. 배포 설정

```
model            dragonkue/snowflake-arctic-embed-l-v2.0-ko
dim              1024
max_seq_length   8192
chunk_budget     448    overlap 80
정밀도           float16
```

운영 인덱스 조각 **122,772** (`config/accepted_extraction.json` → `embed_profile`).

### 3-2. 어떻게 골랐나 — 남의 벤치마크를 쓰지 않았다

★**질의를 지어내지 않았다.** 우리 약관 코퍼스에서 뽑은 실제 문장을 질의로 썼다.

- **측정 21회** — fp16 17회 + 4bit 4회
- 질의: 제목 검색 145문항 · **뒷부분 검색 60문항**(다만·단·그러나…) · 면책 표현 16문항
- 짝비교 + **부트스트랩 2,000회 95% 신뢰구간**

**fp16 17회 상위 결과** (전체 표는 [브리핑 §3](../handoff/13_임베딩모델_선정_브리핑.md))

| 모델 | 차원 | 최대 | MRR | R@10 | 뒷MRR | 면책MRR | 잘림 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `Snowflake/snowflake-arctic-embed-l-v2.0` | 1024 | 8192 | 0.557 | 0.697 | 0.290 | 0.288 | 0.0% |
| `dragonkue/BGE-m3-ko` | 1024 | 8192 | 0.556 | 0.697 | 0.271 | 0.174 | 0.0% |
| ★`dragonkue/snowflake-arctic-embed-l-v2.0-ko` | 1024 | 8192 | 0.549 | **0.703** | **0.317** | **0.341** | 0.0% |
| `nlpai-lab/KURE-v1` | 1024 | 8192 | 0.549 | 0.724 | 0.276 | 0.258 | 0.0% |
| `intfloat/multilingual-e5-large` | 1024 | **512** | 0.542 | 0.703 | 0.187 | 0.246 | **27.2%** |
| `jhgan/ko-sroberta-multitask` (옛 배포 설정) | 768 | **128** | 0.434 | 0.607 | **0.133** | 0.077 | **83.7%** |

### 3-3. ★결정적이었던 것은 순위표가 아니라 **잘림**

옛 배포 설정 `ko-sroberta @ 128토큰` 은 **조항의 83.7% 가 잘렸다.**
그리고 우리가 실제로 검색해야 하는 문장은 **조항 뒤쪽**에 있다 —
*"다만 … 경우에는 보상하지 않습니다"* 가 면책의 실체다.

```
뒷부분 검색 MRR    ko-sroberta@128  0.133
                   Arctic-ko@8192   0.317   (2.4배)
면책 표현 MRR      ko-sroberta@128  0.077
                   Arctic-ko@8192   0.341   (4.4배)
```

### 3-4. ★순위표를 그대로 읽으면 안 된다 — 정직하게 남긴 것

짝비교 결과, 상위 그룹은 **95% 신뢰구간이 0 을 걸친다.**

```
[title] 기준: Snowflake/snowflake-arctic-embed-l-v2.0 (질의 145 · 부트스트랩 2000회)
dragonkue/BGE-m3-ko                            -0.001 [-0.053, +0.051]  구간이 0 을 걸침
dragonkue/snowflake-arctic-embed-l-v2.0-ko     -0.007 [-0.046, +0.031]  구간이 0 을 걸침
nlpai-lab/KURE-v1                              -0.008 [-0.057, +0.045]  구간이 0 을 걸침
…
upskyy/kf-deberta-multitask                    -0.073 [-0.139, -0.008]  ★차이 확인
jhgan/ko-sroberta-multitask (max_seq=512)      -0.130 [-0.194, -0.068]  ★차이 확인
```

★**구간이 0 을 걸치면 "차이를 확인하지 못한 것"이지 "같다"가 아니다.**
그리고 기준 대비 16개를 비교했으므로 다중비교 보정 없이 단일 비교로 읽으면 안 된다.

→ 그래서 브리핑 §3-3 은 **"1위 확정"이 아니라 "예비 후보를 9개로 줄이는 단계"** 로 적혀 있다.
아직 남은 것: s6 코퍼스 재평가 · 구어체 질의 · holdout · 비열등성 δ.

### 3-5. 그런데 왜 지금 이걸 배포했나

시간 제약 때문이다. **재검증 없이 임의로 바꾸지 않는 것**이 더 안전하다고 판단했다 —
임베딩 모델을 바꾸면 전량 재색인(6시간)이고, 그 사이 두 벡터 공간이 섞이면 검색이 조용히 망가진다.
그래서 `embed_model` 을 기본키에 넣어 **섞이지 않게** 해 두고, 현재 모델을 유지했다(→ [05A §2-1](05A_DB_스키마.md)).

---

## 4. 리랭커 — 5종 실측 후 Qwen3-Reranker-4B

### 4-1. 평가 입력 (2026-08-04 · S6 delivery)

```
1차 검색      Arctic-ko dense top20 (같은 문서 안)
원본 청크     145,220
평가 질의     417  (gold 가 top20 에 있는 retrievable 314)
pair          8,273
입력 SHA-256  f9338edaae9a5d7ac8579888aa7ab5f45db4f6bd7926bce94261867025f1ad2f
GPU           NVIDIA RTX 4000 Ada Generation
```

### 4-2. 실측 — retrievable 314문항 기준

| 모델 | Hit@1 | MRR@10 | **대체표현 Hit@1** | 처리량(pair/s) | top20 환산 | 판정 |
|---|---:|---:|---:|---:|---:|---|
| **`Qwen/Qwen3-Reranker-4B`** | **84.71%** | **0.9104** | **73.47%** | 11.36 | 1.76초 | ★정확도 우선 승자 |
| `BAAI/bge-reranker-v2-m3` | 78.03% | 0.8562 | 50.00% | 40.69 | 0.49초 | 저지연 폴백 |
| `Alibaba-NLP/gte-multilingual-reranker-base` | 71.02% | 0.8040 | 45.92% | 47.27 | 0.42초 | 탈락 |
| `Qwen/Qwen3-Reranker-0.6B` | 71.02% | 0.8021 | 33.67% | 7.79 | 2.57초 | ★탈락 — §4-4 |
| `jinaai/jina-reranker-v2-base-multilingual` | 66.56% | 0.7777 | 33.67% | **356.56** | 0.06초 | 초저지연, 품질 탈락 |
| *dense Arctic-ko 기준선* | *64.65%* | *0.7684* | *17.35%* | — | — | 기준선 |

**paired bootstrap 10,000회 (seed `20260804`) — 4B vs BGE**

```
전체 Hit@1     +6.69%p   95% CI [+1.59, +11.78]
전체 MRR       +0.0542   95% CI [+0.0218, +0.0869]
대체표현 Hit@1 +23.47%p  95% CI [+11.22, +35.71]   ← ★여기서 갈렸다
```

★**「대체표현」이 결정적이었다.** 사용자는 약관 문구 그대로 묻지 않는다.
*"도수치료 되나요"* 라고 묻고 약관은 *"의사의 지시 없는 물리요법"* 이라 적혀 있다.
그 간극에서 4B 가 BGE 보다 23%p 앞섰다.

### 4-3. 두 지표를 **함께** 보고한다

```
retrievable 314문항   Hit@1 84.71%   MRR 0.9104   ← 리랭커의 재배치 능력
전체 417문항          Hit@1 63.79%   MRR 0.6855   ← 후보 생성·버전 불일치까지 포함한 종단
```

★**하나만 단독으로 보고하지 않는다.** 전체가 낮아지는 주원인은 리랭커가 복구할 수 없는
gold 결측(93건은 S5 평가 ID ↔ S6 delivery 불일치)이고, 이걸 리랭커 실패와 섞으면 진단이 틀린다.

### 4-4. ★책상 위 권고가 실측에 뒤집혔다 — 남길 가치가 있는 기록

2026-08-02 SOTA 조사(코덱스 합의)는 12GB 예산에 맞춰 **`Qwen3-Reranker-0.6B`** 를 권고했고
*"4B 급은 근거 없이 올리지 않는다"* 고 적었다. **그 판단은 옳은 절차였다** — 크기로 품질을 주장하면 안 된다.

그런데 2026-08-04 실측에서 0.6B 는 **Hit@1 71.02% · 대체표현 33.67%** 로 4B(84.71% · 73.47%)에
크게 밀렸고, **처리량마저 더 느렸다**(7.79 vs 11.36 pair/s).

→ 결론을 바꿨다. 다만 **4B 는 12GB 전부 상주 예산에 안 들어간다**(§6).
그래서 **오프라인 release 로만 쓰고 실시간은 꺼 둔다.**

```env
RAG_RERANK_ENABLED=false            # ★기본 OFF
RERANKER_PROVIDER=cross_encoder
RERANKER_MODEL=Qwen/Qwen3-Reranker-4B
RERANKER_FALLBACK_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_DTYPE=float16
RERANKER_BATCH_SIZE=1
RERANKER_MAX_LENGTH=768
RERANKER_OVER_FETCH=20
RERANKER_TRUST_REMOTE_CODE=false
```

★**"선정·검증된 리랭커"와 "항상 켜진 실시간 리랭커"는 다른 말이다.** 발표에서 섞지 않는다.

### 4-5. 무효 결과도 기록한다 — `Querit/Querit-4B`

- 로드 로그: `score.weight` 가 체크포인트에서 초기화되지 않고 **무작위 초기화**됐다는 경고
- 8,273개 점수가 **전부 `0.76025390625`** — dense 순서를 한 건도 바꾸지 못함
- 판정: **모델 품질이 낮다고 결론 내리지 않고** `checkpoint/scoring-adapter incompatibility` 로 분류

★상수 점수를 "성능 나쁨"으로 적었으면 그 모델에 대한 거짓 기록이 남았을 것이다.
그래서 점수 게이트(membership · finite · **non-constant**)를 평가 코드에 넣었다.

### 4-6. 고정 revision

| 모델 | revision |
|---|---|
| `Qwen/Qwen3-Reranker-4B` | `22e683669bc0f0bd69640a1354a6d0aebcfeede5` |
| `BAAI/bge-reranker-v2-m3` | `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` |
| `Alibaba-NLP/gte-multilingual-reranker-base` | `8215cf04918ba6f7b6a62bb44238ce2953d8831c` |
| `Qwen/Qwen3-Reranker-0.6B` | `e61197ed45024b0ed8a2d74b80b4d909f1255473` |
| `jinaai/jina-reranker-v2-base-multilingual` | `9cfeff2df7d40d1b78e75e5e9cebec92a99813c9` |
| `dragonkue/snowflake-arctic-embed-l-v2.0-ko` | (`accepted_extraction.json` 의 `embed_profile.revision` — ★**비어 있다. 고정 필요**) |

---

## 5. OCR / VLM — ★답변 LLM 이 아니다

자기부담금처럼 **행·열 관계가 의미를 결정하는 표**만 선별해 OCR 에 보낸다.
전체 문서를 무조건 OCR 하지 않는다.

```
표 후보 탐지 → 어려운 페이지만 선별 → OCR·유형별 추출 → ★사람 승인 → 인덱스 편입
                                                   └ 미승인은 shadow 로 보존·차단
```

**실적 (S7.1)**

| | 값 |
|---|---:|
| 사람 검수 패턴 | 29 |
| 승인(`approve`) | 24패턴 · **850 facts** |
| 수정 필요(`fix`) | 5패턴 · 216 facts → **격리** |
| 승인 facts → 청크 | 75 (원문 표기 차이 보존) |
| 승인 facts → occurrence | 850 · 179문서 |
| 미승인 shadow facts | **8,622** (B8 8,586 + F4 36) |
| **미승인의 DB occurrence** | **0** ← 차단 확인 |

★OCR 산출물을 **판정 근거로 쓰려면 사람 승인이 필요하다.** 승인 전에는 파일로 보존만 하고
DB·serving·citation 어디에도 넣지 않는다.

---

## 6. ★VRAM 예산 — RTX 4070 SUPER 12GB 기준

GPU 상자: `Ryzen 5 8600G 12스레드 · RTX 4070 SUPER 12GB`.

### 6-1. 전부 상주 조합 (권고)

| 모델 | 가중치 | 실행 중 |
|---|---:|---:|
| PaddleOCR-VL-1.6 BF16 | 1.79 GiB | 2.3~2.8 |
| korean PP-OCRv5 mobile rec | 0.014 | 0.05~0.1 |
| multilingual-e5-large BF16 | ~1.05 | 1.25~1.45 |
| Qwen3-Reranker-0.6B BF16 | 1.11 | 1.3~1.55 |
| Gemma-4-E4B Q4 (**text only**) | 4.80 | 5.4~5.8 |
| **합** | **~8.8 GiB** | **피크 10.5~11.6 GiB** |

조건: 생성 4K · 리랭크 1K · 배치 1~4 · 동시 요청 1 · **Gemma 멀티모달 projector 미로드**.

> ★초판이 *"Gemma Q4 ~4.5GB"* 라고 적었는데 부족한 수치였다. 공식 Q4 GGUF 는
> **텍스트 가중치만 5.15GB**, 멀티모달 projector 가 **추가 992MB** 다.
> 생성에는 projector 를 로드하지 않아야 한다.

### 6-2. 그래서 4B 리랭커는 왜 실시간이 아닌가

위 표는 리랭커가 **0.6B** 일 때의 예산이다. 실측에서 이긴 **4B 로 바꾸면 전부 상주가 깨진다.**
→ 4B 는 별도 GPU(RTX 4000 Ada)에서 **오프라인 배치**로 돌려 release 를 만들고,
실시간 경로는 `RAG_RERANK_ENABLED=false` 로 둔다. GPU serving·동시성·OOM 시험 뒤에 켠다.

### 6-3. 하드웨어를 늘리면?

| | 열리는 것 | 실익 |
|---|---|---|
| **16GB** | `Qwen3.5-4B BF16` 전부 상주가 간신히(피크 14.5~15.8 GiB) | **작다.** 로딩 지연이 줄 뿐 — 정확도·근거성·기권 정책은 개선되지 않는다 |
| **32GB** | Gemma BF16 · 4B 리랭커 동시 상주 · 12B Q4 | **제한적.** 4B 리랭커가 일반 벤치에서 앞서지만 재색인·임계값 재보정·지연·테스트 범위는 확실히 증가 |

★**하드웨어를 늘려도 이 제품의 실패 모드(잘못된 보장 판정)는 줄지 않는다.**

### 6-4. ★RunPod 은 연산 확장 자원이 **아니다**

| 올려도 되는 것 | 절대 안 되는 것 |
|---|---|
| 공개 모델 가중치·토크나이저 | 실제 진료비 내역서·처방전 |
| 애플리케이션 코드·양자화 스크립트 | **OCR 결과 · 문서 청크 · 프롬프트 · 검색 결과 · 생성 로그** |
| **완전 합성** 문서 | 의료문서로 만든 **임베딩·벡터DB·캐시·LoRA 체크포인트** |
| 공개 벤치마크 | **약관 원문·스캔·OCR 텍스트** |

★암호화 전송·저장을 해도 **GPU 메모리에서는 평문으로 처리된다.**
→ RunPod 16/32GB 는 **공개·합성 자료 기반 개발·비교·부하시험 전용**이다.

---

## 7. 추론 파라미터 (현재 값)

| 슬롯 | 파라미터 | 값 | 출처 |
|---|---|---|---|
| 생성 | `n_ctx` | 1024 | `scripts/local_model_server.py:62` |
| 생성 | `quantization` | Q4_0 (QAT) | `model_registry.yaml` |
| 생성 | 요청 타임아웃 | 120초 / health 3초 | `.env.example` |
| 임베딩 | `dim` / `max_seq_length` | 1024 / 8192 | `config/accepted_extraction.json` |
| 임베딩 | `chunk_budget` / `overlap` | 448 / 80 | 같은 곳 |
| 검색 | HNSW 연산자 | `vector_l2_ops` | `pgvector_clause_index.py:540` |
| 리랭커 | dtype / batch / max_length | fp16 / 1 / 768 | `.env.example` |
| 리랭커 | over-fetch | top20 | 같은 곳 |

★`temperature` · `top_p` · `max_tokens` 는 서버가 **요청에서 그대로 전달**한다
(`local_model_server.py:106`) — 코드에 고정값을 박지 않았다.

---

## 8. 성능 요약 (측정일·범위를 붙여서)

| 지표 | 값 | 범위 | 측정일 |
|---|---:|---|---|
| retrievable Hit@1 | **84.71%** | 314문항 · Qwen3-4B 리랭크 후 | 2026-08-04 |
| retrievable MRR@10 | **0.9101** | 같음 | 2026-08-04 |
| 전체 Hit@1 | 63.79% | 417문항 · 종단 | 2026-08-04 |
| 기존 gold rank 회귀 | **0건** | S7 → S7.1 | 2026-08-04 |
| pgvector top20 p50 / p95 | **323ms / 364ms** | warm · 승인 OCR 벡터 | 2026-08-04 |
| 리랭크 처리량 | 11.689 pair/s | RTX 4000 Ada · 8,285 pair | 2026-08-04 |

---

## 9. ★이 문서가 주장하지 **않는** 것

1. **"Arctic-ko 가 최고다"** — 상위 그룹은 신뢰구간이 겹친다. **예비 후보 축소**까지다(§3-4).
2. **"Gemma 로 답변 품질이 좋다"** — 라이브 품질 승인이 없다. `verified_at: null` 이다(§2-5).
3. **"리랭커가 켜져 있다"** — 기본 OFF 다. 오프라인 release 만 있다(§4-4).
4. **"OCR facts 로 정확도가 올랐다"** — 측정한 것은 **기존 검색 비회귀**다. 신규 facts 전용 holdout 은 없다.
5. **"12GB 예산표가 실측이다"** — 공개 스펙 기반 **추정**이다. `memory_peak_mb` 는 아직 `null` 이다.

---

## 참조

- ★**발표용 그림**: [`docs/delivery/presentation_visuals.html`](../delivery/presentation_visuals.html)
  — 그림 3(리랭커 5종 막대) · 그림 4(임베딩 잘림)가 이 문서 §3·§4 를 그림으로 옮긴 것이다
- 임베딩 선정 브리핑(측정 21회): [`docs/handoff/13_임베딩모델_선정_브리핑.md`](../handoff/13_임베딩모델_선정_브리핑.md)
- 리랭커 5종 실측: [`docs/reports/2026-08-04_S6_Arctic-ko_리랭커5종_실측과_운영반영.md`](../reports/2026-08-04_S6_Arctic-ko_리랭커5종_실측과_운영반영.md)
- S7.1 승격·비회귀·지연: [`docs/reports/2026-08-04_S7.1_OCR승격_최종결과.md`](../reports/2026-08-04_S7.1_OCR승격_최종결과.md)
- SOTA 조사·12GB 예산(코덱스 합의): [`docs/reports/2026-08-02_1500_SOTA_모델_조사_코덱스합의.md`](../reports/2026-08-02_1500_SOTA_모델_조사_코덱스합의.md)
- 레지스트리: `model_registry.yaml` · `app/core/model_registry.py`
- 게이트웨이: `app/adapters/llm_gateway.py` · `app/core/llm_clients.py`
