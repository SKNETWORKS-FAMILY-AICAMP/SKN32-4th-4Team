-- Link a final claim observation back to the exact precheck and keep its
-- idempotency/audit identity without storing the caller's raw key.

ALTER TABLE app.coverage_review
    ADD COLUMN trace_id varchar(128);

UPDATE app.coverage_review
   SET trace_id = NULLIF(response_snapshot ->> 'trace_id', '')
 WHERE response_snapshot IS NOT NULL;

CREATE UNIQUE INDEX coverage_review_trace_uq
    ON app.coverage_review (trace_id)
    WHERE trace_id IS NOT NULL;

ALTER TABLE app.claim
    ADD COLUMN submission_id varchar(64),
    ADD COLUMN source_event_key_hash char(64),
    ADD COLUMN source_payload_hash char(64),
    ADD CONSTRAINT claim_source_fields_set_check
        CHECK (
            (submission_id IS NULL) = (source_event_key_hash IS NULL) AND
            (submission_id IS NULL) = (source_payload_hash IS NULL)
        ),
    ADD CONSTRAINT claim_source_event_key_hash_check
        CHECK (
            source_event_key_hash IS NULL OR
            source_event_key_hash ~ '^[0-9a-f]{64}$'
        ),
    ADD CONSTRAINT claim_source_payload_hash_check
        CHECK (
            source_payload_hash IS NULL OR
            source_payload_hash ~ '^[0-9a-f]{64}$'
        );

CREATE UNIQUE INDEX claim_submission_id_uq
    ON app.claim (submission_id)
    WHERE submission_id IS NOT NULL;
CREATE UNIQUE INDEX claim_source_event_key_uq
    ON app.claim (source_event_key_hash)
    WHERE source_event_key_hash IS NOT NULL;

ALTER TABLE app.evidence
    ADD COLUMN submission_id varchar(64);

CREATE UNIQUE INDEX evidence_submission_id_uq
    ON app.evidence (submission_id)
    WHERE submission_id IS NOT NULL;

ALTER TABLE app.outcome
    ADD COLUMN reason text;

-- PostgreSQL CHECK constraints cannot reference the parent claim row, so use
-- a trigger for the cross-row date invariant.

CREATE FUNCTION app.enforce_outcome_date_order()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, app
AS $$
DECLARE
    claim_date date;
BEGIN
    SELECT claimed_on INTO claim_date FROM app.claim WHERE id = NEW.claim_id;
    IF claim_date IS NULL THEN
        RAISE EXCEPTION 'claim does not exist: %', NEW.claim_id
            USING ERRCODE = '23503';
    END IF;
    IF NEW.decided_on < claim_date THEN
        RAISE EXCEPTION 'outcome decided_on precedes claim claimed_on'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER outcome_date_order_guard
BEFORE INSERT OR UPDATE OF claim_id, decided_on ON app.outcome
FOR EACH ROW EXECUTE FUNCTION app.enforce_outcome_date_order();

DROP VIEW app.cohort_stats;
CREATE VIEW app.cohort_stats AS
SELECT d.kcd_code_id,
       pv.product_id,
       a.policy_version_id,
       pv.generation,
       s.age_band,
       verification.verification_method,
       count(DISTINCT o.id) AS n,
       count(DISTINCT o.id) FILTER (WHERE o.decision = 'approved') AS approved_n,
       count(DISTINCT o.id) FILTER (WHERE o.decision = 'denied') AS denied_n,
       'verified_real'::text AS data_source
FROM app.outcome o
JOIN app.claim c ON c.id = o.claim_id
JOIN app.coverage_review ca ON ca.id = c.case_id
JOIN app.subject s ON s.id = ca.subject_id
JOIN app.assessment a ON a.id = c.assessment_id
JOIN app.case_diagnosis d ON d.case_id = ca.id
JOIN core.policy_version pv ON pv.id = a.policy_version_id
JOIN LATERAL (
    SELECT v.verification_method
      FROM app.evidence e
      JOIN app.evidence_verification v ON v.evidence_id = e.id
     WHERE e.outcome_id = o.id AND v.result = 'verified'
     ORDER BY v.verified_at, v.id
     LIMIT 1
) verification ON true
GROUP BY d.kcd_code_id, pv.product_id, a.policy_version_id, pv.generation,
         s.age_band, verification.verification_method;

COMMENT ON COLUMN app.coverage_review.trace_id IS
    'Opaque precheck trace used to attach a later claim; unique when present';
COMMENT ON COLUMN app.claim.source_event_key_hash IS
    'HMAC-SHA256 of observation caller scope and idempotency key; raw key is not stored';
COMMENT ON COLUMN app.claim.source_payload_hash IS
    'SHA-256 of canonical final claim observation payload';
COMMENT ON COLUMN app.evidence.submission_id IS
    'Observation submission that introduced this evidence; keeps replay exact if more evidence is added later';

ALTER FUNCTION app.enforce_outcome_date_order() OWNER TO insurance_owner;
ALTER VIEW app.cohort_stats OWNER TO insurance_owner;
GRANT SELECT ON app.cohort_stats TO insurance_app;
