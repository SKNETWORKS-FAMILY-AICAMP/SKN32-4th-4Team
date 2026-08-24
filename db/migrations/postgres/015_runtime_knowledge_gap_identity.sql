-- Runtime-generated knowledge gaps need an identity source after legacy rows are imported.

CREATE SEQUENCE ops.knowledge_gap_id_seq;
SELECT setval(
    'ops.knowledge_gap_id_seq',
    COALESCE((SELECT max(id) FROM ops.knowledge_gap), 0) + 1,
    false
);
ALTER TABLE ops.knowledge_gap
    ALTER COLUMN id SET DEFAULT nextval('ops.knowledge_gap_id_seq');
ALTER SEQUENCE ops.knowledge_gap_id_seq OWNED BY ops.knowledge_gap.id;
