-- 003 · 보존기간 만료 이력을 **세기만** 하는 함수
--
-- ★★왜 (2026-08-26 · 코덱스 전역파괴함수 감사 P1)
--
--   `ops.prune_agent_history()` 는 4개 테이블에서 보존기간이 지난 행을 **즉시 지운다.**
--   CLI(`scripts/agent_clients.py prune`)는 그걸 그대로 부른다 —
--   **몇 건이 지워질지 보기 전에 지운다.**
--
--   지우는 것 자체는 맞다(보존기간이 지난 비식별 이력이다). 다만
--   **보고 나서 정하게** 해야 한다. 그래서 같은 조건으로 «세기만» 하는 함수를 둔다.
--
-- ★파기 함수를 안 고친다. 고치면 이미 쓰는 곳의 계약이 바뀐다.
--   ★두 함수가 **같은 조건**을 써야 한다 — 갈리면 미리보기가 거짓말이 된다.
--     그래서 `WHERE retention_until < p_before` 를 그대로 복사한다.
--     ★한쪽만 고치면 조용히 어긋난다. 고칠 때는 둘 다 고친다.

CREATE OR REPLACE FUNCTION ops.count_agent_history_expired(
    p_before timestamptz DEFAULT clock_timestamp())
RETURNS TABLE (relation_name text, expired_rows bigint)
LANGUAGE plpgsql
STABLE                      -- ★쓰지 않는다. 읽기 전용임을 타입으로 못박는다.
SECURITY DEFINER
SET search_path = pg_catalog, ops
AS $$
BEGIN
    relation_name := 'agent_client_auth_log';
    SELECT count(*) INTO expired_rows FROM ops.agent_client_auth_log
     WHERE retention_until < p_before;
    RETURN NEXT;

    relation_name := 'agent_rate_event';
    SELECT count(*) INTO expired_rows FROM ops.agent_rate_event
     WHERE retention_until < p_before;
    RETURN NEXT;

    relation_name := 'agent_api_audit';
    SELECT count(*) INTO expired_rows FROM ops.agent_api_audit
     WHERE retention_until < p_before;
    RETURN NEXT;

    relation_name := 'agent_idempotency';
    SELECT count(*) INTO expired_rows FROM ops.agent_idempotency
     WHERE retention_until < p_before;
    RETURN NEXT;
END;
$$;

ALTER FUNCTION ops.count_agent_history_expired(timestamptz)
    OWNER TO insurance_agent_owner;

-- ★파기와 **같은 권한**이다. 세는 것을 더 널리 열지 않는다 —
--   「무엇이 얼마나 쌓였나」도 운영 정보다.
REVOKE ALL ON FUNCTION ops.count_agent_history_expired(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ops.count_agent_history_expired(timestamptz)
    TO insurance_agent_admin;
