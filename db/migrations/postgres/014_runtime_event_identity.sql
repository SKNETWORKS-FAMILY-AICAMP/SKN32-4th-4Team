-- 014_runtime_event_identity.sql
-- Runtime-generated events need an identity source after legacy rows are imported.

CREATE SEQUENCE ops.run_event_id_seq;
SELECT setval(
    'ops.run_event_id_seq',
    COALESCE((SELECT max(id) FROM ops.run_event), 0) + 1,
    false
);
ALTER TABLE ops.run_event
    ALTER COLUMN id SET DEFAULT nextval('ops.run_event_id_seq');
ALTER SEQUENCE ops.run_event_id_seq OWNED BY ops.run_event.id;
