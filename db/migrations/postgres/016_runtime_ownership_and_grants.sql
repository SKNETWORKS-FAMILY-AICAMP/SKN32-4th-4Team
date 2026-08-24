-- Runtime tables/sequences created after 005 must follow the same ownership
-- and application-role contract as the core schema.

ALTER TABLE app.user_account OWNER TO insurance_owner;
ALTER TABLE app.face_credential OWNER TO insurance_owner;
ALTER TABLE ops.knowledge_gap OWNER TO insurance_owner;
ALTER TABLE ops.run_event OWNER TO insurance_owner;

ALTER SEQUENCE ops.run_event_id_seq OWNER TO insurance_owner;
ALTER SEQUENCE ops.knowledge_gap_id_seq OWNER TO insurance_owner;

GRANT USAGE ON SCHEMA app, ops TO insurance_app;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON app.user_account, app.face_credential TO insurance_app;
GRANT SELECT, INSERT, UPDATE
    ON ops.knowledge_gap TO insurance_app;
GRANT SELECT, INSERT
    ON ops.run_event TO insurance_app;
GRANT USAGE, SELECT
    ON SEQUENCE ops.run_event_id_seq, ops.knowledge_gap_id_seq TO insurance_app;
