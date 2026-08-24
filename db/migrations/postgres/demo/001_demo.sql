-- 합성 에이전트 제출·검증 전용 PostgreSQL 스키마.
-- ★insurance_demo 별도 DB에만 적용한다. 실제 사례 DB와 UNION하지 않는다.
-- ★BEGIN/COMMIT은 apply.py가 DDL+ledger를 한 트랜잭션으로 묶으므로 두지 않는다.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA demo;

CREATE TABLE demo.submission (
    submission_id   varchar(64) PRIMARY KEY,
    run_id           varchar(32) NOT NULL,
    client_ref       varchar(80) NOT NULL,
    idempotency_key  varchar(64) NOT NULL,
    payload_hash     char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    insurer          text NOT NULL,
    enrolled_on      varchar(8) NOT NULL DEFAULT ''
                     CHECK (enrolled_on = '' OR enrolled_on ~ '^[0-9]{8}$'),
    kcd_codes        text[] NOT NULL CHECK (cardinality(kcd_codes) > 0),
    product_id       text NOT NULL DEFAULT '',
    age_band         text,
    outcome          text NOT NULL CHECK (outcome IN ('paid','denied','partial','pending')),
    outcome_reason   text NOT NULL DEFAULT '',
    precheck_trace_id text,
    data_source      text NOT NULL DEFAULT 'synthetic' CHECK (data_source = 'synthetic'),
    received_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_ref, idempotency_key)
);

CREATE INDEX submission_run_idx ON demo.submission(run_id);
CREATE INDEX submission_received_idx ON demo.submission(received_at);
CREATE INDEX submission_client_idx ON demo.submission(client_ref);
CREATE INDEX submission_kcd_gin_idx ON demo.submission USING gin(kcd_codes);

-- append-only 검증 이력. submission_id를 PK로 두지 않아 재검토 이력을 보존한다.
CREATE TABLE demo.verification_event (
    verification_id bigserial PRIMARY KEY,
    submission_id    varchar(64) NOT NULL REFERENCES demo.submission(submission_id),
    decision         text NOT NULL CHECK (decision IN ('accepted','rejected')),
    method           text NOT NULL
                     CHECK (method IN ('admin_review','simulated_consistency','legacy_import')),
    verification_level text NOT NULL
                     CHECK (verification_level IN
                            ('synthetic_admin_review','synthetic_consistency','legacy_synthetic')),
    rule_version     text NOT NULL,
    reason_codes     text[] NOT NULL DEFAULT '{}',
    evidence         jsonb NOT NULL DEFAULT '{}'::jsonb
                     CHECK (octet_length(evidence::text) <= 8192),
    actor            text NOT NULL,
    verified_at      timestamptz NOT NULL DEFAULT now()
);

-- 현행 집계에서 accepted는 한 제출당 한 번뿐이다. 이력 테이블 자체는 append-only다.
CREATE UNIQUE INDEX verification_one_accept_idx
    ON demo.verification_event(submission_id) WHERE decision = 'accepted';
CREATE INDEX verification_submission_idx ON demo.verification_event(submission_id);
CREATE INDEX verification_time_idx ON demo.verification_event(verified_at);
CREATE INDEX verification_reason_gin_idx ON demo.verification_event USING gin(reason_codes);

-- 합성 집계 전용 뷰. 다른 DB/스키마의 실제 사례와 결합하지 않는다.
CREATE VIEW demo.accepted_cohort_event AS
SELECT s.submission_id,
       s.run_id,
       s.client_ref,
       s.insurer,
       code AS kcd_code,
       s.product_id,
       s.age_band,
       s.outcome,
       v.verification_level,
       v.method AS verification_method,
       v.rule_version,
       v.verified_at,
       'synthetic'::text AS data_source
FROM demo.submission s
JOIN demo.verification_event v
  ON v.submission_id = s.submission_id AND v.decision = 'accepted'
CROSS JOIN LATERAL unnest(s.kcd_codes) AS code;

COMMENT ON SCHEMA demo IS
    '합성 시뮬레이션 전용. 실제 보험금 지급 사례나 진위 검증 데이터가 아니다.';
COMMENT ON TABLE demo.verification_event IS
    '합성 정합성/관리자 데모 검토 이력. accepted는 보험금 승인 또는 진위 확인을 뜻하지 않는다.';
