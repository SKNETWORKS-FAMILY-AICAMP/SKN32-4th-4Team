-- 002_grants.sql — 롤·소유자·권한
--
-- ★스키마 GRANT 만으로 append-only 가 되지 않는다(코덱스 1·3라운드).
--   REVOKE 는 **소유자**를 막지 못한다. 소유자는 자기 권한을 스스로 되돌린다.
--   그래서 소유자를 앱과 분리한다:
--     insurance_owner  NOLOGIN. 스키마·테이블 소유. 마이그레이션만 이 롤로 돈다
--     insurance_app    NOLOGIN. 런타임. 필요한 최소 권한만 상속받는다
--   실제 로그인 롤은 운영에서 만들고 여기 비밀번호를 남기지 않는다.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'insurance_owner') THEN
        CREATE ROLE insurance_owner NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'insurance_app') THEN
        CREATE ROLE insurance_app NOLOGIN;
    END IF;
END $$;

ALTER SCHEMA core OWNER TO insurance_owner;
ALTER SCHEMA app  OWNER TO insurance_owner;
ALTER SCHEMA ops  OWNER TO insurance_owner;

DO $$
DECLARE t record;
BEGIN
    FOR t IN SELECT schemaname, tablename FROM pg_tables
              WHERE schemaname IN ('core','app','ops')
    LOOP
        EXECUTE format('ALTER TABLE %I.%I OWNER TO insurance_owner', t.schemaname, t.tablename);
    END LOOP;
END $$;

REVOKE ALL ON SCHEMA core, app, ops FROM PUBLIC;
GRANT USAGE ON SCHEMA core, app, ops TO insurance_app;

-- core — 참조 데이터. 읽기만.
GRANT SELECT ON ALL TABLES IN SCHEMA core TO insurance_app;
ALTER DEFAULT PRIVILEGES FOR ROLE insurance_owner IN SCHEMA core
    GRANT SELECT ON TABLES TO insurance_app;

-- app — 업무 데이터. 아직 테이블이 없지만 기본 권한을 미리 건다.
ALTER DEFAULT PRIVILEGES FOR ROLE insurance_owner IN SCHEMA app
    GRANT SELECT, INSERT, UPDATE ON TABLES TO insurance_app;

-- ops.admin_user — ★SELECT 만.
--   "관리자 승격은 CLI 전용" 을 DB 권한이 깨뜨리면 안 된다(코덱스 3라운드 ⑧).
--   승격은 insurance_owner 또는 운영자 계정으로만 한다.
GRANT SELECT ON ops.admin_user TO insurance_app;

-- ops.audit_log — INSERT·SELECT 만. UPDATE·DELETE·TRUNCATE 는 명시적으로 회수.
GRANT SELECT, INSERT ON ops.audit_log TO insurance_app;
REVOKE UPDATE, DELETE, TRUNCATE ON ops.audit_log FROM insurance_app;
GRANT USAGE ON SEQUENCE ops.audit_log_id_seq TO insurance_app;

-- ── 적용 후 눈으로 확인 ────────────────────────────────────────
-- SELECT grantee, privilege_type FROM information_schema.table_privileges
--  WHERE table_schema='ops' AND table_name IN ('audit_log','admin_user')
--    AND grantee='insurance_app';
--   → audit_log: INSERT,SELECT 만 / admin_user: SELECT 만
