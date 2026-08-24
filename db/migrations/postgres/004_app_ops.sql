-- Insurance application and operational schemas.
-- Apply after 001_core.sql.  This is the PostgreSQL source of truth for
-- customer cases, assessments, claims, evidence, consent, and agent audit.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE app.subject (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    age_band         text,
    sex              text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    retention_until  timestamptz,
    deleted_at       timestamptz
);

CREATE TABLE app.policy_holding (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id        uuid NOT NULL REFERENCES app.subject(id),
    product_id        uuid NOT NULL REFERENCES core.product(id),
    policy_version_id uuid NOT NULL REFERENCES core.policy_version(id),
    enrolled_on       date NOT NULL,
    terminated_on     date,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (terminated_on IS NULL OR enrolled_on <= terminated_on)
);
CREATE INDEX policy_holding_subject_idx ON app.policy_holding(subject_id);

CREATE TABLE app."case" (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id        uuid REFERENCES app.subject(id),
    policy_holding_id uuid NOT NULL REFERENCES app.policy_holding(id),
    incident_on       date NOT NULL,
    channel           text NOT NULL,
    agent_client_id   varchar,
    created_at        timestamptz NOT NULL DEFAULT now(),
    retention_until   timestamptz,
    deleted_at        timestamptz
);
CREATE INDEX case_subject_idx ON app."case"(subject_id);
CREATE INDEX case_policy_holding_idx ON app."case"(policy_holding_id);

CREATE TABLE app.case_diagnosis (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id          uuid NOT NULL REFERENCES app."case"(id),
    kcd_code_id      uuid REFERENCES core.kcd_code(id),
    ocr_confidence   numeric,
    user_corrected   boolean NOT NULL DEFAULT false,
    corrected_at     timestamptz,
    CHECK (ocr_confidence IS NULL OR (ocr_confidence >= 0 AND ocr_confidence <= 1))
);
CREATE INDEX case_diagnosis_case_idx ON app.case_diagnosis(case_id);

CREATE TABLE app.assessment (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id           uuid NOT NULL REFERENCES app."case"(id),
    policy_version_id uuid NOT NULL REFERENCES core.policy_version(id),
    verdict           text NOT NULL,
    abstained         boolean NOT NULL DEFAULT false,
    abstain_reason    text,
    missing_documents jsonb,
    rule_engine_version text NOT NULL,
    as_of             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX assessment_case_idx ON app.assessment(case_id);

CREATE TABLE app.assessment_clause_citation (
    assessment_id    uuid NOT NULL REFERENCES app.assessment(id),
    policy_clause_id uuid NOT NULL,
    citeable         boolean NOT NULL DEFAULT true CHECK (citeable),
    role             text NOT NULL CHECK (role IN ('ground','exclusion')),
    content_hash     char(64) NOT NULL,
    quote            text NOT NULL,
    locator          jsonb NOT NULL,
    PRIMARY KEY (assessment_id, policy_clause_id),
    FOREIGN KEY (policy_clause_id, citeable)
        REFERENCES core.policy_clause(id, citeable)
);

CREATE TABLE app.claim (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id         uuid NOT NULL UNIQUE REFERENCES app."case"(id),
    claimed_on      date NOT NULL,
    claimed_amount  numeric
);

CREATE TABLE app.outcome (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    claim_id      uuid NOT NULL UNIQUE REFERENCES app.claim(id),
    decision      text NOT NULL CHECK (decision IN ('approved','partial','denied')),
    paid_amount   numeric,
    decided_on    date NOT NULL
);

CREATE TABLE app.evidence (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    outcome_id            uuid NOT NULL REFERENCES app.outcome(id),
    doc_type              text NOT NULL,
    sha256_hash           char(64) NOT NULL,
    stored_ref            text NOT NULL,
    submitted_at          timestamptz NOT NULL DEFAULT now(),
    consistency_checked_at timestamptz,
    consistency_result    jsonb
);
CREATE INDEX evidence_outcome_idx ON app.evidence(outcome_id);

CREATE TABLE app.evidence_verification (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    evidence_id        uuid NOT NULL UNIQUE REFERENCES app.evidence(id),
    result             text NOT NULL CHECK (result IN ('verified','rejected','pending')),
    verification_method text NOT NULL,
    verified_by        uuid REFERENCES ops.admin_user(id),
    verified_at        timestamptz NOT NULL DEFAULT now(),
    reason             text
);

CREATE TABLE ops.agent_client (
    agent_client_id varchar PRIMARY KEY,
    name            text NOT NULL,
    api_key_hash    text NOT NULL,
    rate_limit_rpm  integer NOT NULL CHECK (rate_limit_rpm > 0),
    status          text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    disabled_at     timestamptz
);

CREATE TABLE ops.agent_client_auth_log (
    log_id          varchar PRIMARY KEY,
    agent_client_id varchar REFERENCES ops.agent_client(agent_client_id),
    attempted_at    timestamptz NOT NULL DEFAULT now(),
    result          text NOT NULL,
    retention_until timestamptz
);

CREATE TABLE ops.interaction_log (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    channel        text NOT NULL,
    agent_client_id varchar REFERENCES ops.agent_client(agent_client_id),
    source_event_id varchar,
    session_token  varchar,
    actor_kind     text NOT NULL,
    question_masked text,
    answer         text,
    abstained      boolean NOT NULL,
    gap_status     text,
    promoted_ref   varchar,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (agent_client_id, source_event_id)
);

CREATE TABLE ops.consent (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id      uuid NOT NULL REFERENCES app.subject(id),
    purpose         text NOT NULL,
    policy_version_id uuid REFERENCES core.policy_version(id),
    granted_at      timestamptz NOT NULL,
    revoked_at      timestamptz,
    retention_until timestamptz
);
CREATE INDEX consent_subject_idx ON ops.consent(subject_id);

CREATE VIEW app.cohort_stats AS
SELECT d.kcd_code_id,
       ph.product_id,
       ph.policy_version_id,
       pv.generation,
       count(DISTINCT o.id) AS n,
       count(DISTINCT o.id) FILTER (WHERE o.decision = 'approved') AS approved_n,
       count(DISTINCT o.id) FILTER (WHERE o.decision = 'denied') AS denied_n,
       'verified_real'::text AS data_source
FROM app.outcome o
JOIN app.claim c ON c.id = o.claim_id
JOIN app."case" ca ON ca.id = c.case_id
JOIN app.case_diagnosis d ON d.case_id = ca.id
JOIN app.policy_holding ph ON ph.id = ca.policy_holding_id
JOIN core.policy_version pv ON pv.id = ph.policy_version_id
WHERE EXISTS (
    SELECT 1
    FROM app.evidence e
    JOIN app.evidence_verification v ON v.evidence_id = e.id
    WHERE e.outcome_id = o.id AND v.result = 'verified'
)
GROUP BY d.kcd_code_id, ph.product_id, ph.policy_version_id, pv.generation;
