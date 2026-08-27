-- Preserve an unresolved/invalid submitted KCD code instead of dropping or guessing it.

ALTER TABLE app.case_diagnosis
    ADD COLUMN raw_kcd_code text,
    ADD CONSTRAINT diagnosis_raw_code_nonblank_check
        CHECK (raw_kcd_code IS NULL OR NULLIF(btrim(raw_kcd_code), '') IS NOT NULL),
    ADD CONSTRAINT diagnosis_code_reference_check
        CHECK (kcd_code_id IS NOT NULL OR raw_kcd_code IS NOT NULL);

COMMENT ON COLUMN app.case_diagnosis.raw_kcd_code IS
    'Submitted/OCR code as received; kcd_code_id remains NULL when it cannot be resolved';
