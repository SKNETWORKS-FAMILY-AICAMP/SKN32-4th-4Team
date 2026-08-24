# 올바른 보험비서

진료비 내역의 KCD 질병기호와 가입 시점의 실손보험 약관을 비교해, 보장·면책 가능성과
근거 조항을 함께 안내하는 서비스입니다. 근거가 없거나 문서 판본을 확정할 수 없으면 임의로
답하지 않고 `확인 불가`로 처리합니다.

## 주요 기능

- 보험사·상품·가입일을 이용한 약관 판본 확인
- KCD 코드와 약관의 보장·면책 조항 대조
- 근거 조항·쪽수·인용문 검증
- FastAPI 고객·관리자·외부 Agent API
- PostgreSQL/pgvector 검색과 선택형 리랭킹
- PDF 좌표 추출 및 선별 OCR 전처리 도구
- 음성 상담과 얼굴 로그인용 선택 모듈

## 폴더 구조

```text
app/           API, 도메인 규칙, 검색·LLM 어댑터, 프론트엔드
config/        공개 가능한 실행 설정과 승인 릴리스 정보
db/            PostgreSQL migration과 저장소 구현
requirements/  역할별 의존성 목록
scripts/       서버 실행, 수집·전처리·색인·운영 명령
tests/         회귀·보안·계약 테스트
data/raw/manifests/  약관 본문이 없는 수집 메타데이터
```

## 개발 환경 실행

Python 3.12 이상이 필요합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements/runtime.txt
pip install -r requirements/dev.txt

Copy-Item config/development.env.example .env
python -m scripts.manage migrate
python -m scripts.run_customer_server
```

고객 화면은 `http://127.0.0.1:8080`, 관리자 서버는 별도 터미널에서
`python -m scripts.run_admin_server`로 실행한 뒤 `http://127.0.0.1:8081`에서 확인합니다.

`.env`의 `SECRET_KEY`는 반드시 각자 만든 긴 난수로 교체해야 합니다. OpenAI나 Gemini를
선택할 때만 해당 API 키를 로컬 `.env`에 넣고 Git에는 올리지 않습니다.

## 선택 설치

```powershell
# PDF 수집·전처리
pip install -r requirements/preprocess.txt

# 임베딩·리랭킹·색인
pip install -r requirements/index.txt

# 음성·얼굴 기능
pip install -r requirements/local-ml-optional.txt
```

전체 묶음이 꼭 필요한 환경에서만 `pip install -r requirements.txt`를 사용합니다.

## 데이터 안내

이 공개 저장소에는 보험약관 PDF, 약관 본문을 추출한 JSON, 벡터 DB, 모델 가중치,
사용자·에이전트 제출 데이터와 로컬 DB가 포함되지 않습니다. 이 자료는 저작권·개인정보·용량
문제로 Git에 올리지 않습니다.

따라서 코드는 실행할 수 있지만, 실제 약관 판정과 검색은 팀 내부에서 별도로 전달받은
구조화 약관 데이터 또는 승인된 PostgreSQL/pgvector DB를 연결해야 동작합니다. 데이터가
없을 때 서비스는 빈 결과를 꾸며내지 않고 준비되지 않았다는 오류를 반환합니다.

## 기본 검사

```powershell
python -m scripts.verify_public_repo
```

이 명령은 공개 금지 파일과 대표 비밀키가 섞이지 않았는지 확인하고, Python 문법 검사와
공개본만으로 실행 가능한 회귀검사를 차례로 수행합니다. 전체 테스트 중 실제 PostgreSQL,
LLM, GPU 모델, 내부 문서 또는 비공개 약관 데이터가 필요한 검사는 해당 환경을 준비한 뒤
별도로 실행합니다.

## 주의

이 서비스의 결과는 보험금 지급을 확정하는 법률·의학적 판단이 아닙니다. 실제 지급 여부는
보험사가 계약 내용과 제출 서류를 심사해 결정합니다.
