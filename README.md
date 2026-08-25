# embedding — 임베딩·색인 자료만 모은 브랜치

`develop`(483파일)에서 **임베딩·색인에 직접 관련된 26개만** 남겼다.

> ★**이 브랜치는 돌아가는 앱이 아니다.** 그대로는 import 되지 않는다 —
> `app/core/config.py`·`app/core/errors.py` 같은 의존을 **일부러** 뺐다.
> 임베딩 작업 자료를 한자리에 놓고 보기 위한 갈래다.
>
> ★★**`develop` 에 그대로 머지하지 말 것.** 머지하면 여기 없는 **457개 파일이 삭제된다.**
> 올릴 것이 생기면 그 파일만 `cherry-pick` 하거나 경로를 지정해 옮긴다.

---

## 들어 있는 것

| 갈래 | 파일 |
|---|---|
| **임베더** | `app/adapters/clause_query_embedder.py` — 질의 인코딩<br>`app/adapters/clause_document_embedder.py` — 문서 인코딩 |
| **색인 어댑터** | `app/adapters/pgvector_clause_index.py` · `pgvector_index.py`<br>`db/postgres/pgvector_clause_index.py` · `pgvector_index.py` |
| **적재·인코딩** | `scripts/index/` — `s7_arctic_embed`(GPU 임베딩) · `shard_embed`(샤딩) · `gpu_embed`<br>`load_precomputed` · `load_s7_1_approved_facts`(승인분 증분) · `build_clause_index`<br>`sync_embed_profile`(프로필 동기화) · `backfill_embed_revision`(라벨 보정) |
| **스키마** | `db/migrations/postgres/003_embedding.sql` — HNSW · GIN trigram |
| **승인 프로필** | `config/accepted_extraction.json` — 모델 · revision · `query_prefix` · 차원 |
| **의존성** | `requirements/index.txt` |
| **시험** | `tests/test_clause_document_embedder.py` · `test_s7_arctic_embed.py`<br>`test_bench_embedders_cli.py` · `test_clause_index.py` · `test_pgvector.py` |
| **모델 비교** | `scripts/eval/bench_embedders.py` |

## 일부러 뺀 것

- **리랭커** (`clause_rerank.py` · `reranker.py` · `clause_search.py` · 관련 시험)
  — 임베딩 **다음** 단계다. 갈래가 다르므로 섞지 않았다.
- **약관 원문과 벡터 데이터** — 저작물이고 용량이 크다.
  `.gitignore` 를 함께 넣어 그 규칙이 이 브랜치에도 따라오게 했다.

---

## ★ 이 갈래에서 가장 중요한 사실 — `query_prefix`

색인은 조항 본문을 **접두사 없이**, 질의는 **`"query: "` 를 붙여** 인코딩했다.

서비스가 그걸 모르고 맨 질의를 인코딩하면 **오류도 안 나고 로그도 안 남긴 채
틀린 조항이 올라온다.** 근거를 대는 서비스에서 이건 가장 나쁜 실패다.

그래서 접두사를 코드에 박지 않고 이렇게 다룬다.

```
data/work/s7_arctic_embed5/manifest.json   ← 적재가 실제로 쓴 값(진실의 출처)
        │  scripts/index/sync_embed_profile.py 가 겹치는 항목을 대조하고
        ▼  다르면 아무것도 쓰지 않고 멈춘다
config/accepted_extraction.json            ← 승인 프로필(서비스가 읽는 곳)
        │
        ▼  app/adapters/clause_query_embedder.py
질의 인코딩 — query_prefix 가 없으면 기본값으로 때우지 않고 **예외로 멈춘다**
```

### 프로필 항목

```json
{
  "model": "dragonkue/snowflake-arctic-embed-l-v2.0-ko",
  "revision": "55ec6e9358a56d56af759bc8372e970caf8c305f",
  "dim": 1024, "max_seq_length": 8192,
  "chunk_budget": 448, "overlap": 80,
  "query_prefix": "query: ", "doc_prefix": "", "normalized": true
}
```

★`revision` 은 **색인 게이트 키의 일부**다(`model|revision|d…|L…|c…|o…`).
비워 두면 「어느 무게추로 만든 벡터인지」를 알 수 없고,
반대로 프로필에만 채우고 DB 라벨을 안 고치면 **키가 어긋나 검색이 전량 막힌다**.
실제로 그 일이 있었고, `scripts/index/backfill_embed_revision.py` 가 그때 만든 도구다
(라벨만 고치고 벡터는 건드리지 않으며, `--revert` 로 되돌아간다).

---

## 자주 쓰는 순서

```bash
# 1. 승인 프로필이 적재 매니페스트와 맞는지 확인하고 채운다
python -m scripts.index.sync_embed_profile --dry-run
python -m scripts.index.sync_embed_profile

# 2. DB 라벨에 revision 이 빠져 있으면 채운다(되돌릴 수 있다)
python -m scripts.index.backfill_embed_revision --dry-run

# 3. 임베딩 모델 비교
python -m scripts.eval.bench_embedders --help
```

---

## 색인의 지금 모양 (2026-08-05 실측)

| | |
|---|---|
| 조각 | **122,772** · `vector(1024)` |
| HNSW | **753 MB** · `vector_l2_ops` |
| GIN trigram | **255 MB** · `gin_trgm_ops` |
| 세대 | `s6` 210,733 발생 · `s5-mixed` 158,186 (게이트로 가림) |

★조각·발생·내용은 **서로 다른 단위**다. 한 퍼널로 이으면 「몇 % 적재됨」이 잘못 계산된다.
