-- runtime readiness는 DDL 변경 없이 migration ledger를 읽을 수 있어야 한다.
GRANT SELECT ON public.schema_migration TO insurance_app;
