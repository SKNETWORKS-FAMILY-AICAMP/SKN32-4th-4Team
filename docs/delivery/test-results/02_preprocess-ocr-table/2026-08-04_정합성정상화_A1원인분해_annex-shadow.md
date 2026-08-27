> 공개 정리본 · 원본: docs/reports/2026-08-04_정합성정상화_A1원인분해_annex-shadow.md
> 이 문서는 실행 당시의 측정 기록입니다. 같은 항목의 문서가 여러 개면 날짜가 늦은 문서를 현재 결과로 읽으세요.

# 정합성 정상화 · A1 원인 분해 · annex shadow 결과

- 날짜: 2026-08-04
- accepted clause: `s6_pymupdf-1.28.0`
- serving 변경: **없음**
- 상태: 감사·shadow 산출물 완료

## 1. 정합성 138건의 정체

기존 재실행 결과는 T9 53건 + W1 85건 = 확정 위반 138건이었다. 데이터가 깨진 것이 아니라
생성기와 검사기의 표 부착 계약이 갈라진 것이 원인이었다.

- 생성기: `선` 표라도 `is_table=false` 또는 T9 본문 모양이면 제외
- 옛 검사기: 모든 `선` 표는 조항에 실려야 한다고 가정
- 옛 W1: `2열짝짓기`만 보류로 계산하고, 거절된 `선` 표는 보류에 포함하지 않음

`scripts.extract.table_signals.attachment_verdict()`를 단일 계약으로 만들고
`to_clauses`와 `consistency_check`가 함께 사용하도록 변경했다.

검증 결과:

| 검사 | 결과 |
|---|---:|
| 관련 테스트 | 46/46 통과 |
| consistency selftest | 25/25 통과 |
| consistency 확정 위반 | **0** |
| A2 검수 신호 | 2,239 / 775문서 |

## 2. 매니페스트 2,734건 불일치

기존 `manifest_s6`는 2026-08-03 02:44 UTC 기준이었다. 이후 페이지 전량이 08:06 UTC,
조항 전량이 13:10 UTC경 재생성돼 정확히 S5+S6 2,734개가 옛 해시와 달랐다.

기존 매니페스트는 다음 위치에 보존했다.

- `data/manifests/preprocess/archive/manifest_s6_pre-regeneration_2026-08-03.json`
- `data/manifests/preprocess/archive/manifest_s6_pre-regeneration_2026-08-03.sha256`

현재 산출물로 `manifest_s6`를 다시 만들고 검증했다.

| manifest | 문서 | 해시 불일치 |
|---|---:|---:|
| S6 | 1,367 | **0** |
| S7 | 1,367 | **0** |

## 3. A1 원인 분해

`between_covered` 7,993쪽 중 run 단위 원인 프록시는 다음과 같다.

| 프록시 | 페이지 |
|---|---:|
| 본문 손실 후보 | **6,836** |
| 부록 경계 후보 | 231 |
| 빈쪽·이미지 전용 | 662 |
| 법규 참고 | 89 |
| locator-only | 42 |
| 부분 도달 | 21 |
| 미분류 | 112 |

`content_loss_candidate`는 48자 anchor가 기존 조항·부록 본문에 25% 이하만 도달하고,
앞뒤에 유효 locator가 있는 페이지다. 자동 정답이 아니라 검수 우선순위다.

## 4. annex 참조 shadow

기존 s6 JSON을 변경하지 않고 별도 shadow 산출물로 전량 materialize했다.

| 항목 | 값 |
|---|---:|
| ok 문서 | 1,306 |
| 전체 괄호형 참조 | 11,490 |
| 해소 | 3,354 |
| 미해소 | 8,136 |
| 코드 언급 | 39,605 |
| exclude / mention | 19,394 / 20,211 |
| 조건부 참조 | 704 |
| 고유 owner | 1,428 |
| 복수 owner 후보 | 679 |
| quarantine 문서 | 1 |

삼성생명 `0fc2aef025b0`의 알려진 오연결은
`config/annex_ref_quarantine.json`으로 명시했다. resolved·unresolved·owner 모든 행은
`release_state=shadow`, `serving_eligible=false`다. 조건부 참조의 코드는 전부 `mention`으로
유지되는 것을 생성 중 검증한다.

산출물:

- `data/eval/annex_shadow_s6/resolved.jsonl` — 3,354행
- `data/eval/annex_shadow_s6/unresolved.jsonl` — 8,136행
- `data/eval/annex_shadow_s6/owners.jsonl` — 2,107행
- `data/eval/annex_shadow_s6/summary.json`

## 5. Claude CLI 교차검토

첫 검토는 5분 제한에서 타임아웃됐다. 범위를 줄인 재검토에서 5건을 제시했다.

반영:

- 유효하지 않은 locator를 coverage로 clamp하지 않음
- L3 자기검사를 범위 내 역전으로 고정
- T6 미측정 메시지를 `None개`가 아닌 명시적 사유로 변경
- quarantine 사유가 resolved 행에도 유지되는 테스트 추가

미반영:

- “조건부 참조인데 코드 mention이 0개면 오류” — 코드가 없는 부록을 참조하는 것은 정상적으로
  가능하므로 빈 mention 자체를 오류로 만들지 않았다. 조건부에서 exclude로 승격되지 않는지만
  fail-closed 검증한다.

## 6. 다음 승격 조건

1. A1 review240의 사람 라벨로 원인 프록시의 가중 결함률 산출
2. annex는 quarantine 제외 + 고유 owner + 비조건부 참조부터 end-to-end shadow diff
3. mention이 판정으로 새지 않고, exclude가 기존 판정을 어떻게 바꾸는지 코드별 회귀표 생성
4. 위 조건 전에는 accepted serving 경로에 annex를 연결하지 않음
