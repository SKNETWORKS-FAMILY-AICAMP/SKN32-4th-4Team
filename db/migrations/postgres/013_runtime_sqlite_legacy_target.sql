-- 013_runtime_sqlite_legacy_target.sql
-- SQLite users/face_credentials/knowledge_gaps/run_events의 PostgreSQL 이관 대상.
-- 001_core.sql 및 004_app_ops.sql 이후에 적용한다.
-- BEGIN/COMMIT은 scripts/db/apply.py가 담당한다.

CREATE TABLE app.user_account (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    legacy_user_id   bigint UNIQUE,
    username         text NOT NULL UNIQUE,
    password_hash    text NOT NULL,
    role             text NOT NULL DEFAULT 'USER'
                         CHECK (role IN ('USER', 'ADMIN')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    deleted_at       timestamptz
);
COMMENT ON TABLE app.user_account IS
    '애플리케이션 로그인 계정. app.subject 및 ops.admin_user와 별도 의미를 가진다.';
COMMENT ON COLUMN app.user_account.legacy_user_id IS
    'SQLite users.id와의 일회성 이관·재실행 매핑키.';

CREATE TABLE app.face_credential (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          uuid NOT NULL UNIQUE REFERENCES app.user_account(id),
    embedding        bytea NOT NULL,
    embedding_dim    smallint NOT NULL DEFAULT 512 CHECK (embedding_dim > 0),
    created_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (octet_length(embedding) = embedding_dim * 4)
);
COMMENT ON TABLE app.face_credential IS
    '원본 사진 없이 float32 얼굴 임베딩만 저장한다. 앱 계정당 1개.';

CREATE TABLE ops.knowledge_gap (
    id               bigint PRIMARY KEY,
    question         text NOT NULL,
    trace_id         text NOT NULL DEFAULT '',
    resolved         boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),
    retention_until  timestamptz,
    deleted_at       timestamptz
);
CREATE INDEX knowledge_gap_open_idx
    ON ops.knowledge_gap (resolved, created_at);
CREATE INDEX knowledge_gap_trace_idx
    ON ops.knowledge_gap (trace_id);
COMMENT ON TABLE ops.knowledge_gap IS
    'PII 마스킹 후 관리자용 지식보강 큐. SQLite knowledge_gaps의 이관 대상.';

CREATE TABLE ops.run_event (
    id               bigint PRIMARY KEY,
    trace_id         text NOT NULL,
    kind             text NOT NULL,
    detail           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX run_event_trace_idx
    ON ops.run_event (trace_id, created_at);
CREATE INDEX run_event_kind_idx
    ON ops.run_event (kind, created_at);
COMMENT ON TABLE ops.run_event IS
    'append-only 실행 이벤트. 감사 로그(ops.audit_log)와 목적이 다르다.';
