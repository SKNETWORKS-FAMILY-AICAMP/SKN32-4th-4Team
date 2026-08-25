-- 001_core.sql — 스키마 3개 + core 12테이블 + ops 2테이블
--
-- 설계: docs/handoff/02_ERD_및_스키마.md (27테이블 중 P0-b 범위)
-- 근거: docs/reports/2026-08-03_DDL_LangGraph_3라운드_교차검증.md
--
-- ★이 파일이 만들지 않는 것과 그 이유
--   app.*        P1~P2. 컬럼·보존정책이 덜 확정됐다
--   ops.consent  `consent.subject_id → app.subject` 인데 subject 가 P2다.
--                설계문서가 P0-b 에 넣어둔 것은 모순이고, 여기서 뺀다
--   임베딩 컬럼·ANN 인덱스   002_embedding.sql. 모델이 바뀌면 전량 재구축이라 분리
--
-- ★enum 을 CREATE TYPE 이 아니라 text + CHECK 로 둔다
--   설계가 아직 움직인다. CHECK 는 ALTER 한 줄로 값이 바뀌지만
--   enum 타입은 값 제거·개명이 어렵다. 확정되면 그때 타입으로 옮긴다.

-- ★BEGIN/COMMIT 을 여기 두지 않는다. apply.py 가 DDL+이력을 한 트랜잭션으로 감싼다.

CREATE SCHEMA core;
CREATE SCHEMA app;
CREATE SCHEMA ops;

COMMENT ON SCHEMA core IS '약관 코퍼스 — 참조 데이터. 앱 롤은 SELECT 만';
COMMENT ON SCHEMA app  IS '케이스·판정·증빙 — ★PII 있음';
COMMENT ON SCHEMA ops  IS '운영·거버넌스 — audit_log 는 append-only';

-- ════════════════════════════════════════════════════════════════
-- ops — core 가 FK 로 참조하므로 먼저 만든다
-- ════════════════════════════════════════════════════════════════

CREATE TABLE ops.admin_user (
    id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    login   text NOT NULL UNIQUE,
    role    text NOT NULL
);
COMMENT ON TABLE ops.admin_user IS
    '관리자. ★승격은 CLI 전용 — UI 버튼을 만들지 않는다';

CREATE TABLE ops.audit_log (
    id            bigserial PRIMARY KEY,
    actor_id      uuid,
    actor_type    text,
    action        text NOT NULL,
    target_table  text,
    target_id     text,
    before        jsonb,
    after         jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_log_target_idx  ON ops.audit_log (target_table, target_id);
CREATE INDEX audit_log_created_idx ON ops.audit_log (created_at);
COMMENT ON TABLE ops.audit_log IS
    '★append-only. 누가 언제 무엇을 verified 로 바꿨나 — 이 서비스에서 가장 중요한 로그. '
    'before/after 에 민감정보가 남을 수 있어 보존기간·redaction 을 첫 행 받기 전에 정해야 한다';

-- ════════════════════════════════════════════════════════════════
-- core — 참조 데이터
-- ════════════════════════════════════════════════════════════════

CREATE TABLE core.insurer (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          text NOT NULL UNIQUE,
    legal_name    text NOT NULL,
    display_name  text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('general','life')),
    active        boolean NOT NULL DEFAULT true
);
COMMENT ON TABLE core.insurer IS
    '12행. ★적재 전 samsunglife → 삼성생명 정규화 필요 — 안 하면 13개사가 된다(실측)';

CREATE TABLE core.confirmed_policy_document (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    sha256               text NOT NULL UNIQUE CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    source_url           text NOT NULL,
    fetched_at           timestamptz NOT NULL,
    http_status          int,
    bytes                bigint CHECK (bytes IS NULL OR bytes >= 0),
    pages                int CHECK (pages IS NULL OR pages >= 0),
    insurer_id           uuid REFERENCES core.insurer(id),
    identified_by        uuid NOT NULL REFERENCES ops.admin_user(id),
    identified_at        timestamptz NOT NULL,
    identification_note  text,
    license              text,
    redistributable      boolean NOT NULL DEFAULT false
);
COMMENT ON TABLE core.confirmed_policy_document IS
    '★status 컬럼이 없다. 행이 존재하는 것이 곧 "확정됨"이다 — enum 은 UPDATE 로 뚫린다. '
    'identified_by NOT NULL 이라 확정에는 반드시 사람 이름이 붙는다';

CREATE TABLE core.document_extraction (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    confirmed_document_id   uuid NOT NULL REFERENCES core.confirmed_policy_document(id),
    extractor               text NOT NULL,
    schema_version          int  NOT NULL,
    parse_status            text NOT NULL CHECK (parse_status IN ('ok','suspect','failed')),
    failure_reason          text,
    approval                text NOT NULL DEFAULT 'candidate'
                                 CHECK (approval IN ('candidate','accepted','rejected')),
    numbering               text CHECK (numbering IN ('article','numbered','none','ambiguous')),
    parse_warnings          jsonb NOT NULL DEFAULT '[]'::jsonb,
    toc_pages               int[] NOT NULL DEFAULT '{}',
    toc_page_count          int  NOT NULL DEFAULT 0 CHECK (toc_page_count >= 0),
    unmapped_glyph_count    int  NOT NULL DEFAULT 0 CHECK (unmapped_glyph_count >= 0),
    unmapped_glyphs         jsonb NOT NULL DEFAULT '{}'::jsonb,
    control_removed_count   int  NOT NULL DEFAULT 0,
    pua_removed_count       int  NOT NULL DEFAULT 0,
    extracted_at            timestamptz NOT NULL,
    UNIQUE (confirmed_document_id, schema_version, extractor),
    UNIQUE (id, confirmed_document_id)
);
-- ★문서당 accepted 는 1건. "가장 큰 sN 폴더 자동 선택" 을 구조로 막는다.
CREATE UNIQUE INDEX document_extraction_one_accepted
    ON core.document_extraction (confirmed_document_id)
    WHERE approval = 'accepted';
COMMENT ON COLUMN core.document_extraction.parse_status IS
    '★기본값 없음. 누락은 fail-closed — ok 로 채우지 않는다';

CREATE TABLE core.product (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    insurer_id    uuid NOT NULL REFERENCES core.insurer(id),
    product_code  text UNIQUE,
    name          text NOT NULL,
    line          text NOT NULL DEFAULT 'unknown'
                       CHECK (line IN ('standard','senior','simplified_issue','travel','unknown'))
);
COMMENT ON COLUMN core.product.product_code IS
    '★여기에 세대를 박지 않는다 — 코드 형식이 값을 강제하게 된다';
COMMENT ON COLUMN core.product.line IS
    '실측값만 둔다. unknown 184건이 실재하므로 멤버로 유지';

CREATE TABLE core.policy_version (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    confirmed_document_id  uuid NOT NULL REFERENCES core.confirmed_policy_document(id),
    product_id             uuid REFERENCES core.product(id),
    version_label          text NOT NULL,
    variant                text CHECK (variant IN
                                ('standard','contract_conversion','conversion_resume','child_conversion')),
    valid_from             date,
    valid_to               date,
    sales_from             date,
    sales_to               date,
    date_confidence        text NOT NULL CHECK (date_confidence IN ('exact','month','unknown')),
    generation             smallint CHECK (generation BETWEEN 1 AND 5),
    generation_source      text,
    generation_confidence  text CHECK (generation_confidence IN ('exact','month','unknown')),
    UNIQUE (product_id, version_label),
    UNIQUE (id, confirmed_document_id),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_from <= valid_to),
    CHECK (sales_to IS NULL OR sales_from IS NULL OR sales_from <= sales_to)
);
COMMENT ON COLUMN core.policy_version.confirmed_document_id IS
    '★NOT NULL. 확정 안 된 문서로는 버전 행을 만들 수 없다. '
    '★UNIQUE 를 걸지 않는다 — 1파일:N상품 156건·1파일:N판매구간 102건(실측)';
COMMENT ON COLUMN core.policy_version.generation IS
    '★NULL 허용. 모르는 세대를 숫자로 채우면 그 오류가 판정까지 간다';
COMMENT ON COLUMN core.policy_version.variant IS
    '★파생 규칙 미정 — 매니페스트에 출처 필드가 없다. 지금은 NULL 로 둔다';

CREATE TABLE core.clause_content (
    content_hash  text PRIMARY KEY CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    hash_version  text NOT NULL,
    title         text NOT NULL DEFAULT '',
    body          text NOT NULL,
    char_length   int  NOT NULL CHECK (char_length >= 0),
    -- ★항(項)은 **내용의 성질**이다. occurrence 에 두면 58만 항이 중복 저장돼
    --   clause_content 의 중복제거(65.4%)를 되돌린다(코덱스 3라운드 지적).
    paragraph_count int NOT NULL DEFAULT 0 CHECK (paragraph_count >= 0),
    paragraphs      jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(paragraphs) = 'array'
               AND jsonb_array_length(paragraphs) = paragraph_count)
);
COMMENT ON TABLE core.clause_content IS
    '내용이 정체성. ★해시에 section·조 번호를 넣지 않는다 — 수록 문맥은 내용이 아니다. '
    '실측 중복률 65.4%';

CREATE TABLE core.policy_clause (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- ★같은 확정문서인지 복합 FK 로 강제한다(코덱스 3라운드 ⑦).
    --   extraction 과 version 이 서로 다른 문서를 가리키면 인용이 엉뚱해진다.
    confirmed_document_id   uuid NOT NULL,
    document_extraction_id  uuid NOT NULL,
    policy_version_id       uuid,
    ordinal                 int  NOT NULL CHECK (ordinal >= 0),
    content_hash            text NOT NULL REFERENCES core.clause_content(content_hash),
    qualified_no            text NOT NULL DEFAULT '',
    section                 text NOT NULL DEFAULT '',
    clause_no               text NOT NULL DEFAULT '',
    citation                text NOT NULL DEFAULT '',
    kind                    text NOT NULL DEFAULT 'unclassified'
                                 CHECK (kind IN ('coverage','exclusion','definition','limit','unclassified')),
    citeable                boolean NOT NULL,
    statute                 boolean NOT NULL DEFAULT false,
    paragraph_no_ambiguous  boolean NOT NULL DEFAULT false,
    locator                 jsonb NOT NULL,
    table_count             int NOT NULL DEFAULT 0 CHECK (table_count >= 0),
    tables_on_pages         jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (document_extraction_id, ordinal),
    -- ★인용 테이블이 (id, citeable) 복합 FK 로 참조한다. 이 UNIQUE 가 그 대상이다.
    UNIQUE (id, citeable),
    -- ★준용이 같은 버전 안에 머물게 하는 복합 FK 의 대상.
    UNIQUE (id, policy_version_id),
    FOREIGN KEY (document_extraction_id, confirmed_document_id)
        REFERENCES core.document_extraction (id, confirmed_document_id),
    FOREIGN KEY (policy_version_id, confirmed_document_id)
        REFERENCES core.policy_version (id, confirmed_document_id)
);
CREATE INDEX policy_clause_content_idx ON core.policy_clause (content_hash);
CREATE INDEX policy_clause_version_idx ON core.policy_clause (policy_version_id);
-- 표시·검색용. 유일하지 않으므로 UNIQUE 가 아니다.
CREATE INDEX policy_clause_qno_idx     ON core.policy_clause (qualified_no);
COMMENT ON COLUMN core.policy_clause.ordinal IS
    '★식별키. qualified_no 는 문서 내 중복 31,085건이라 식별자가 될 수 없다(실측)';
COMMENT ON COLUMN core.policy_clause.citeable IS
    '★page_fallback 은 false. 인용 테이블의 복합 FK 가 이 값으로 막는다';

CREATE TABLE core.clause_chunk (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content_hash       text NOT NULL REFERENCES core.clause_content(content_hash),
    -- ★policy_version_id 를 두지 않는다(코덱스 3라운드 ⑥).
    --   content_hash 하나가 최대 170개 문서·버전에 실리는데 컬럼은 한 값만 담는다.
    --   버전 필터는 policy_clause 를 조인해서 건다:
    --     clause_chunk JOIN policy_clause USING (content_hash)
    --      WHERE policy_clause.policy_version_id = $1
    chunk_index        int  NOT NULL CHECK (chunk_index >= 0),
    text               text NOT NULL,
    token_count        int CHECK (token_count IS NULL OR token_count >= 0),
    chunk_type         text NOT NULL DEFAULT 'clause'
                            CHECK (chunk_type IN ('clause','paragraph_aligned','page_fallback')),
    paragraph_from     smallint,
    paragraph_to       smallint,
    UNIQUE (content_hash, chunk_index)
);
COMMENT ON TABLE core.clause_chunk IS
    '검색 전용. 임베딩 컬럼은 002_embedding.sql — 모델이 바뀌면 전량 재구축이라 분리했다';

CREATE TABLE core.clause_code_rule (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    content_hash       text NOT NULL REFERENCES core.clause_content(content_hash),
    code_letter        char(1) NOT NULL,
    code_lo            int NOT NULL,
    code_lo_sub        int,
    code_hi            int NOT NULL,
    code_hi_sub        int,
    kind               text NOT NULL CHECK (kind IN ('exclude','exception','mention')),
    quote              text NOT NULL DEFAULT '',
    source_span        jsonb,
    extractor_version  text NOT NULL,
    confidence         numeric CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    CHECK (code_lo <= code_hi),
    -- ★재적재 멱등성. 같은 조항·같은 범위·같은 추출기면 한 줄이다.
    UNIQUE (content_hash, code_letter, code_lo, code_lo_sub, code_hi, code_hi_sub, kind, extractor_version)
);
-- ★mention 은 판정 근거로 쓰지 않는다(전체의 39%). 부분 인덱스로 뺀다.
CREATE INDEX clause_code_rule_range_idx
    ON core.clause_code_rule (code_letter, code_lo, code_hi)
    WHERE kind <> 'mention';
CREATE INDEX clause_code_rule_content_idx ON core.clause_code_rule (content_hash);
COMMENT ON TABLE core.clause_code_rule IS
    '★kcd_code 와 다르다. 국가 표준 분류표가 아니라 우리가 약관에서 추출한 파생물이다';

CREATE TABLE core.clause_reference (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    src_clause_id      uuid NOT NULL REFERENCES core.policy_clause(id),
    raw_text           text NOT NULL,
    target_clause_id   uuid REFERENCES core.policy_clause(id),
    resolution_status  text NOT NULL
                            CHECK (resolution_status IN ('resolved','ambiguous','external','unresolved')),
    resolver_version   text NOT NULL,
    src_ordinal        int NOT NULL DEFAULT 0 CHECK (src_ordinal >= 0),
    policy_version_id  uuid,
    -- ★resolved 인데 대상이 비어 있을 수 없다. 반대도 마찬가지.
    CHECK ((resolution_status = 'resolved') = (target_clause_id IS NOT NULL)),
    UNIQUE (src_clause_id, src_ordinal),
    FOREIGN KEY (src_clause_id, policy_version_id)
        REFERENCES core.policy_clause (id, policy_version_id),
    -- ★준용은 같은 버전 안에서만 해소된다. 2019년 약관이 2024년 조항을 가리키지 못한다.
    FOREIGN KEY (target_clause_id, policy_version_id)
        REFERENCES core.policy_clause (id, policy_version_id)
);
CREATE INDEX clause_reference_src_idx    ON core.clause_reference (src_clause_id);
CREATE INDEX clause_reference_status_idx ON core.clause_reference (resolution_status);
CREATE INDEX clause_reference_target_idx ON core.clause_reference (target_clause_id);
COMMENT ON COLUMN core.clause_reference.target_clause_id IS
    '★NULL 이 다수다. 실측 해소 37.6% · 모호 52.6% · 외부 9.9% — NOT NULL 로 두면 절반 이상을 못 넣는다';

CREATE TABLE core.kcd_version (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    label           text NOT NULL UNIQUE,
    effective_from  date,
    effective_to    date
);

CREATE TABLE core.kcd_code (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kcd_version_id  uuid NOT NULL REFERENCES core.kcd_version(id),
    code            text NOT NULL,
    name_ko         text NOT NULL,
    UNIQUE (kcd_version_id, code)
);
COMMENT ON TABLE core.kcd_code IS
    '★적재원이 저장소에 없다. 통계청 KCD 표를 받아야 채워진다 — P0-d';

