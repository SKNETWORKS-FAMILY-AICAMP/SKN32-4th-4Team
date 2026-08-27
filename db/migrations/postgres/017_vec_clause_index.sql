-- 017 · 조항 벡터 색인을 `insurance_real` 안으로 (스키마 `vec`)
--
-- ★왜 여기로 오나
--   이 색인은 그동안 **다른 DB(`mall_vec`)** 에 있었다. 그 이름은 커머스 실습 시절
--   (`_unified_mall`) 잔재이고, 담긴 데이터는 판정·검색이 **지금 쓰는** 현행 색인이다.
--   두 DB 가 갈려 있어서 원장(`core`)과 파생 색인을 **한 트랜잭션으로 못 묶었다.**
--
-- ★왜 `public` 이 아니라 `vec` 인가
--   이 DB 는 `core`/`app`/`ops` 로 역할이 갈려 있고 권한도 스키마 단위로 걸린다.
--   `public` 에 두면 그 규율 밖에 놓인다.
--   ★★**약관 원문 파생물이다.** `core` 와 같은 등급으로 다룬다 —
--     소유자는 `insurance_owner`, 런타임(`insurance_app`)은 **SELECT 만**.
--
-- ★이 파일은 **이미 서 있던 스키마를 그대로 옮겨 적은 것**이다(2026-08-26).
--   컬럼·기본키·인덱스는 `mall_vec.public` 의 실물과 같아야 한다.
--   데이터 이관은 이 DDL 이 아니라 별도 스크립트가 한다.

CREATE SCHEMA IF NOT EXISTS vec AUTHORIZATION insurance_owner;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 중복 없는 부모 조항 원문. 조각을 이어 붙여 복원하지 않는다 —
-- 겹침이 토큰 기준이라 글자 수로 자를 수 없기 때문이다.
CREATE TABLE IF NOT EXISTS vec.policy_clause_content (
    content_hash text    PRIMARY KEY,
    text         text    NOT NULL,
    n_chunks     integer NOT NULL
);

-- 검색 조각 + 모델별 벡터.
-- ★`embed_model` 이 기본키에 있다. 없으면 `ON CONFLICT DO NOTHING` 에서
--   옛 모델 벡터가 자리를 지키고 새 벡터가 **조용히 버려진다.**
CREATE TABLE IF NOT EXISTS vec.policy_clause_chunk (
    content_hash text    NOT NULL,
    chunk_ix     integer NOT NULL,
    n_chunks     integer NOT NULL DEFAULT 0,
    text         text    NOT NULL,
    embedding    vector(1024) NOT NULL,
    embed_model  text    NOT NULL,
    PRIMARY KEY (content_hash, chunk_ix, embed_model)
);

-- 어느 문서 · 몇 쪽 · 인용 가능한가.
-- ★`index_generation` 에 **기본값을 두지 않는다.** 옛 스키마는
--   `DEFAULT 's5-mixed'` 였는데 그 세대는 2026-08-26 에 은퇴해 0행이 됐다.
--   기본값으로 들어간 행은 **아무도 안 읽는다** — 조용히 사라지는 행이 된다.
--   적재 경로는 세대를 명시해 넘기므로, 여기서는 명시를 **강제**한다.
CREATE TABLE IF NOT EXISTS vec.policy_clause_occurrence (
    content_hash      text    NOT NULL,
    sha256            text    NOT NULL,
    insurer           text    NOT NULL DEFAULT '',
    qualified_no      text    NOT NULL DEFAULT '',
    section           text    NOT NULL DEFAULT '',
    title             text    NOT NULL DEFAULT '',
    page_from         integer NOT NULL DEFAULT 0,
    page_to           integer NOT NULL DEFAULT 0,
    citation_eligible boolean,
    chunk_type        text,
    is_statute        boolean,
    parse_status      text,
    ordinal           integer,
    index_generation  text    NOT NULL,
    source_kind       text    NOT NULL DEFAULT 'clause',
    PRIMARY KEY (content_hash, sha256, qualified_no, page_from, index_generation)
);

-- ★★참조 무결성. `mall_vec` 에는 2026-08-26 까지 외래키가 **0개**였고
--   그래서 고아 발생이 45,816행 쌓였다.
-- ★`CASCADE` 가 아니라 `RESTRICT` 다. `CASCADE` 면 본문 한 줄을 지울 때
--   그 벡터가 조용히 함께 사라진다 — 검색 결과가 주는데 아무도 모른다.
-- ★지우는 순서: **자식 먼저, 부모 나중**(조각·발생 → 본문).
ALTER TABLE vec.policy_clause_chunk
    DROP CONSTRAINT IF EXISTS policy_clause_chunk_content_fk;
ALTER TABLE vec.policy_clause_chunk
    ADD CONSTRAINT policy_clause_chunk_content_fk
    FOREIGN KEY (content_hash) REFERENCES vec.policy_clause_content(content_hash)
    ON DELETE RESTRICT;

ALTER TABLE vec.policy_clause_occurrence
    DROP CONSTRAINT IF EXISTS policy_clause_occurrence_content_fk;
ALTER TABLE vec.policy_clause_occurrence
    ADD CONSTRAINT policy_clause_occurrence_content_fk
    FOREIGN KEY (content_hash) REFERENCES vec.policy_clause_content(content_hash)
    ON DELETE RESTRICT;

-- ★인덱스는 **데이터를 넣은 뒤** 만드는 것이 훨씬 빠르다(HNSW 931 MB).
--   그래서 여기서는 만들지 않는다 — 이관 스크립트가 적재 후에 만든다.
--   `018_vec_indexes.sql` 참조.

-- 권한 — `core` 와 같은 등급.
GRANT USAGE ON SCHEMA vec TO insurance_app, insurance_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA vec TO insurance_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA vec GRANT SELECT ON TABLES TO insurance_app;
