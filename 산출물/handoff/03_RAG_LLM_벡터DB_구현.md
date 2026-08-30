# 개발 소프트웨어 보고서 — RAG LLM·벡터DB 연동

## 1. 구현 결과

FastAPI 요청이 승인된 보험 약관 릴리스만 검색하고, PostgreSQL의 dense·lexical 후보를 결합한 뒤 부모 조항과 출처를 복원해 LLM에 제공하는 RAG 경로를 구현했다. 근거가 없거나 판본이 불명확하면 LLM을 호출해 답을 지어내지 않고 기권한다.

## 2. 코드 구성

| 기능 | 구현 파일 |
|---|---|
| RAG 유스케이스와 근거 답변 | `app/application/answer_question.py`, `app/rag/service.py` |
| 보험 사전판정 | `app/core/usecases/precheck.py`, `app/routers/precheck.py` |
| 문서·세대 식별 | `app/adapters/manifest_policy_resolver.py`, `app/core/domain/generation.py` |
| KCD 범위 처리 | `app/core/domain/kcd_ranges.py` |
| 인용·신선도 게이트 | `app/core/domain/citation_guard.py`, `app/adapters/freshness_gate.py` |
| pgvector 인덱스 | `app/adapters/pgvector_clause_index.py` |
| 약관 검색 저장소 | `app/adapters/pg_clause_store.py` |
| dense retrieval | `app/adapters/pgvector_retriever.py` |
| lexical retrieval | `app/adapters/pg_lexical_retriever.py` |
| RRF 결합 | `app/adapters/hybrid_retriever.py`, `fusion_retriever.py` |
| 리랭커 | `app/adapters/reranker.py`, `clause_rerank.py` |
| LLM gateway | `app/adapters/llm_gateway.py`, `app/core/llm_clients.py` |
| 모델 registry | `model_registry.yaml`, `app/core/model_registry.py` |
| 인덱스 생성·적재 | `scripts/index/build_clause_index.py`, `load_precomputed.py` |
| S7.1 증분 적재 | `scripts/index/load_s7_1_approved_facts.py` |
| API 준비상태 | `app/obs/readiness.py`, `app/routers/health.py` |

## 3. 검색 파이프라인

```text
질문 + 보험 문맥
  → 활성 release·문서 SHA·세대 필터
  → Arctic-ko pgvector HNSW top-N
  + pg_trgm lexical top-N
  → Reciprocal Rank Fusion
  → Qwen3-Reranker-4B top-k 재정렬(설정 시)
  → content_hash로 부모 조항 복원
  → occurrence에서 보험사·문서·쪽·조항 locator 선택
  → citation guard
  → Gemma에 근거만 전달
  → 답변 + citations 또는 abstention
```

### 벡터DB 핵심 설계

`policy_clause_chunk`는 `(content_hash, chunk_ix, embed_model)`을 기본키로 사용한다. 같은 본문을 여러 임베딩 모델로 계산해도 덮어쓰거나 다른 벡터 공간을 섞지 않는다. HNSW는 `vector_l2_ops`를 사용한다.

`policy_clause_occurrence`는 문서 SHA와 쪽, 조항, `index_generation`, `citation_eligible`, `source_kind`를 저장한다. 검색용 조각과 인용 위치를 분리해 중복 본문을 한 번 임베딩하면서도 각 문서의 정확한 출처를 반환한다.

## 4. LLM 연동

현재 기본 생성 설정은 다음과 같다.

```env
LLM_PROVIDER=local
LOCAL_BASE_URL=http://127.0.0.1:8002/v1
LOCAL_MODEL=gemma-4-e4b
```

`scripts/local_model_server.py`가 `google/gemma-4-E4B-it-qat-q4_0-gguf`의 `gemma-4-E4B_q4_0-it.gguf`를 llama-cpp-python으로 열고 OpenAI 호환 `/v1` 인터페이스를 제공한다. 애플리케이션은 구체 SDK가 아니라 gateway를 호출하므로 OpenAI와 Gemini 구성으로 교체할 수 있다.

실행 스크립트는 `LLM_PROVIDER`를 강제로 `local`로 덮어쓰지 않는다. `.env`에서 `local`, `openai`, `gemini` 중 하나를 명시하고, 고객 용어 챗봇은 승인 약관 인용이 있을 때 선택된 provider를 실제 호출해 쉬운 설명을 만든다. 판정 질문은 계속 사전판정 양식으로 보내며 LLM이 보장 여부를 직접 결정하지 않는다.

모델 ID는 애플리케이션 코드에 흩어 놓지 않고 `model_registry.yaml`과 환경설정에 둔다. 기본 프로필 `local_gemma4_e4b`는 revision `bb3b92e6f031fa438b409f898dd9f14f499a0cb0`, Q4_0, 최대 검증 context 1024로 선언돼 있다. 다만 GGUF artifact SHA와 live verification이 아직 `null`이므로 배포 승인 완료 모델로 과장하지 않는다.

## 5. 임베딩과 리랭커

### 임베딩

- 모델: `dragonkue/snowflake-arctic-embed-l-v2.0-ko`
- 차원: 1,024
- 최대 시퀀스 설정: 8,192
- 청크 budget/overlap: 448/80
- S7 전량 산출: 145,220행 float16, 5 shards
- 운영 인덱스 Arctic-ko 청크: 122,772

S6 모델 비교 결과의 1위였고, 시간 제약상 S7에서 같은 모델을 재검증 없이 임의 교체하지 않고 승인 릴리스 전체에 적용했다.

### 리랭커

- 선택 모델: `Qwen/Qwen3-Reranker-4B`
- S7.1 입력: 417질의, 8,285 query-passage pair
- GPU: NVIDIA RTX 4000 Ada Generation
- 추론: 708.806초, 11.689 pair/s
- 점수 게이트: membership, finite, non-constant, locator 통과

리랭크 평가와 release 생성은 완료했지만 기본 API는 `RAG_RERANK_ENABLED=false`다. 즉 “선정·검증된 리랭커”와 “항상 켜진 실시간 리랭커”를 구분해야 한다.

## 6. 운영 데이터 연결

`config/accepted_extraction.json`이 활성 조항 릴리스와 두 입력을 연결한다.

- `supplemental_facts`: 승인된 S7.1 OCR facts. DB에 적재돼 실제 검색에 참여한다.
- `candidate_fact_registry`: B8/F4 shadow facts. 경로와 해시는 검증하지만 검색·인용에서는 제외한다.

승인 facts 850건은 75개 검색 청크와 850 occurrence로 변환돼 179문서에 연결됐다. 미승인 8,622 facts의 DB occurrence는 0건이다.

## 7. 실행 방법

```bash
pip install -r requirements.txt
cp .env.example .env

python -m scripts.manage migrate
python -m scripts.pg
python -m scripts.index.build_clause_index

# 필요한 경우 S7.1 승인 facts 증분 적재
python -m scripts.index.load_s7_1_approved_facts

# 로컬 생성 모델
python scripts/download_gemma.py      # 최초 1회
python scripts/local_model_server.py  # 8002

# 또는 .env에서 LLM_PROVIDER=openai/gemini와 해당 API 키 설정
python scripts/llm_smoke.py
python scripts/mcp_smoke.py

# 애플리케이션
python -m scripts.run_customer_server
python -m scripts.run_admin_server
```

준비상태는 `GET http://127.0.0.1:8080/api/health/ready`에서 확인한다. 인덱스나 승인 설정이 없으면 자동 폴백하지 않고 `ready=false`로 반환한다.

## 8. 성능과 품질

| 지표 | 결과 |
|---|---:|
| 평가 질의 | 417 |
| 전체 Hit@1 | 63.79% |
| retrievable Hit@1 | 84.71% |
| retrievable MRR@10 | 0.9101 |
| 승인 OCR 유입 | 23 pair, 6질의 |
| 기존 gold rank 회귀 | 0건 |
| pgvector warm top20 p50 | 323ms |
| pgvector warm top20 p95 | 364ms |

검색 SQL은 HNSW 근접 후보를 먼저 제한하고 그 뒤 본문 중복을 제거하도록 개선했다. 이전 p50 5,420ms에서 323ms로 약 16.8배 단축했으며 고정 질의 top20 순위와 거리는 20/20 동일했다.

## 9. 무폴백 동작

- 근거 0건: 생성 모델을 호출하지 않고 abstention.
- 문서 판본 불명: 임의 상품을 고르지 않고 후보를 제시해 되묻기.
- 인용 locator 누락: 해당 근거를 citation에서 제외.
- 모델·DB 장애: 정의된 오류 타입과 5xx, readiness 실패.
- 미승인 OCR facts: shadow 상태 유지, DB·serving·citation 차단.

## 10. 제출·인계 범위

Git 저장소에는 구현 코드, 설정 예시, schema 생성 코드와 테스트를 포함한다. 약관 원문, 모델 가중치, 대용량 벡터 산출물은 별도 artifact로 전달하고 `config` manifest의 해시로 대조한다.
