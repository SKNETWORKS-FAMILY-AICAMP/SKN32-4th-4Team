-- 003_embedding.sql — 확장 + 임베딩 컬럼·ANN 인덱스
--
-- ★001 에서 분리한 이유: 임베딩 모델이 바뀌면 **전량 재구축**이다.
--   차원이 스키마에 박히므로 모델 확정 전에는 컬럼을 만들지 않는다.
--
-- ★차원 미확정. 2라운드 결과 arctic-l-v2(1024) 가 1위이나 e5-large(1024) 와
--   44문항 기준 3문항 차이로 오차범위 언저리다. 확정되면 아래 주석을 풀고 값을 넣는다.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 낱말 검색(현행 pg_trgm 경로와 같은 방식)
CREATE INDEX clause_chunk_text_trgm
    ON core.clause_chunk USING gin (text gin_trgm_ops);

-- ── 모델 확정 후 실행 ───────────────────────────────────────────
-- ALTER TABLE core.clause_chunk ADD COLUMN embedding vector(1024);
-- ALTER TABLE core.clause_chunk ADD COLUMN embed_model text;
-- CREATE INDEX clause_chunk_embedding_hnsw
--     ON core.clause_chunk USING hnsw (embedding vector_cosine_ops);
