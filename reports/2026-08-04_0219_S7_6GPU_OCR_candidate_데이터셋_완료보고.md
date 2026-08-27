# S7 6GPU OCR candidate 데이터셋 완료 보고

- 완료 시각: 2026-08-04 02:19 KST
- 상태: **shadow candidate 데이터셋 생성 완료**
- serving 상태: **미승인·차단 유지**
- 승인 전환: **금지** — 기존 S5↔S6 D1 드리프트 138건과 사람 검수가 남음

## 1. 결론

테스트용 OCR 벤치가 아니라 1,367문서 S7 산출물과 모델팀 인계 export까지 만들었다.

- OCR-hard occurrence 1,361쪽을 exact PNG SHA-256으로 832장까지 줄였다.
- x600 + RunPod 5대, 총 6GPU에서 MinerU2.5를 양자화 없이 실행해 832/832 성공했다.
- 동일 픽셀 529건만 대표 결과를 복원했고 유사 레이아웃에는 결과를 전파하지 않았다.
- 601개 대상 문서 중 573문서에서 자기부담금 candidate fact 4,562개를 생성했다.
- candidate ID 중복 0, serving/citation eligible 0이다.
- 금액·비율 토큰 8,296/8,296이 같은 페이지의 기존 native text에도 정확히 존재했다.
- 기존 S6 accepted 조항은 S7에서 204,098개 전량 동일했고 mismatch 0이다.
- 두 번째 S7 재빌드에서 page 1,367 + clause 1,367 전부 `unchanged`였다.
- S7 manifest와 SHA-256 sidecar를 만들고 1,367건 재검증에서 어긋남 0을 확인했다.

이 결과는 **후보 커버리지 0 → 4,562**의 개선이다. 하지만 serving 커버리지는 의도적으로
늘리지 않았다. 1,390개 `shadow_pass`도 사람 승인 전에는 판정·인용에 쓰지 않는다.

## 2. 완료 체크리스트

- [x] OCR-hard 1,361쪽 선별
- [x] exact image dedup 1,361 → 832
- [x] 6GPU 배분 누락·중복 0
- [x] MinerU2.5 832/832 성공
- [x] 모델 revision·환경·이미지 SHA 계보 고정
- [x] 6개 archive SHA-256 검증·회수
- [x] stale x600 결과 1건 검출·quarantine
- [x] exact alias 529건 복원
- [x] rowspan/colspan 격자 무결성 게이트
- [x] 휴리스틱 축 연결 provenance 명시
- [x] candidate fact 4,562개 생성
- [x] amount/rate native text 교차 대조 8,296/8,296
- [x] S7 page/clause 각 1,367건 결합·검증
- [x] 두 번째 결정성 재빌드 전량 unchanged
- [x] accepted clause mismatch 0
- [x] candidate serving/citation 누출 0
- [x] 파일 조회·인덱스 수집 candidate 격리 테스트
- [x] 모델팀 export 4종 생성
- [x] immutable manifest + sidecar 생성·재검증
- [x] 관련 회귀 테스트 87개 통과
- [x] Claude CLI 교차 검토와 지적 반영
- [ ] D1 기존 드리프트 T9 53 + W1 85 해소
- [ ] 3,172 review-required 및 1,390 shadow-pass 사람 검수·승인
- [ ] 승인 뒤 별도 릴리스 포인터 전환

## 3. 왜 기존 방식과 결과가 같지 않은가

이번 1,361쪽에는 `tables_coords` 선 기반 표가 0개였지만 PyMuPDF native table 객체와 본문
텍스트는 모두 있었다. 즉 OCR의 주 역할은 새 글자를 읽는 것이 아니라, 기존 업무 추출기가
놓친 표의 행·열 연결을 HTML grid로 다시 제안하는 것이었다.

| 항목 | 기존 S7 스켈레톤 | 이번 산출물 |
|---|---:|---:|
| candidate facts | 0 | 4,562 |
| candidate 보유 문서 | 0 | 573 |
| accepted clauses 변경 | - | 0 |
| serving/citation 추가 | 0 | 0 |
| 사람 검수 가능 원점 | 없음 | 문서·쪽·표 bbox·셀 origin·대표 OCR SHA |

따라서 이 작업은 “OCR을 전수 적용해 곧바로 정답으로 사용”한 것이 아니다. 기존 native
evidence를 우선 유지하면서, 누락 업무 fact를 검수 가능한 candidate layer로 추가했다.

## 4. GPU 실행 결과

모델은 `opendatalab/MinerU2.5-Pro-2605-1.2B`, revision은 여섯 장비 모두
`bff20d4ae2bf202df9f45284b4d43681555a97ed`였다.

| 장비 | GPU | 이미지 | 성공 | wall | 평균/쪽 | p95/쪽 | peak VRAM |
|---|---|---:|---:|---:|---:|---:|---:|
| x600 | RTX 4070 SUPER | 120 | 120 | 128.9분 | 64.5초 | 95.8초 | 2,856MB |
| RunPod 1 | RTX 2000 Ada | 139 | 139 | 68.2분 | 29.4초 | 48.0초 | 2,850MB |
| RunPod 2 | RTX 2000 Ada | 123 | 123 | 100.2분 | 48.8초 | 68.2초 | 2,856MB |
| RunPod 3 | RTX 4000 Ada | 122 | 122 | 110.2분 | 54.1초 | 77.1초 | 2,856MB |
| RunPod 4 | RTX 4000 Ada | 157 | 157 | 88.1분 | 33.6초 | 46.9초 | 2,845MB |
| RunPod 5 | RTX 2000 Ada | 171 | 171 | 116.3분 | 40.7초 | 64.6초 | 2,845MB |

가장 늦은 x600 기준 순수 추론 wall은 2시간 9분이었다. 처음 예상한 36.6 GPU시간 전수
OCR 대신, hard filter + exact dedup + 6GPU 분산으로 마감 범위 안에 끝냈다.

### Paddle 양자화를 쓰지 않은 이유

최종 생산 경로는 shadow48 실측 승자인 MinerU2.5를 사용했다. peak VRAM이 약 2.9GB라 모든
장비에 BF16/FP16 모델이 충분히 들어갔고, 양자화로 얻을 메모리 이득보다 숫자·표 구조 회귀와
추가 검증 비용이 컸다. Paddle 계열 양자화 자체가 불가능한 것은 아니지만 이번 마감에서는
정확도 계약이 확인된 비양자화 경로가 더 안전했다.

## 5. OCR·별칭 계보

- occurrence: 1,361
- unique PNG SHA-256: 832
- saved inference: 529
- 최대 전파 조건: **렌더된 PNG byte SHA-256 완전 일치**
- 유사 레이아웃·문서 계열·같은 보험사라는 이유의 전파: 0
- exact alias로 만들어진 candidate: 1,911
- 모델 revision: 4,562 candidate 전량 동일

x600 회수본에는 최종 manifest 밖의 이전 배치 JSON 1개가 있었다. 병합기가 이를 거부했고,
파일은 삭제하지 않고 아래 quarantine에 보존했다.

`data/work/s7_ocr_dedup6_remote/quarantine/dedup6_x600_unexpected/8dd9caaed194_p0011.json`

## 6. candidate 품질 결과

| 지표 | 결과 |
|---|---:|
| candidate facts | 4,562 |
| ID unique | 4,562 |
| candidate 보유 문서 | 573 |
| 대상이지만 candidate 0인 문서 | 28 |
| `shadow_pass` | 1,390 (30.47%) |
| `review_required` | 3,172 (69.53%) |
| 금액 토큰 native exact | 4,172 / 4,172 |
| 비율 토큰 native exact | 4,124 / 4,124 |
| 전체 숫자 토큰 native exact | 8,296 / 8,296 |
| invalid table bbox | 0 |
| serving eligible | 0 |
| citation eligible | 0 |

검수 사유는 중복 집계다.

| 사유 | candidate |
|---|---:|
| `missing_plan` | 2,858 |
| `page_boundary_continuation` | 1,538 |
| `known_domain_ocr_suspect` | 238 |
| `missing_service` | 170 |
| `non_korean_cjk` | 7 |

금액·비율 100% native exact는 OCR이 무조건 옳다는 뜻이 아니다. 적어도 OCR이 새 숫자를
지어낸 경우는 이번 비교에서 없었다는 뜻이다. 최종 축 소속과 페이지 경계는 사람이 승인한다.

## 7. Claude CLI 교차 검토와 반영

Claude는 다음 위험을 지적했다.

1. rowspan/colspan 확장 시 값 오전파
2. 숫자 OCR 정확도 게이트 부재
3. native table이 있는데 재OCR한 범위의 의미 불명확
4. 축 연결 추정과 원문 사실의 구분 부족
5. 분모 없는 `review_required` 보고
6. candidate 격리 플래그를 실제 serving 경로에서 실증할 필요
7. 스켈레톤 재빌드가 accepted evidence를 바꾸는 위험

반영 결과:

- grid의 ragged row와 span mismatch를 계산하고 실패 후보를 `review_required`로 내린다.
- `axis_binding.method`, `association_inferred=true`, `value_invention=false`를 기록한다.
- 4,562 candidate 전체에서 amount/rate 8,296개를 native text와 대조했다.
- 대상 1,361쪽이 “텍스트 없음”이 아니라 “업무 fact missed” 층임을 이 보고서에 정정했다.
- validation status와 사유를 전체 4,562 분모로 index에 기록했다.
- 파일 조회와 clause index collector가 `candidate_facts[]`를 무시하는 테스트를 추가했다.
- S6 accepted clauses를 S7과 전량 비교해 mismatch 0을 확인했다.
- 승인 포인터 `config/accepted_extraction.json`은 S6 Arctic-ko 릴리스 그대로 유지했다.

## 8. S7 빌드·export

### 구조화 산출물

- page: `data/extracted/*/s6_hybrid-table-v1/*.json` 1,367
- clause: `data/structured/*/s7_hybrid-table-v1/*.clauses.json` 1,367
- candidate facts: 4,562
- 1차 결합: page/clause 573개씩 변경, 794개씩 unchanged
- 2차 결합: page 1,367 + clause 1,367 전부 unchanged

page mode는 accepted/native 우선순위를 유지했다.

- native layout: 61,464쪽
- verified line grid: 777쪽
- text only: 100,437쪽
- OCR candidate가 native evidence를 대체한 쪽: 0

### 모델팀 shadow export

경로: `data/exports/dataset/s7_hybrid-table-v1/`

- `clauses.jsonl`: 고유 본문 73,973, 239.5MB
- `occurrences.jsonl`: 발생 213,440, 123.1MB
- `candidate_facts.jsonl`: candidate 4,562, 11.8MB
- `manifest.json`: `is_shadow=true`, `release_id=shadow-s7_hybrid-table-v1`

candidate JSONL은 사람 검수 입력이고 clauses 임베딩·serving 입력과 분리돼 있다.

## 9. 검증 결과

### 통과

- S7 build verify: problems 0
- S7 두 번째 deterministic rebuild: 2,734파일 unchanged
- S7 immutable manifest: 1,367문서, 대조 어긋남 0
- accepted clauses S6↔S7 mismatch: 0
- candidate ID source↔S7 exact set match
- candidate full document SHA match
- serving/citation 누출: 0
- 관련 pytest: 87/87 통과
- D1 검사 selftest: 25/25 통과
- `git diff --check`: 오류 0

### 승인 차단 이슈 — 기존 D1 드리프트

현재 S5↔S6 전량 D1에서 아래 확정 불일치가 재현됐다.

- T9: 신뢰 선 표가 조항 범위 안인데 S6에 미실림 53건 / 51문서
- W1: 보류 표 수가 S5 실측과 S6 stats에서 다름 85건 / 85문서
- 합계: 138건
- A2 검수 신호: 2,239건 / 775문서

8월 3일 기존 D1 보고서는 T9/W1 0건이었다. 현재 `manifest_s6 --verify`도 S5 1,367 +
S6 1,367, 총 2,734파일이 당시 manifest와 달라졌다고 보고한다. 따라서 기준선 산출물이
manifest 생성 뒤 전량 재생성된 상태다.

이번 S7 builder는 현재 S6 조항을 그대로 복제했고 accepted clause mismatch 0이므로 이 138건을
새로 만들지는 않았다. 하지만 기존 결함을 상속하므로 사람 검수만 끝나도 바로 승인할 수는 없다.
S5/S6를 같은 코드·입력으로 다시 동결하고 D1=0을 회복한 뒤 S7을 재빌드해야 한다.

## 10. 해시와 재현성

- MinerU revision: `bff20d4ae2bf202df9f45284b4d43681555a97ed`
- alias map: `ee9df3269a122e26da32c780e182536f5e7c30a30b87a9122828bd17968e25d1`
- merged summary: `1a6e005fc1b5092f214d3edbfe203aa9ceaba9767b4260507f635b829b403661`
- candidate index: `db38d62f9c7b68675a3a5ee50f818a66c1e8d1aaa0dda63998742116a2c13ff9`
- candidate audit: `8493e0ebae394e24794bf4bad50b5fa87f64848ac47cda85b0d676989ae533d6`
- candidate payload tree: `f27fe433a732b23e0257e7fe50c7f43dce68a3bf5660630b7fa8f3a24a57fce9`
- S7 page tree: `981499e5929acfe9acd2e9f482f381c779b2a889335b12c85451df77f49eb7c6`
- S7 clause tree: `35d32b03918667ec9e463ff37c4c97ddd234fe1ae23292da5093bbca9db49a00`
- S7 manifest: `0bde904e06a12b541188f38dbda6fabcb598988c04eb210b8af6fe1005c93287`
- candidate export JSONL: `e2f3e4dd5ce69f7b239f411d7dae8a7142c00af270866b0f2918abf5cb215674`

OCR archive:

| archive | SHA-256 |
|---|---|
| x600 | `8eec532bda23ee7702a1f3e873ee83a2f4c6ce0b239541e144a17d0849bf95f6` |
| RunPod 1 | `2a6288291958d18a0f6d58ca81b365e1b1fc1a2e725c3c4f071c9b9b455ba9b5` |
| RunPod 2 | `48e9b0444aec246c031e341b1dc361f69440f1e1229b67e2e3283daa4e308f94` |
| RunPod 3 | `98d000bc6c287d8e7ccf1cc007e984d2b29b1e1447586fd3437071549234274b` |
| RunPod 4 | `812bda2d01d7e70493938ba7359431aa2bfc3d16f0e259b13a9c2d3527f9a7a1` |
| RunPod 5 | `dd96f8b9a25ec31951b6b527528af8bc1367c20b68eaf485460f3fc618923c5d` |

## 11. 주요 산출물

- 계획: `docs/plans/2026-08-03_2208_S7_실사용_데이터셋_생성계획.md`
- OCR 병합: `data/work/s7/ocr_dedup6_merged/`
- candidate payload: `data/candidates/s7_selfpay/`
- 품질 감사: `data/eval/s7_candidate_quality_summary.json`
- S7 page: `data/extracted/*/s6_hybrid-table-v1/`
- S7 clause: `data/structured/*/s7_hybrid-table-v1/`
- 모델팀 export: `data/exports/dataset/s7_hybrid-table-v1/`
- immutable manifest: `data/manifests/preprocess/manifest_s7.json`
- OCR merge 코드: `scripts/eval/merge_s7_ocr_dedup6.py`
- axis binder: `scripts/eval/selfpay_axis_binder.py`
- S7 audit: `scripts/eval/audit_s7_candidates.py`
- S7 builder: `scripts/extract/build_s7_hybrid.py`
- reranker 실측: `docs/reports/2026-08-04_S6_Arctic-ko_리랭커5종_실측과_운영반영.md`

## 12. 승인 전 다음 작업

1. S5/S6 기준선 2,734파일 드리프트의 생성 명령과 코드 revision을 확정한다.
2. 같은 frozen PDF로 S5→S6를 한 번에 재생성하고 D1 T9/W1을 0으로 되돌린다.
3. 그 S6를 입력으로 S7을 재빌드하고 manifest·tree digest를 다시 발행한다.
4. 1,390 shadow-pass부터 보험사·조판·플랜·페이지경계로 층화 검수한다.
5. 3,172 review-required는 결측 축을 원문에서 보완하거나 reject한다.
6. 승인된 fact만 별도 `accepted facts` 릴리스로 승격한다.
7. 그 뒤에만 승인 포인터와 Arctic-ko 임베딩/Qwen3-Reranker-4B 운영 색인을 연결한다.

현재 단계의 올바른 판정은 **“실사용 가능한 검수 후보 데이터셋 완성, 자동 serving 릴리스는 아직 아님”**이다.
