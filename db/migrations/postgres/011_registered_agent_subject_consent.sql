-- registered-agent subject identity is stored as a keyed hash, never as raw subject data.
ALTER TABLE app.subject
    ADD COLUMN IF NOT EXISTS subject_ref_hash text;

CREATE UNIQUE INDEX IF NOT EXISTS subject_ref_hash_active_uq
    ON app.subject(subject_ref_hash)
    WHERE subject_ref_hash IS NOT NULL AND deleted_at IS NULL;
