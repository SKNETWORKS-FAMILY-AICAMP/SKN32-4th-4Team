-- 020_occurrence_source_ordinal.sql
--
-- 발생행에 **산출물이 매긴 원래 순번**을 담는다.
--
-- ★★왜 필요한가 (2026-08-27 실측)
--
--   `policy_clause_occurrence.ordinal` 은 산출물의 순번이 아니다.
--   `assign_ordinals` 가 **색인에 든 행만** 줄세워 0부터 다시 매긴 값이다:
--       row_number() OVER (PARTITION BY source_kind
--                          ORDER BY page_from, qualified_no, content_hash)
--
--   그런데 `occurrence_id` 가 그 값으로 만들어진다
--   (`릴리스:sha256:source_kind:ordinal`). 그래서 —
--
--   ① `core.policy_clause.ordinal`(산출물 그대로)과 **번호 체계가 다르다.**
--      실측: 같은 (문서, 종류, 순번) 자리 185,418개 중 **내용 일치 62.51%**,
--      69,515개가 어긋난다. 인용을 원장에 저장하려고 조회하면 **30%가 실패**했다
--      (표본 300건: 찾음 209 · 못 찾음 91).
--
--   ② ★더 나쁜 것 — **인용 게이트 판정이 바뀌면 순번이 따라 바뀐다.**
--      색인에 드는 행이 게이트로 정해지기 때문이다. 오늘 하루만 강등 17,820행이었다.
--      게이트는 **판단**이고 앞으로도 바뀐다. 그 판단 결과로 만든 번호를
--      **영구 식별자**에 쓰면, 판단이 바뀔 때마다 어제 발급한 인용이 다른 조항을 가리킨다.
--
--   ★원래 의도는 산출물 순번이었다 — `app/core/ports/precheck.py` 의
--     `ordinal` 주석에 "조항 JSON 이 결정적으로 매긴 값"이라고 적혀 있다.
--     `assign_ordinals` 가 들어오면서 조용히 어긋났다.
--
-- ★해결 방향(코덱스와 교차검증) — 게이트 결과로 만든 순번을 영구 ID 에서 뺀다.
--     · `ordinal`        검색용 순번. 그대로 둔다.
--     · `source_ordinal` 산출물이 매긴 원래 순번. **영구 식별자는 이걸 쓴다.**
--   실측으로 확인한 전제 —
--     · 산출물 (문서, 종류, 순번) 자리 196,355개 중 **중복 0** → 키가 된다
--     · 한 문서 안 같은 content_hash 중복 **2,789자리** → 해시 단독은 모호하다.
--       그래서 `source_ordinal` + `content_hash` 를 **함께** 본다.
--
-- ★지금 바꾸는 이유 — 발급된 인용이 **0건**이다
--   (`core.clause_reference` 0행, `app.assessment_clause_citation` 0행).
--   소급 피해 없이 형식을 바꿀 수 있는 창은 지금뿐이다.
--
-- ★NULL 을 기본값으로 두지 않는다 — 「아직 안 채웠다」와 「0번」은 다르다.
--   못 채운 행은 `occurrence_id` 가 **빈 문자열**이 되어 호출부가 기권한다.

SET LOCAL search_path TO vec, public;

ALTER TABLE vec.policy_clause_occurrence
    ADD COLUMN IF NOT EXISTS source_ordinal integer;

COMMENT ON COLUMN vec.policy_clause_occurrence.source_ordinal IS
  '산출물(clauses.json)이 매긴 원래 수록 순번. occurrence_id 가 이 값으로 만들어진다. '
  'ordinal(검색용 재번호)과 다르다 — 게이트 판정이 바뀌면 ordinal 은 바뀌지만 이 값은 안 바뀐다.';

-- ★조회 경로가 `(sha256, source_kind, source_ordinal)` 이다. 색인을 걸어 둔다.
CREATE INDEX IF NOT EXISTS policy_clause_occurrence_source_ord
    ON vec.policy_clause_occurrence (sha256, source_kind, source_ordinal);
