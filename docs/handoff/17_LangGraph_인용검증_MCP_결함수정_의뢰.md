# LangGraph 인용 검증·MCP 호출 결함 수정 의뢰 — Agent 정재희

수신 **정재희(Agent·LangGraph)** · 2026-08-03 · 대상 [06_계약_Agent.md](06_계약_Agent.md) 담당

검토 대상: [`feature-agent/langgraph_agent/bugfix_summary.txt`](https://github.com/SKNETWORKS-FAMILY-AICAMP/SKN32-3rd-4Team/blob/feature-agent/langgraph_agent/bugfix_summary.txt)

> 한 줄 — **이미 고친 동작은 보존하고, 재현된 결함 3건을 회귀 테스트와 함께 수정한 뒤 인용 검증과 판정 그래프를 프로젝트 정본 한 벌로 합쳐 주세요.**

이 문서는 새 기능 제안서가 아니다. `feature-agent` 브랜치의 수정 내용을 프로젝트 계약과 대조하고 실제 입력으로 재현한 결과를 작업 단위로 넘기는 **결함 수정 의뢰서**다.

---

## 0. 요청 요약

아래 순서로 진행해 주세요.

1. **`mcp_caller` executor 정리 누락 수정** — 모든 종료 경로에서 `finally` 보장
2. **`undeclared_mentions` 준용 조항 오탐 수정** — 검증된 인용문 내부 번호만 제외
3. **`citation_guard.by_path` 덮어쓰기 수정** — 복수 후보 보존, 충돌 시 `ambiguous`
4. **계약 게이트 복구** — 문서 신뢰도와 0-근거 fail-closed를 실제 실행 경로에서 강제
5. **두 그래프 통합** — 외부 브랜치의 5노드·손잡이·재시도와 프로젝트 정본의 저장소 대조를 한 경로로 합치기
6. 관련 회귀 테스트와 전체 테스트 결과 제출

★단순히 외부 브랜치 파일을 현재 프로젝트에 덮어쓰면 안 된다. 양쪽에 서로 없는 안전장치가 있기 때문이다.

---

## 1. 이미 잘 반영된 내용 — 유지할 것

`bugfix_summary.txt`의 주장과 대상 코드를 대조했을 때 다음 변경은 실제로 반영돼 있었다. 아래 항목은 이번 수정에서 되돌리지 않는다.

| 영역 | 확인된 변경 | 요청 |
|---|---|---|
| `nodes/mcp_caller.py` | `policy_rag` 요청 시 약관 조항이 반드시 있어야 성공 | 유지 |
| `nodes/mcp_caller.py` | 호출마다 격리된 1-worker executor 사용 | 유지하되 모든 예외 경로 정리 보완 |
| `graph/router.py` | `json.loads("null")`의 `None` 방어 | 유지 |
| `graph/router.py` | 분류 실패 시 위험한 `policy_rag` 기본값 제거 | 유지 |
| `main.py` | 그래프A 대신 계약형 그래프B 호출 | 유지하되 프로젝트 정본으로 연결 |
| `graph/precheck_graph.py` | resolve/gate/retrieve/assess/explain 5노드 분해 | 프로젝트 정본에 이식 |
| `graph/precheck_graph.py` | 인용 검증 재시도 배선 복구 | 프로젝트 정본에 이식 |
| `graph/precheck_graph.py` | E001 손잡이 기반 explain/verify | 프로젝트 정본에 이식 |
| 그래프 계약 | 자율 ReAct 없음, 재시도 최대 2회 | 유지 |
| 그래프 계약 | `verdict`를 그래프가 재판정하지 않음 | 유지 |

특히 **재시도 코드가 존재하지만 `build()`에서 배선되지 않아 한 번도 실행되지 않던 결함을 찾아 수정한 것**은 유효한 개선이다.

---

## 2. P0-1 · `mcp_caller` 비-타임아웃 예외에서 executor 누수

### 재현 결과

도구 함수가 `ValueError` 같은 일반 예외를 던지도록 하고 50회 호출했다.

```text
호출 전 스레드 수  1
호출 후 스레드 수  16
결과              worker 15개 누수
```

### 원인

`_call_with_timeout`이 성공 경로와 timeout 경로에서는 `executor.shutdown()`을 호출하지만, 일반 예외 경로에는 `finally`가 없다. `future.result()`가 도구 예외를 다시 던지면 executor 정리 전에 함수가 빠져나간다.

### 수정 요구

- executor 생성 이후의 모든 경로를 `try/finally`로 감싼다.
- 성공, 호출 deadline 초과, 도구의 일반 예외, 도구 자체의 `TimeoutError`, 결과 파싱 예외에서 worker가 남지 않아야 한다.
- timeout 후 실행 중인 worker 때문에 `shutdown(wait=True)`가 무기한 대기하지 않도록 현재 비차단 정책을 보존한다.
- Python 3.11+에서 `concurrent.futures.TimeoutError is TimeoutError`인 점을 고려해 다음 두 경우를 구분한다.
  - wrapper가 정한 deadline을 넘김 → `MCP 호출 타임아웃`
  - 도구 구현이 스스로 `TimeoutError`를 던짐 → 도구 실행 오류로 원인과 예외 체인을 보존

### 회귀 테스트

1. 성공 호출을 반복해도 worker 수가 누적되지 않는다.
2. deadline timeout을 반복해도 worker 수가 호출 횟수만큼 증가하지 않는다.
3. `ValueError`를 50회 발생시켜도 executor worker가 누적되지 않는다.
4. 도구가 직접 `TimeoutError("upstream timeout")`를 던지면 wrapper deadline 메시지로 오표시되지 않는다.
5. 원래 예외의 `__cause__` 또는 명시적 error payload가 유지된다.

스레드 테스트는 시스템 전체 `threading.active_count()`의 정확한 값보다 executor `thread_name_prefix`를 기준으로 남은 worker를 세어 flakiness를 줄여 주세요.

---

## 3. P0-2 · `undeclared_mentions`가 인용문 안의 준용 조항을 오탐

### 재현 입력

근거 조항의 본문:

```text
회사는 제3조(보험금의 지급사유)에서 정한 사유가 발생한 경우…
```

설명문이 해당 근거를 인용하면서 같은 문장을 사용한 경우:

```text
제9조에 따라 보상됩니다. 다만 제3조에서 정한 지급사유를…
```

현재 결과:

```text
ok=False
undeclared_mentions=['제3조']
reason_code=undeclared_citation
```

### 영향 규모

제공된 s6 전량 실측에서 **204,098개 조항 중 90,428개(44.3%)**가 자기 번호가 아닌 `제N조`를 본문에 포함한다.

약관은 다른 조항을 준용하는 문장이 많다. 근거 원문을 정확히 인용했는데 그 안의 교차참조 번호를 별도 인용으로 요구하면 정상 답변이 대량 기권될 수 있다.

기권은 안전한 방향이지만 이 프로젝트의 원칙은 **근거를 댈 수 없을 때 기권**이다. 근거를 정확히 댔는데 검증기가 오탐해 폐기하는 것은 안전성 향상이 아니라 가용성 결함이다.

### 수정 요구

`answer_text` 전체에서 무조건 `제N조`를 수집하지 말고 다음 순서로 좁힌다.

1. cited handle/path를 실제 evidence에 해소한다.
2. quote가 해당 evidence 원문에 실제로 포함되는지 먼저 검증한다.
3. 그 quote가 `answer_text`에 나타나는 정확한 span을 찾는다.
4. **검증을 통과한 quote span 안의 조 번호만** undeclared 검사에서 제외한다.
5. quote 바깥에서 독립적으로 주장한 조 번호는 기존처럼 미선언 인용으로 실패시킨다.

다음처럼 넓게 풀면 안 된다.

- evidence 어디엔가 등장한 모든 조 번호를 허용
- quote payload에 들어오기만 하면 허용
- answer 전체에서 문자열 replace 후 위치 정보 무시
- `undeclared_mentions` 검사를 경고로 낮춤

### 회귀 테스트

| 경우 | 기대 결과 |
|---|---|
| 검증된 E001 quote 내부의 `제3조` | `undeclared_mentions`에 넣지 않음 |
| 같은 답변의 quote 밖에서 `제4조에 따라` 추가 주장 | `undeclared_citation` |
| quote가 evidence 원문에 없음 | `quote_not_in_source` |
| quote payload에는 있지만 answer에 실제 span이 없음 | 면제하지 않음 |
| 동일 문장이 quote 안팎에 각각 존재 | quote span만 면제, 바깥 mention은 검사 |
| 복수 quote와 중첩 가능 문자열 | span 단위로 결정론적으로 처리 |

---

## 4. P0-3 · `citation_guard.by_path`가 동일 경로 후보를 덮어씀

### 재현 결과

서로 다른 근거 두 개가 모두 다음 번호를 가진 경우:

```text
근거 A qualified_no = "제9조"  본문 A
근거 B qualified_no = "제9조"  본문 B
```

LLM이 `제9조`를 인용하면 현재 결과는 다음과 같다.

```text
ok=True
resolution=exact
candidates=0
matched=뒤에 색인된 본문 B
```

`by_no`는 list로 고쳤지만 `_index()`의 `by_path`가 여전히 `dict[str, EvidenceClause]`다. `_check_one()`이 `by_path`를 `by_no`보다 먼저 조회하므로 동일 정규화 경로의 마지막 근거가 앞 근거를 덮고 `exact`로 통과한다.

또한 제공된 그래프의 `_retrieve`가 `qualified_no`를 채우지 않아 `article_no`만 사용하면 `제9조`처럼 충돌 가능성이 가장 큰 형태로 evidence가 만들어진다.

### 영향 규모

제공된 s6 실측에서 **1,367문서 중 1,198문서(87.6%)**가 같은 `qualified_no`를 둘 이상 가진다.

손잡이 E001을 쓰면 정확히 구분할 수 있지만 검증기의 역할은 프롬프트 준수를 기대하지 않고 코드로 방어하는 것이다. LLM이 E001 대신 `제9조`를 출력해도 잘못된 조항이 `exact`로 통과해서는 안 된다.

### 수정 요구

- `by_path`도 단일 dict 값이 아니라 복수 후보를 보존한다.
- 후보 수는 문자열만이 아니라 `clause_id` 또는 occurrence 기준의 **논리 조항 수**로 판단한다.
- 동일 논리 조항의 여러 chunk라면 한 후보로 접을 수 있다.
- 서로 다른 조항이 동일 정규화 경로를 가지면 `Resolution.AMBIGUOUS`를 반환한다.
- `candidates`에는 사용자가 재지정하거나 디버깅할 수 있는 handle/path/clause identifier를 남긴다.
- `_retrieve`에서 가능한 경우 `qualified_no`, `clause_id`, page/occurrence 정보를 모두 보존한다.
- E001 손잡이로 인용하면 지정한 근거 하나만 `EXACT`로 해소한다.

### 회귀 테스트

1. 동일 `qualified_no="제9조"`, 서로 다른 `clause_id/text` 두 개 + `cited=["제9조"]` → `AMBIGUOUS`.
2. 위 입력에서 `cited=["E001"]` → E001이 가리키는 근거로 `EXACT`.
3. 같은 논리 조항의 여러 chunk → 한 후보로 접은 뒤 해소.
4. 전체 경로가 서로 다른 `보통약관/제9조`, `특별약관/제9조` → 각 전체 경로로 정확히 해소.
5. 존재하지 않는 전체 경로를 번호만으로 강등하지 않고 `UNKNOWN` 유지.
6. 충돌 후보의 순서를 바꿔도 결과와 reason code가 동일.

---

## 5. P0-4 · 계약 게이트를 실제 실행 경로에서 복구

### 5.1 문서 게이트

[06_계약_Agent.md](06_계약_Agent.md) §1은 `gate_document`가 `parse_status == "ok"`를 검사하고 아니면 `DOCUMENT_NOT_RELIABLE`로 기권하도록 규정한다.

제공된 그래프의 `_gate_document`는 항상 `True`를 반환해 노드 이름만 있고 게이트가 동작하지 않는다.

수정 요구:

- resolved policy/document에서 실제 extraction `parse_status`를 읽는다.
- `ok`가 아니거나 상태를 확인할 수 없으면 fail-closed로 기권한다.
- gate 실패 시 retrieve, assess, explain을 호출하지 않는다.
- 현재 s6에서 문서 상태와 조항별 `citation_eligible`이 분리돼 있으므로, 문서 gate와 조항 eligibility를 각각 적용한다.

### 5.2 0-근거 fail-closed

제공된 구현의 `verify_citations_in_message`는 `if not clauses: return True`로 근거 0건을 검증 통과시킨다.

프로젝트 정본 `app/workflow/precheck_graph.py`에는 다음 안전장치가 이미 있다.

- citations 0건이면 판정을 통과시키지 않는다.
- 이미 규칙엔진이 `NO_EVIDENCE` 등으로 기권했다면 원래 기권 사유를 보존한다.
- 인용이 필요한 비기권 결과에 근거가 없으면 `CITATION_UNVERIFIED`로 기권한다.

이 동작을 통합 후에도 유지한다.

회귀 테스트:

| 입력 | 기대 결과 |
|---|---|
| retrieve 0건 | assess/explain 미호출, `NO_EVIDENCE` 기권 |
| 비기권 verdict + citations 0건 | 응답 폐기, `CITATION_UNVERIFIED` 기권 |
| 이미 다른 이유로 기권 + citations 0건 | 원래 reason code 보존 |
| evidence 있음 + 검증 성공 | 정상 완료 |

---

## 6. P0-5 · 판정 그래프와 인용 검증을 정본 한 벌로 통합

### 현재 갈라진 상태

`langgraph_agent/graph/citation_guard.py`와 프로젝트의 `app/core/domain/citation_guard.py`는 점검 시 바이트 단위로 동일했다. 그러나 `precheck_graph.py`는 이미 서로 다른 안전장치를 갖도록 갈라졌다.

| 외부 브랜치에만 있는 것 | 프로젝트 정본에만 있는 것 |
|---|---|
| 실제 5노드 LangGraph 분해 | 0-근거 fail-closed |
| E001 손잡이 기반 explain/verify | 독립 clause store 재조회 |
| 설명문 파싱 | `clause_id` 정확 조회와 충돌 탐지 |
| build에 연결된 검증 재시도 | page 범위 검증 |
| main의 그래프B 전환 | quote가 저장소 원문에 포함되는지 검증 |
| 동일 evidence로 설명만 재생성 | 공통 조항 eligibility gate |

한쪽을 다른 쪽으로 덮어쓰면 상대가 가진 안전장치를 잃는다.

### 정본 위치

계약상 정본은 다음 두 파일이다.

- 그래프: `app/workflow/precheck_graph.py`
- 도메인 인용 검증: `app/core/domain/citation_guard.py`

[06_계약_Agent.md](06_계약_Agent.md) §6과 [11_AI_구조_지도.md](11_AI_구조_지도.md)는 이미 존재하는 정본을 두고 새 위치에 같은 판단 로직을 다시 만들지 말라고 규정한다. `tests/test_arch.py`의 ARCH-003도 같은 사실의 중복 정의를 막는다.

### 통합 요구

1. 외부 브랜치의 5노드 topology, handle interface, retry wiring을 `app/workflow/precheck_graph.py`로 이식한다.
2. 프로젝트의 `verify_against_store()`를 최종 검증 단계에서 유지한다.
3. `citation_guard` 로직은 `app/core/domain/citation_guard.py` 한 곳에서만 정의한다.
4. 외부 브랜치 파일이 필요한 경우 로직 복사본이 아니라 정본을 import하는 얇은 adapter/re-export만 둔다.
5. 제품 runtime은 정본 graph builder 한 개만 호출한다.
6. 규칙엔진이 verdict를 소유하고 LLM은 설명만 생성한다.
7. 검증 실패 재시도는 같은 세대·같은 승인 evidence 안에서 설명문을 최대 1회 수정하고, 근거 부족 표적 검색을 포함해 전체 재시도는 2회를 넘지 않는다.
8. 재시도 후에도 실패하면 설명 초안과 검증 실패 citation을 state와 응답 양쪽에서 제거하고 기권한다.

### 통합 완료를 확인하는 테스트

- product runtime에서 canonical graph가 호출되는지 확인
- 5개 노드의 trail과 실제 LangGraph 실행 순서 일치
- gate/retrieve 실패 시 뒤 노드가 호출되지 않음
- handle 검증 후 clause store 독립 대조가 모두 수행됨
- store에 없는 `clause_id`, page 불일치, quote 불일치가 각각 실패
- 같은 reason으로 무한 재시도하지 않음
- 최대 재시도 2회 초과 불가
- 검증 실패 초안이 최종 state/checkpoint에 남지 않음
- ARCH-003 및 handoff consistency 테스트 통과

---

## 7. P1 · 그래프 상태의 평문 질병기호

그래프B는 질병기호 hash만 state에 남기지만 그래프A는 도구 호출을 이유로 `disease_code` 평문을 state에 보존한다. state는 로깅·checkpoint 대상이 될 수 있어 [06_계약_Agent.md](06_계약_Agent.md) §1의 “상태에 원문 개인정보 저장 금지”와 어긋난다.

통합 후 제품 runtime에서 그래프A를 호출하지 않는 것만으로 끝내지 말고 다음 중 하나를 선택해 명시한다.

- 그래프A를 제거·폐기하고 테스트 전용에서도 정본 graph를 사용하거나
- 평문 코드는 직렬화·로그 대상 state 밖의 일회성 호출 인자로만 전달하고 state에는 hash만 유지

완료 기준:

- trace, checkpoint, debug state에 원문 질병기호가 없음
- 도구 호출 기능은 유지
- hash가 원문 식별자처럼 외부 응답에 과도하게 노출되지 않음

---

## 8. 하면 안 되는 수정

| 금지 | 이유 |
|---|---|
| 테스트를 통과시키기 위해 `undeclared_mentions` 전체를 warning으로 낮춤 | 미선언 인용 우회가 다시 열림 |
| `by_path` 충돌 시 첫째/마지막 후보 임의 선택 | 잘못된 근거가 exact로 승인됨 |
| E001 사용을 프롬프트에만 요구 | 프롬프트는 방어선이 아님 |
| citations 0건을 정상 검증으로 처리 | 근거 없는 판정 통과 |
| 저장소 재조회 없이 LLM에 넘긴 evidence로 자기검증 | 조작된 citation이 스스로 통과 |
| 문서 gate를 노드 이름·trail만으로 충족했다고 처리 | 실제 불량 문서가 차단되지 않음 |
| 그래프가 규칙엔진 verdict를 재해석 | Agent 계약 위반 |
| 자율 ReAct 또는 재시도 2회 초과 | 종료·감사·비용 계약 위반 |
| 두 디렉터리에 citation/graph 로직 복사 유지 | 다시 drift 발생 |

---

## 9. 제출 산출물

| 산출물 | 필수 내용 |
|---|---|
| 코드 변경 | P0-1~P0-5 전부, P1 처리 방향 명시 |
| citation 회귀 테스트 | `by_path` 충돌, handle exact, 준용 quote, quote 밖 미선언 |
| MCP 회귀 테스트 | 일반 예외/timeout/도구 TimeoutError/worker 정리 |
| graph 회귀 테스트 | 문서 gate, 0근거, 5노드, 재시도, store 대조 |
| 구조 테스트 | 정본 외 중복 로직 없음, ARCH-003 통과 |
| 실행 결과 | 관련 테스트와 전체 `pytest -q` 결과 |
| 짧은 변경 기록 | 수정 파일, 선택한 설계, 남은 위험 |

권장 테스트 명령:

```bash
# 프로젝트 정본
pytest -q tests/test_citation_guard.py tests/test_graph.py tests/test_arch.py tests/test_handoff_consistency.py

# 외부 브랜치에서 통합 전후 확인할 테스트
pytest -q langgraph_agent/tests/test_mcp_caller.py \
          langgraph_agent/tests/test_precheck_graph.py \
          langgraph_agent/tests/test_main.py \
          langgraph_agent/tests/test_router.py

# 마지막 전체 회귀
pytest -q
```

파일 이동·통합으로 테스트 경로가 달라지면 새 정본 경로로 옮기되, 위 시나리오 자체는 삭제하지 않는다.

---

## 10. 완료 조건(Definition of Done)

다음 조건을 모두 만족해야 완료다.

- [ ] 일반 예외 50회 후 executor worker가 누적되지 않는다.
- [ ] 도구 자체 `TimeoutError`와 wrapper deadline timeout의 오류 의미가 구분된다.
- [ ] 검증된 quote 안의 준용 조 번호는 미선언으로 오탐하지 않는다.
- [ ] quote 밖의 미선언 조항 주장은 계속 fail-closed다.
- [ ] 동일 경로의 서로 다른 조항은 `exact`가 아니라 `ambiguous`다.
- [ ] E001은 지정한 근거 하나로 정확히 해소된다.
- [ ] retrieve 결과에 `qualified_no`, `clause_id`, 위치 식별자가 가능한 범위에서 보존된다.
- [ ] `parse_status != ok` 문서는 retrieve 전에 기권한다.
- [ ] 근거 0건은 판정을 통과하지 않는다.
- [ ] 5노드 topology와 재시도 배선이 실제 runtime에서 실행된다.
- [ ] citation은 독립 clause store의 ID·쪽·quote와 대조된다.
- [ ] 그래프와 citation guard의 판단 로직이 정본 한 벌에만 존재한다.
- [ ] state/checkpoint/log에 평문 질병기호가 남지 않는다.
- [ ] 자율 ReAct 없음, 재시도 최대 2회, verdict 비재해석 계약을 유지한다.
- [ ] 관련 테스트와 전체 테스트가 통과한다.

---

## 11. 작업 결과 회신 형식

아래 형식으로 짧게 회신해 주세요.

```text
수정 파일:
정본으로 남긴 graph/citation 파일:
P0-1 executor 정리 방식:
P0-2 quote span 처리 방식:
P0-3 후보 유일성 기준:
문서 gate 데이터 출처:
0-근거 처리 결과:
그래프A 개인정보 처리:
추가한 회귀 테스트:
관련 테스트 결과:
전체 테스트 결과:
남은 위험 또는 후속 작업:
```

핵심은 테스트 수를 늘리는 것이 아니라, **어떤 입력도 조용히 잘못된 근거로 `exact` 통과하지 않고, 정상 근거도 교차참조 때문에 불필요하게 폐기되지 않도록 만드는 것**이다.

