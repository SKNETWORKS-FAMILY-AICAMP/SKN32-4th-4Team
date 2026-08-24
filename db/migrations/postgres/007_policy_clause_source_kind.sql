-- occurrence_id의 clause|annex 축을 core.policy_clause에도 보존한다.
-- 기존 행의 source_kind를 제목이나 locator로 추정하지 않는다.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM core.policy_clause) THEN
        RAISE EXCEPTION
            '007 requires an explicit policy_clause source_kind backfill; refusing to guess';
    END IF;
END $$;

ALTER TABLE core.policy_clause
    ADD COLUMN source_kind text NOT NULL,
    ADD CONSTRAINT policy_clause_source_kind_check
        CHECK (source_kind IN ('clause','annex')),
    DROP CONSTRAINT policy_clause_document_extraction_id_ordinal_key,
    ADD CONSTRAINT policy_clause_extraction_source_ordinal_key
        UNIQUE (document_extraction_id, source_kind, ordinal);

COMMENT ON COLUMN core.policy_clause.source_kind IS
    'occurrence source axis. clause and annex ordinals are independent; never infer during backfill';
