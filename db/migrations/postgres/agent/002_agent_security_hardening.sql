-- 001 적용 후 독립 리뷰에서 확인된 lease·PII·권한·보존 경계를 전진 보강한다.
-- ★001은 이미 적용된 불변 migration이므로 수정하지 않는다.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- caller-controlled Idempotency-Key 원문을 제거하고 HMAC 식별자만 저장할 자리를 만든다.
ALTER TABLE ops.agent_idempotency
    ADD COLUMN idempotency_hash char(64),
    ADD COLUMN lease_token varchar(32) NOT NULL DEFAULT repeat('0', 32),
    ADD COLUMN lease_generation integer NOT NULL DEFAULT 1 CHECK (lease_generation > 0),
    ADD COLUMN retention_until timestamptz NOT NULL DEFAULT (clock_timestamp() + interval '90 days');

-- 기존 행은 일회성 비밀 없는 지문으로 이관한다. 이후 런타임 신규 행은 AGENT_HASH_SECRET HMAC이다.
UPDATE ops.agent_idempotency
SET idempotency_hash = encode(
    digest(client_id || E'\x1f' || idempotency_key, 'sha256'),
    'hex'
);

ALTER TABLE ops.agent_idempotency
    ALTER COLUMN idempotency_hash SET NOT NULL,
    ALTER COLUMN lease_token DROP DEFAULT,
    DROP CONSTRAINT agent_idempotency_pkey,
    DROP COLUMN idempotency_key,
    ADD PRIMARY KEY (client_id, idempotency_hash);

ALTER TABLE ops.agent_client_auth_log
    ADD COLUMN retention_until timestamptz NOT NULL DEFAULT (clock_timestamp() + interval '30 days');
ALTER TABLE ops.agent_rate_event
    ADD COLUMN retention_until timestamptz NOT NULL DEFAULT (clock_timestamp() + interval '2 days');
ALTER TABLE ops.agent_api_audit
    ADD COLUMN retention_until timestamptz NOT NULL DEFAULT (clock_timestamp() + interval '90 days');

-- 거절 spam은 현재 window count 대상이 아니다. allowed 행만 좁게 읽는다.
CREATE INDEX agent_rate_allowed_bucket_idx
    ON ops.agent_rate_event(client_id, operation, subject_hash, occurred_at DESC)
    WHERE allowed=true;
CREATE INDEX agent_auth_retention_idx ON ops.agent_client_auth_log(retention_until);
CREATE INDEX agent_rate_retention_idx ON ops.agent_rate_event(retention_until);
CREATE INDEX agent_audit_retention_idx ON ops.agent_api_audit(retention_until);
CREATE INDEX agent_idempotency_retention_idx ON ops.agent_idempotency(retention_until);

-- 파기는 runtime이 아니라 admin이 명시적으로 실행한다.
CREATE FUNCTION ops.prune_agent_history(p_before timestamptz DEFAULT clock_timestamp())
RETURNS TABLE (relation_name text, deleted_rows bigint)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, ops
AS $$
DECLARE
    n bigint;
BEGIN
    DELETE FROM ops.agent_client_auth_log WHERE retention_until < p_before;
    GET DIAGNOSTICS n = ROW_COUNT;
    relation_name := 'agent_client_auth_log'; deleted_rows := n; RETURN NEXT;

    DELETE FROM ops.agent_rate_event WHERE retention_until < p_before;
    GET DIAGNOSTICS n = ROW_COUNT;
    relation_name := 'agent_rate_event'; deleted_rows := n; RETURN NEXT;

    DELETE FROM ops.agent_api_audit WHERE retention_until < p_before;
    GET DIAGNOSTICS n = ROW_COUNT;
    relation_name := 'agent_api_audit'; deleted_rows := n; RETURN NEXT;

    DELETE FROM ops.agent_idempotency WHERE retention_until < p_before;
    GET DIAGNOSTICS n = ROW_COUNT;
    relation_name := 'agent_idempotency'; deleted_rows := n; RETURN NEXT;
END;
$$;

-- 로컬 userspace PG에서도 runtime/admin/owner 권한 차이를 실제로 검증할 수 있게 역할을 분리한다.
-- 운영에서는 pg_hba/TLS와 별도 비밀번호·인증서를 반드시 설정한다.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='insurance_agent_owner') THEN
        CREATE ROLE insurance_agent_owner NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='insurance_agent_runtime') THEN
        CREATE ROLE insurance_agent_runtime LOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='insurance_agent_admin') THEN
        CREATE ROLE insurance_agent_admin LOGIN;
    END IF;
END
$$;

ALTER SCHEMA ops OWNER TO insurance_agent_owner;
ALTER TABLE ops.agent_client OWNER TO insurance_agent_owner;
ALTER TABLE ops.agent_client_auth_log OWNER TO insurance_agent_owner;
ALTER TABLE ops.agent_rate_event OWNER TO insurance_agent_owner;
ALTER TABLE ops.agent_idempotency OWNER TO insurance_agent_owner;
ALTER TABLE ops.agent_api_audit OWNER TO insurance_agent_owner;
ALTER FUNCTION ops.prune_agent_history(timestamptz) OWNER TO insurance_agent_owner;

REVOKE ALL ON SCHEMA ops FROM PUBLIC;
GRANT USAGE ON SCHEMA ops TO insurance_agent_runtime, insurance_agent_admin;

REVOKE ALL ON ALL TABLES IN SCHEMA ops FROM PUBLIC, insurance_agent_runtime, insurance_agent_admin;
GRANT SELECT ON ops.agent_client TO insurance_agent_runtime;
GRANT INSERT ON ops.agent_client_auth_log TO insurance_agent_runtime;
GRANT SELECT, INSERT ON ops.agent_rate_event TO insurance_agent_runtime;
GRANT SELECT, INSERT, UPDATE ON ops.agent_idempotency TO insurance_agent_runtime;
GRANT INSERT ON ops.agent_api_audit TO insurance_agent_runtime;

GRANT SELECT, INSERT, UPDATE ON ops.agent_client TO insurance_agent_admin;
GRANT SELECT ON ops.agent_client_auth_log, ops.agent_rate_event,
                ops.agent_idempotency, ops.agent_api_audit TO insurance_agent_admin;

REVOKE ALL ON FUNCTION ops.prune_agent_history(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.prune_agent_history(timestamptz) TO insurance_agent_admin;

COMMENT ON FUNCTION ops.prune_agent_history(timestamptz) IS
    '보존기간이 지난 인증/rate/audit/idempotency 이력을 admin이 명시적으로 파기한다.';
