-- 018 · `vec` 테이블 소유권을 `core` 와 같은 등급으로 맞춘다
--
-- ★★017 이 놓친 것 (2026-08-26, 코덱스 사전감사가 잡음)
--
--   017 은 `CREATE SCHEMA vec AUTHORIZATION insurance_owner` 로 **스키마** 소유자만 정했다.
--   그 안의 `CREATE TABLE` 은 실행한 역할(`postgres`)이 소유하게 된다 —
--   스키마 소유자를 따라가지 않는다.
--
--   실측 확인: `core` 12테이블은 전부 `insurance_owner` 소유인데
--   `vec` 3테이블은 `postgres` 소유였다. **같은 DB 안에서 규율이 갈렸다.**
--
--   ★소유자가 `postgres` 면 `005_integrity_and_privileges.sql` 이 세운
--     「소유자만 쓰고 런타임은 읽는다」 구도 밖에 놓인다.
--     약관 원문 파생물이라 그 구도 안에 있어야 한다.

ALTER TABLE vec.policy_clause_content     OWNER TO insurance_owner;
ALTER TABLE vec.policy_clause_chunk       OWNER TO insurance_owner;
ALTER TABLE vec.policy_clause_occurrence  OWNER TO insurance_owner;

-- 소유자가 바뀌면 기본 권한도 그 역할 기준으로 다시 건다.
GRANT USAGE ON SCHEMA vec TO insurance_app, insurance_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA vec TO insurance_app;
ALTER DEFAULT PRIVILEGES FOR ROLE insurance_owner IN SCHEMA vec
    GRANT SELECT ON TABLES TO insurance_app;
