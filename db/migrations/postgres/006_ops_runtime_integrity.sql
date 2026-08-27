-- ops runtime integrity and least-privilege follow-up.
--
-- 004/005 이후 repository 연결 과정에서 확인된 두 간극을 보정한다.
--   1) insurance_app은 consent를 직접 쓸 권한도, 안전한 상태전이 함수도 없었다.
--   2) UNIQUE(agent_client_id, source_event_id)는 agent_client_id=NULL 중복을 막지 못한다.

ALTER TABLE ops.agent_client
    ADD CONSTRAINT agent_client_status_check
        CHECK (status IN ('active','disabled')),
    ADD CONSTRAINT agent_client_disabled_at_check
        CHECK (
            (status = 'active' AND disabled_at IS NULL) OR
            (status = 'disabled' AND disabled_at IS NOT NULL)
        ),
    ADD CONSTRAINT agent_client_name_nonblank_check
        CHECK (NULLIF(btrim(name), '') IS NOT NULL);

ALTER TABLE ops.agent_client_auth_log
    ADD CONSTRAINT agent_auth_result_nonblank_check
        CHECK (NULLIF(btrim(result), '') IS NOT NULL);

ALTER TABLE ops.interaction_log
    ADD CONSTRAINT interaction_channel_nonblank_check
        CHECK (NULLIF(btrim(channel), '') IS NOT NULL),
    ADD CONSTRAINT interaction_actor_kind_nonblank_check
        CHECK (NULLIF(btrim(actor_kind), '') IS NOT NULL),
    ADD CONSTRAINT interaction_source_event_nonblank_check
        CHECK (source_event_id IS NULL OR NULLIF(btrim(source_event_id), '') IS NOT NULL);

-- 기존 UNIQUE는 등록 agent의 멱등성을 담당한다. NULL agent는 별도 partial unique로 막는다.
CREATE UNIQUE INDEX interaction_anonymous_source_event_uq
    ON ops.interaction_log (channel, source_event_id)
    WHERE agent_client_id IS NULL AND source_event_id IS NOT NULL;

ALTER TABLE ops.consent
    ADD CONSTRAINT consent_purpose_nonblank_check
        CHECK (NULLIF(btrim(purpose), '') IS NOT NULL),
    ADD CONSTRAINT consent_revocation_order_check
        CHECK (revoked_at IS NULL OR revoked_at >= granted_at),
    ADD CONSTRAINT consent_retention_order_check
        CHECK (retention_until IS NULL OR retention_until >= granted_at);

-- policy_version_id가 있는 동의와 없는 동의를 각각 NULL-safe하게 하나만 활성화한다.
CREATE UNIQUE INDEX consent_active_versioned_uq
    ON ops.consent (subject_id, purpose, policy_version_id)
    WHERE revoked_at IS NULL AND policy_version_id IS NOT NULL;
CREATE UNIQUE INDEX consent_active_unversioned_uq
    ON ops.consent (subject_id, purpose)
    WHERE revoked_at IS NULL AND policy_version_id IS NULL;

CREATE FUNCTION ops.grant_consent(
    p_subject_id uuid,
    p_purpose text,
    p_policy_version_id uuid DEFAULT NULL,
    p_granted_at timestamptz DEFAULT now(),
    p_retention_until timestamptz DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, ops, app, core
AS $$
DECLARE
    consent_id uuid;
BEGIN
    IF NULLIF(btrim(p_purpose), '') IS NULL THEN
        RAISE EXCEPTION 'consent purpose must not be blank'
            USING ERRCODE = '22023';
    END IF;
    IF p_retention_until IS NOT NULL AND p_retention_until < p_granted_at THEN
        RAISE EXCEPTION 'consent retention_until precedes granted_at'
            USING ERRCODE = '23514';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended(
        p_subject_id::text || chr(31) || p_purpose || chr(31) ||
        coalesce(p_policy_version_id::text, '-'),
        0
    ));

    SELECT id INTO consent_id
      FROM ops.consent
     WHERE subject_id = p_subject_id
       AND purpose = p_purpose
       AND policy_version_id IS NOT DISTINCT FROM p_policy_version_id
       AND revoked_at IS NULL;
    IF consent_id IS NOT NULL THEN
        RETURN consent_id;
    END IF;

    INSERT INTO ops.consent(
        subject_id, purpose, policy_version_id, granted_at, retention_until
    ) VALUES (
        p_subject_id, p_purpose, p_policy_version_id, p_granted_at, p_retention_until
    )
    RETURNING id INTO consent_id;
    RETURN consent_id;
END $$;

CREATE FUNCTION ops.revoke_consent(
    p_consent_id uuid,
    p_revoked_at timestamptz DEFAULT now()
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, ops
AS $$
DECLARE
    consent_id uuid;
    granted timestamptz;
BEGIN
    SELECT id, granted_at INTO consent_id, granted
      FROM ops.consent
     WHERE id = p_consent_id
     FOR UPDATE;
    IF consent_id IS NULL THEN
        RAISE EXCEPTION 'consent does not exist: %', p_consent_id
            USING ERRCODE = '23503';
    END IF;
    IF p_revoked_at < granted THEN
        RAISE EXCEPTION 'consent revoked_at precedes granted_at'
            USING ERRCODE = '23514';
    END IF;

    UPDATE ops.consent
       SET revoked_at = coalesce(revoked_at, p_revoked_at)
     WHERE id = p_consent_id;
    RETURN consent_id;
END $$;

ALTER FUNCTION ops.grant_consent(uuid, text, uuid, timestamptz, timestamptz)
    OWNER TO insurance_owner;
ALTER FUNCTION ops.revoke_consent(uuid, timestamptz)
    OWNER TO insurance_owner;

REVOKE ALL ON ops.consent FROM insurance_app;
GRANT SELECT ON ops.consent TO insurance_app;
REVOKE ALL ON FUNCTION ops.grant_consent(uuid, text, uuid, timestamptz, timestamptz)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION ops.revoke_consent(uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.grant_consent(uuid, text, uuid, timestamptz, timestamptz)
    TO insurance_app;
GRANT EXECUTE ON FUNCTION ops.revoke_consent(uuid, timestamptz)
    TO insurance_app;
