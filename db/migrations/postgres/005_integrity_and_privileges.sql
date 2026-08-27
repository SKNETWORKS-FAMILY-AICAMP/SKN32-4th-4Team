-- 005_integrity_and_privileges.sql
--
-- 004 이후 발견된 P0 보정만 담는다.
--   1) 판정 당시 약관 판본과 인용 조항 판본의 교차 참조 차단
--   2) claim 이 어느 assessment 를 근거로 했는지 고정
--   3) cohort_stats 를 판정 당시 판본으로 집계
--   4) consistent(계산 결과)와 verified(불변 행)의 비대칭 강제
--   5) 002 보다 늦게 생긴 객체의 owner/grant 재적용
--
-- ★기존 행을 "최신 assessment" 같은 추정값으로 백필하지 않는다. 실제 트랙은 아직
-- 0행이어야 한다. 행이 있으면 명시적으로 멈춰 별도 매핑 migration 을 요구한다.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM app.assessment_clause_citation) THEN
        RAISE EXCEPTION
            '005 requires an explicit citation policy_version_id backfill; refusing to guess';
    END IF;
    IF EXISTS (SELECT 1 FROM app.claim) THEN
        RAISE EXCEPTION
            '005 requires an explicit claim assessment_id backfill; refusing to choose latest assessment';
    END IF;
END $$;

-- ── 판정·인용·청구의 불변 연결 ──────────────────────────────────

-- 최신 핸드오프와 외부 ERD의 확정 명명. CASE 예약어 인용을 런타임 전체로
-- 퍼뜨리지 않는다. 004의 FK와 view 의존성은 PostgreSQL이 rename을 따라 갱신한다.
ALTER TABLE app."case" RENAME TO coverage_review;

ALTER TABLE app.assessment
    ADD CONSTRAINT assessment_id_policy_version_key
        UNIQUE (id, policy_version_id),
    ADD CONSTRAINT assessment_id_case_key
        UNIQUE (id, case_id),
    ADD CONSTRAINT assessment_verdict_check
        CHECK (verdict IN ('likely_covered','unlikely','needs_documents','needs_expert')),
    ADD CONSTRAINT assessment_abstain_reason_check
        CHECK (NOT abstained OR NULLIF(btrim(abstain_reason), '') IS NOT NULL);

ALTER TABLE core.policy_clause
    ADD CONSTRAINT policy_clause_id_content_hash_key UNIQUE (id, content_hash);

ALTER TABLE app.assessment_clause_citation
    ADD COLUMN policy_version_id uuid NOT NULL,
    ADD CONSTRAINT citation_policy_clause_version_fk
        FOREIGN KEY (policy_clause_id, policy_version_id)
        REFERENCES core.policy_clause (id, policy_version_id),
    ADD CONSTRAINT citation_assessment_version_fk
        FOREIGN KEY (assessment_id, policy_version_id)
        REFERENCES app.assessment (id, policy_version_id),
    ADD CONSTRAINT citation_policy_clause_content_fk
        FOREIGN KEY (policy_clause_id, content_hash)
        REFERENCES core.policy_clause (id, content_hash),
    ADD CONSTRAINT citation_content_hash_hex_check
        CHECK (content_hash ~ '^[0-9a-f]{64}$');

ALTER TABLE app.claim
    ADD COLUMN assessment_id uuid NOT NULL,
    ADD CONSTRAINT claim_assessment_same_case_fk
        FOREIGN KEY (assessment_id, case_id)
        REFERENCES app.assessment (id, case_id),
    ADD CONSTRAINT claim_amount_nonnegative_check
        CHECK (claimed_amount IS NULL OR claimed_amount >= 0);

ALTER TABLE app.outcome
    ADD CONSTRAINT outcome_paid_amount_nonnegative_check
        CHECK (paid_amount IS NULL OR paid_amount >= 0);

-- 계약 입력의 product 와 policy_version 도 서로 다른 원장을 가리킬 수 없게 한다.
ALTER TABLE core.policy_version
    ADD CONSTRAINT policy_version_id_product_key UNIQUE (id, product_id);

ALTER TABLE app.policy_holding
    ADD CONSTRAINT policy_holding_version_product_fk
        FOREIGN KEY (policy_version_id, product_id)
        REFERENCES core.policy_version (id, product_id);

-- case.subject_id 는 익명 입력 때문에 NULL 을 허용하지만, 값이 있으면 holding 과 같아야 한다.
ALTER TABLE app.policy_holding
    ADD CONSTRAINT policy_holding_id_subject_key UNIQUE (id, subject_id);

ALTER TABLE app.coverage_review
    ADD CONSTRAINT case_holding_subject_fk
        FOREIGN KEY (policy_holding_id, subject_id)
        REFERENCES app.policy_holding (id, subject_id),
    ADD CONSTRAINT case_agent_client_fk
        FOREIGN KEY (agent_client_id)
        REFERENCES ops.agent_client (agent_client_id);

-- ── 검증 사실: consistent 는 evidence 컬럼, verified/rejected 는 불변 행 ──

ALTER TABLE app.evidence
    ADD CONSTRAINT evidence_sha256_hex_check
        CHECK (sha256_hash ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT evidence_consistency_pair_check
        CHECK ((consistency_checked_at IS NULL) = (consistency_result IS NULL)),
    ADD CONSTRAINT evidence_consistency_shape_check
        CHECK (
            consistency_result IS NULL OR
            (
                jsonb_typeof(consistency_result) = 'object' AND
                consistency_result ? 'status' AND
                consistency_result->>'status' IN ('consistent','rejected')
            )
        );

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM app.evidence_verification WHERE result = 'pending'
    ) THEN
        RAISE EXCEPTION
            'pending verification rows must be resolved before applying 005';
    END IF;
END $$;

ALTER TABLE app.evidence_verification
    DROP CONSTRAINT evidence_verification_result_check,
    ADD CONSTRAINT evidence_verification_result_check
        CHECK (result IN ('verified','rejected')),
    ADD CONSTRAINT evidence_verification_method_check
        CHECK (NULLIF(btrim(verification_method), '') IS NOT NULL);

CREATE FUNCTION app.enforce_evidence_verification_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, app, ops
AS $$
DECLARE
    consistency_status text;
BEGIN
    SELECT e.consistency_result->>'status'
      INTO consistency_status
      FROM app.evidence e
     WHERE e.id = NEW.evidence_id
     FOR SHARE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'evidence does not exist: %', NEW.evidence_id
            USING ERRCODE = '23503';
    END IF;

    IF NEW.result = 'verified' THEN
        IF consistency_status IS DISTINCT FROM 'consistent' THEN
            RAISE EXCEPTION
                'evidence must be consistent before verified: % (status=%)',
                NEW.evidence_id, consistency_status
                USING ERRCODE = '23514';
        END IF;
        IF NEW.verified_by IS NULL THEN
            RAISE EXCEPTION 'verified evidence requires verified_by'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END $$;

CREATE TRIGGER evidence_verification_insert_guard
BEFORE INSERT ON app.evidence_verification
FOR EACH ROW EXECUTE FUNCTION app.enforce_evidence_verification_insert();

CREATE FUNCTION app.forbid_evidence_verification_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'evidence verification is append-only; % is forbidden', TG_OP
        USING ERRCODE = '42501';
END $$;

CREATE TRIGGER evidence_verification_append_only
BEFORE UPDATE OR DELETE ON app.evidence_verification
FOR EACH ROW EXECUTE FUNCTION app.forbid_evidence_verification_mutation();

CREATE FUNCTION app.record_evidence_consistency(
    p_evidence_id uuid,
    p_consistent boolean,
    p_details jsonb DEFAULT '{}'::jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, app
AS $$
DECLARE
    recorded jsonb;
BEGIN
    IF p_details IS NULL OR jsonb_typeof(p_details) <> 'object' THEN
        RAISE EXCEPTION 'consistency details must be a JSON object'
            USING ERRCODE = '22023';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM app.evidence_verification v
         WHERE v.evidence_id = p_evidence_id
    ) THEN
        RAISE EXCEPTION 'cannot recalculate consistency after verification: %', p_evidence_id
            USING ERRCODE = '55000';
    END IF;

    UPDATE app.evidence
       SET consistency_checked_at = now(),
           consistency_result = p_details || jsonb_build_object(
               'status', CASE WHEN p_consistent THEN 'consistent' ELSE 'rejected' END
           )
     WHERE id = p_evidence_id
     RETURNING consistency_result INTO recorded;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'evidence does not exist: %', p_evidence_id
            USING ERRCODE = '23503';
    END IF;

    RETURN recorded;
END $$;

CREATE FUNCTION app.record_evidence_verification(
    p_evidence_id uuid,
    p_result text,
    p_verification_method text,
    p_verified_by uuid,
    p_reason text DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, app, ops
AS $$
DECLARE
    inserted_id uuid;
    existing app.evidence_verification%ROWTYPE;
BEGIN
    IF p_result NOT IN ('verified','rejected') THEN
        RAISE EXCEPTION 'invalid verification result: %', p_result
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO app.evidence_verification (
        evidence_id, result, verification_method, verified_by, reason
    ) VALUES (
        p_evidence_id, p_result, p_verification_method, p_verified_by, p_reason
    )
    ON CONFLICT (evidence_id) DO NOTHING
    RETURNING id INTO inserted_id;

    IF inserted_id IS NOT NULL THEN
        RETURN inserted_id;
    END IF;

    SELECT * INTO existing
      FROM app.evidence_verification
     WHERE evidence_id = p_evidence_id;

    IF existing.result IS NOT DISTINCT FROM p_result
       AND existing.verification_method IS NOT DISTINCT FROM p_verification_method
       AND existing.verified_by IS NOT DISTINCT FROM p_verified_by
       AND existing.reason IS NOT DISTINCT FROM p_reason THEN
        RETURN existing.id;
    END IF;

    RAISE EXCEPTION 'conflicting verification already exists for evidence: %', p_evidence_id
        USING ERRCODE = '23505';
END $$;

-- ── 판정 당시 assessment 판본을 쓰는 코호트 게이트 ───────────────

CREATE OR REPLACE VIEW app.cohort_stats AS
SELECT d.kcd_code_id,
       pv.product_id,
       a.policy_version_id,
       pv.generation,
       count(DISTINCT o.id) AS n,
       count(DISTINCT o.id) FILTER (WHERE o.decision = 'approved') AS approved_n,
       count(DISTINCT o.id) FILTER (WHERE o.decision = 'denied') AS denied_n,
       'verified_real'::text AS data_source
FROM app.outcome o
JOIN app.claim c ON c.id = o.claim_id
JOIN app.coverage_review ca ON ca.id = c.case_id
JOIN app.assessment a ON a.id = c.assessment_id
JOIN app.case_diagnosis d ON d.case_id = ca.id
JOIN core.policy_version pv ON pv.id = a.policy_version_id
WHERE EXISTS (
    SELECT 1
    FROM app.evidence e
    JOIN app.evidence_verification v ON v.evidence_id = e.id
    WHERE e.outcome_id = o.id AND v.result = 'verified'
)
GROUP BY d.kcd_code_id, pv.product_id, a.policy_version_id, pv.generation;

-- ── 004 이후 객체의 owner/default privilege 보정 ────────────────

DO $$
DECLARE obj record;
BEGIN
    FOR obj IN
        SELECT table_schema, table_name
          FROM information_schema.tables
         WHERE table_schema IN ('core','app','ops')
           AND table_type = 'BASE TABLE'
    LOOP
        EXECUTE format(
            'ALTER TABLE %I.%I OWNER TO insurance_owner',
            obj.table_schema, obj.table_name
        );
    END LOOP;

    FOR obj IN
        SELECT table_schema, table_name
          FROM information_schema.views
         WHERE table_schema IN ('core','app','ops')
    LOOP
        EXECUTE format(
            'ALTER VIEW %I.%I OWNER TO insurance_owner',
            obj.table_schema, obj.table_name
        );
    END LOOP;

    FOR obj IN
        SELECT sequence_schema, sequence_name
          FROM information_schema.sequences
         WHERE sequence_schema IN ('core','app','ops')
    LOOP
        EXECUTE format(
            'ALTER SEQUENCE %I.%I OWNER TO insurance_owner',
            obj.sequence_schema, obj.sequence_name
        );
    END LOOP;
END $$;

ALTER FUNCTION app.enforce_evidence_verification_insert() OWNER TO insurance_owner;
ALTER FUNCTION app.forbid_evidence_verification_mutation() OWNER TO insurance_owner;
ALTER FUNCTION app.record_evidence_consistency(uuid, boolean, jsonb) OWNER TO insurance_owner;
ALTER FUNCTION app.record_evidence_verification(uuid, text, text, uuid, text) OWNER TO insurance_owner;

GRANT USAGE ON SCHEMA core, app, ops TO insurance_app;
GRANT SELECT ON ALL TABLES IN SCHEMA core TO insurance_app;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA app TO insurance_app;
GRANT SELECT ON ALL TABLES IN SCHEMA ops TO insurance_app;
GRANT INSERT ON ops.agent_client_auth_log, ops.interaction_log, ops.audit_log
    TO insurance_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA core, app, ops TO insurance_app;

-- 집계·검증 행과 정합성 컬럼은 전용 함수만 쓴다.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON app.cohort_stats FROM insurance_app;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON app.evidence_verification FROM insurance_app;
REVOKE UPDATE, DELETE, TRUNCATE ON app.evidence FROM insurance_app;
REVOKE UPDATE, DELETE, TRUNCATE ON ops.agent_client_auth_log, ops.interaction_log, ops.audit_log
    FROM insurance_app;

REVOKE ALL ON FUNCTION app.record_evidence_consistency(uuid, boolean, jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION app.record_evidence_verification(uuid, text, text, uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION app.record_evidence_consistency(uuid, boolean, jsonb)
    TO insurance_app;
GRANT EXECUTE ON FUNCTION app.record_evidence_verification(uuid, text, text, uuid, text)
    TO insurance_app;

ALTER DEFAULT PRIVILEGES FOR ROLE insurance_owner IN SCHEMA core
    GRANT SELECT ON TABLES TO insurance_app;
ALTER DEFAULT PRIVILEGES FOR ROLE insurance_owner IN SCHEMA app
    GRANT SELECT, INSERT, UPDATE ON TABLES TO insurance_app;
ALTER DEFAULT PRIVILEGES FOR ROLE insurance_owner IN SCHEMA ops
    GRANT SELECT ON TABLES TO insurance_app;
