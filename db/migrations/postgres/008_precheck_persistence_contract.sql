-- Persist every precheck, including abstentions before policy resolution.
-- Unknown policy/version stays NULL; never substitute the latest row.

ALTER TABLE app.coverage_review
    ALTER COLUMN policy_holding_id DROP NOT NULL,
    ADD COLUMN request_key_hash char(64),
    ADD COLUMN request_payload_hash char(64),
    ADD COLUMN response_snapshot jsonb,
    ADD CONSTRAINT coverage_review_request_fields_pair_check
        CHECK (
            (request_key_hash IS NULL) = (request_payload_hash IS NULL) AND
            (request_key_hash IS NULL) = (response_snapshot IS NULL)
        ),
    ADD CONSTRAINT coverage_review_request_key_hash_check
        CHECK (request_key_hash IS NULL OR request_key_hash ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT coverage_review_payload_hash_check
        CHECK (request_payload_hash IS NULL OR request_payload_hash ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT coverage_review_response_shape_check
        CHECK (response_snapshot IS NULL OR jsonb_typeof(response_snapshot) = 'object');

-- Registered agents are isolated by client; anonymous/public requests by channel.
CREATE UNIQUE INDEX coverage_review_agent_request_uq
    ON app.coverage_review (agent_client_id, request_key_hash)
    WHERE agent_client_id IS NOT NULL AND request_key_hash IS NOT NULL;
CREATE UNIQUE INDEX coverage_review_anonymous_request_uq
    ON app.coverage_review (channel, request_key_hash)
    WHERE agent_client_id IS NULL AND request_key_hash IS NOT NULL;

ALTER TABLE app.assessment
    ALTER COLUMN policy_version_id DROP NOT NULL,
    ADD CONSTRAINT assessment_resolved_policy_check
        CHECK (policy_version_id IS NOT NULL OR abstained);

COMMENT ON COLUMN app.coverage_review.request_key_hash IS
    'HMAC-SHA256 of caller idempotency key; raw key is never stored';
COMMENT ON COLUMN app.coverage_review.request_payload_hash IS
    'SHA-256 of canonical persistence request; detects same key with changed payload';
COMMENT ON COLUMN app.coverage_review.response_snapshot IS
    'Original API response for exact idempotent replay; may contain sensitive insurance facts';
