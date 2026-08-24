# 의존성 그룹

루트 `requirements.txt`는 기존 설치 명령을 깨지 않기 위해 아래 그룹을 모두 포함한다.

```bash
pip install -r requirements.txt
```

역할별 최소 환경은 필요한 그룹만 명시적으로 설치한다.

| 그룹 | 범위 | 설치 예 |
|---|---|---|
| `runtime.txt` | FastAPI·인증·PostgreSQL·LangGraph·MCP | `pip install -r requirements/runtime.txt` |
| `preprocess.txt` | PDF 문서 수집·추출과 OCR 입력 준비 | `pip install -r requirements/preprocess.txt` |
| `index.txt` | 임베딩·리랭커 평가·적재 | `pip install -r requirements/index.txt` |
| `local-ml-optional.txt` | 음성·얼굴 선택 기능 | 해당 런타임에서만 설치 |
| `dev.txt` | pytest·API 테스트 | `pip install -r requirements/dev.txt` |

전처리·인덱스 작업은 `runtime.txt`를 공통으로 먼저 설치한 뒤 해당 그룹을 더한다. 선택 그룹을 설치하지 않은 환경에서는 관련 기능을 조용히 폴백하지 말고 명시적 설정/의존성 오류로 취급한다. 대규모 OCR 모델 비교, 시각화, 파인튜닝처럼 GPU나 연구 전용 패키지가 필요한 스크립트는 실행 환경에 맞는 별도 환경을 사용한다.
