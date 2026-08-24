-- 등록 외부 에이전트 API 전용 PostgreSQL 스키마.
-- ★insurance_agent 별도 DB에만 적용한다. 합성 insurance_demo·검색 mall_vec와 섞지 않는다.
-- ★BEGIN/COMMIT은 apply.py가 DDL+ledger를 한 트랜잭션으로 묶으므로 두지 않는다.

CREATE SCHEMA ops;

CREATE TABLE ops.agent_client (
    client_id              varchar(64) PRIMARY KEY
                           CHECK (client_id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{2,63}$'),
    display_name           varchar(120) NOT NULL,
    api_key_hash           char(64) NOT NULL UNIQUE
                           CHECK (api_key_hash ~ '^[0-9a-f]{64}$'),
    key_fingerprint        varchar(16) NOT NULL UNIQUE
                           CHECK (key_fingerprint ~ '^[0-9a-f]{16}$'),
    scopes                 text[] NOT NULL CHECK (
                               cardinality(scopes) > 0
                               AND scopes <@ ARRAY[
                                   'precheck:read', 'terms:read',
                                   'observations:write', 'cohort:read'
                               ]::text[]
                           ),
    rate_limit_per_minute  integer NOT NULL DEFAULT 60
                           CHECK (rate_limit_per_minute BETWEEN 1 AND 60000),
    status                 text NOT NULL DEFAULT 'active'
                           CHECK (status IN ('active', 'disabled')),
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    key_rotated_at         timestamptz
);

-- 인증 성공·실패 이력. Authorization/API key/IP/User-Agent 원문은 받을 컬럼 자체가 없다.
CREATE TABLE ops.agent_client_auth_log (
    auth_event_id          varchar(32) PRIMARY KEY,
    claimed_client_id      varchar(64),
    authenticated_client_id varchar(64) REFERENCES ops.agent_client(client_id),
    key_fingerprint        varchar(16) NOT NULL DEFAULT '',
    result                 text NOT NULL CHECK (
                               result IN ('success','missing','malformed','unknown','invalid','disabled')
                           ),
    trace_hash             char(64) CHECK (trace_hash IS NULL OR trace_hash ~ '^[0-9a-f]{64}$'),
    occurred_at            timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX agent_auth_time_idx ON ops.agent_client_auth_log(occurred_at DESC);
CREATE INDEX agent_auth_client_idx
    ON ops.agent_client_auth_log(authenticated_client_id, occurred_at DESC);

-- 다중 워커가 공유하는 60초 sliding-window 기록. 허용·거절을 모두 남긴다.
CREATE TABLE ops.agent_rate_event (
    rate_event_id          varchar(32) PRIMARY KEY,
    client_id              varchar(64) NOT NULL REFERENCES ops.agent_client(client_id),
    operation              text NOT NULL CHECK (
                               operation IN ('support_manifest','precheck','terms_explain','cohort','observation')
                           ),
    subject_hash           char(64) NOT NULL CHECK (subject_hash ~ '^[0-9a-f]{64}$'),
    allowed                boolean NOT NULL,
    limit_per_minute       integer NOT NULL CHECK (limit_per_minute > 0),
    occurred_at            timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX agent_rate_bucket_idx
    ON ops.agent_rate_event(client_id, operation, subject_hash, occurred_at DESC);

-- 쓰기 요청 멱등성 정본. raw payload는 두지 않고 keyed request hash만 저장한다.
CREATE TABLE ops.agent_idempotency (
    client_id              varchar(64) NOT NULL REFERENCES ops.agent_client(client_id),
    idempotency_key        varchar(128) NOT NULL
                           CHECK (idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._~-]{7,127}$'),
    request_hash           char(64) NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    state                  text NOT NULL DEFAULT 'processing'
                           CHECK (state IN ('processing','completed','failed')),
    submission_id          varchar(128) NOT NULL DEFAULT '',
    created_at             timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at             timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (client_id, idempotency_key)
);
CREATE INDEX agent_idempotency_updated_idx ON ops.agent_idempotency(updated_at);

-- 원문을 받을 자유형 JSON/text 컬럼을 두지 않는 typed 감사 원장.
CREATE TABLE ops.agent_api_audit (
    audit_event_id         varchar(32) PRIMARY KEY,
    client_id              varchar(64) NOT NULL REFERENCES ops.agent_client(client_id),
    operation              text NOT NULL CHECK (
                               operation IN ('support_manifest','precheck','terms_explain','cohort','observation')
                           ),
    required_scope         text NOT NULL CHECK (
                               required_scope IN (
                                   'precheck:read','terms:read','observations:write','cohort:read'
                               )
                           ),
    subject_hash           char(64) NOT NULL CHECK (subject_hash ~ '^[0-9a-f]{64}$'),
    request_hash           char(64) CHECK (request_hash IS NULL OR request_hash ~ '^[0-9a-f]{64}$'),
    response_hash          char(64) CHECK (response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$'),
    trace_hash             char(64) NOT NULL CHECK (trace_hash ~ '^[0-9a-f]{64}$'),
    source_event_hash      char(64) CHECK (
                               source_event_hash IS NULL OR source_event_hash ~ '^[0-9a-f]{64}$'
                           ),
    http_status            smallint NOT NULL CHECK (http_status BETWEEN 100 AND 599),
    latency_ms             integer NOT NULL CHECK (latency_ms >= 0),
    verdict                varchar(40),
    abstained              boolean,
    reason_code            varchar(80),
    rule_engine_version    varchar(120),
    model_profile          varchar(160),
    policy_version_ref     varchar(160),
    citation_refs          text[] NOT NULL DEFAULT '{}'
                           CHECK (cardinality(citation_refs) <= 50),
    created_at             timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX agent_audit_client_time_idx
    ON ops.agent_api_audit(client_id, created_at DESC);
CREATE INDEX agent_audit_trace_idx ON ops.agent_api_audit(trace_hash);
CREATE INDEX agent_audit_source_idx
    ON ops.agent_api_audit(client_id, source_event_hash)
    WHERE source_event_hash IS NOT NULL;

COMMENT ON SCHEMA ops IS
    '등록 외부 에이전트 인증·한도·멱등·비식별 감사 전용. 원문 의료정보 저장 금지.';
COMMENT ON TABLE ops.agent_api_audit IS
    '요청/응답/subject/trace는 AGENT_HASH_SECRET HMAC만 저장하는 감사 원장.';
COMMENT ON TABLE ops.agent_idempotency IS
    '외부 쓰기 재시도 제어. 원문 payload 대신 keyed hash만 저장한다.';
