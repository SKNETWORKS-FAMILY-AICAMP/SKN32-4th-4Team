-- 019 · `index_generation` 의 **죽은 기본값**을 없앤다
--
-- ★★무엇이 문제였나 (2026-08-26)
--
--   옛 스키마는 `index_generation text NOT NULL DEFAULT 's5-mixed'` 였다.
--   그런데 `s5-mixed` 세대는 2026-08-26 에 은퇴해 **0행**이 됐다.
--
--   기본값으로 들어간 행은 **아무도 안 읽는다** — 검색·판정 경로가 전부
--   `current_generation()`(=`s6`)으로 거르기 때문이다.
--   오류는 안 난다. INSERT 는 성공하고, 그 행은 조용히 사라진 것과 같아진다.
--
--   적재 경로는 세대를 명시해 넘기므로 지금 실동작에 문제는 없다.
--   그러나 **직접 INSERT 하는 순간** 그렇게 된다. 기본값을 없애 **명시를 강제**한다.
--
-- ★`vec` 쪽은 017 이 처음부터 기본값 없이 만들었다. 여기서는 옛 스키마를 고친다.
--   ★단 이 마이그레이션은 `insurance_real` 에 적용된다. 옛 DB(`mall_vec`)의
--     같은 컬럼은 **그 DB 를 지울 때 함께 사라진다** — 여기서 원격으로 못 고친다.
--     그때까지 `mall_vec` 에 직접 INSERT 하지 않는다.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'vec'
           AND table_name = 'policy_clause_occurrence'
           AND column_name = 'index_generation'
           AND column_default IS NOT NULL
    ) THEN
        ALTER TABLE vec.policy_clause_occurrence
            ALTER COLUMN index_generation DROP DEFAULT;
        RAISE NOTICE 'vec.policy_clause_occurrence.index_generation 기본값을 없앴다';
    END IF;
END $$;
