# 테스트 결과 문서 모음

실제로 실행·측정·검증한 결과 문서 **103개**를 분야별로 모았습니다. 계획서와 단순 진행 메모는 제외했습니다.

## 읽는 방법

- 먼저 관심 분야의 README를 여세요.
- 같은 주제의 문서가 여러 개면 날짜가 늦은 문서를 현재 결과로 봅니다.
- `완료`라고 적혀 있어도 문서 안의 제한사항과 미검증 항목을 함께 확인합니다.
- 내부 PC 경로와 장비 주소는 공개용 표시로 바꿨으며, 시험 수치와 결론은 바꾸지 않았습니다.

## 분야별 목록

- [수집·문서 식별](01_collection-identification/README.md) — 6개
- [전처리·OCR·표](02_preprocess-ocr-table/README.md) — 31개
- [임베딩·검색·리랭커](03_embedding-search/README.md) — 13개
- [백엔드·DB·API·에이전트](04_backend-db-api/README.md) — 21개
- [LLM·파인튜닝·QA](05_llm-finetuning-qa/README.md) — 9개
- [얼굴 로그인·화면](06_face-ui/README.md) — 3개
- [판례·금감원·사람 검수](07_legal-human-review/README.md) — 6개
- [통합·배포·안전성](08_integration-release/README.md) — 14개

## 선별 기준

포함: 실제 실행, 실측, 성능평가, 회귀검증, 스모크테스트, 동등성시험, 전수감사 결과.

제외: 계획서, 설계 합의만 있는 문서, 단순 작업 배분, 원자료, 결함 재현에 필요한 내부 운영정보.

## 기준일

2026-08-27 KST 기준 `docs/reports/`의 최상위 Markdown 문서를 분류했습니다. `docs/reports/debugs/`는 결함 재현과 내부 경로가 많아 이 공개 묶음에서 제외했습니다.
