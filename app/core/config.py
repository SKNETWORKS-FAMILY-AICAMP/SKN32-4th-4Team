"""애플리케이션 설정 (단일 소스).

RULE.md 3.1(하드코딩 금지)에 따라 모델명·경로·키·DB 접속정보를 모두 여기로 모은다.
값은 .env 또는 환경변수에서 로드한다. 기본 프로바이더는 로컬 Gemma(llama-cpp-python
OpenAI 호환 서버)로, 빌드 중 외부 토큰을 소모하지 않는다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 프로젝트 루트: 이 파일은 app/core/config.py 이므로 parents[2] = 프로젝트 루트
ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """전 계층이 공유하는 설정값."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Development/tests keep their SQLite compatibility path. Production must
    # opt into the PostgreSQL cutover explicitly and is validated below.
    APP_ENV: Literal["development", "production"] = "development"

    # --- 브랜드 ---
    # 프로젝트명 단일 소스(RULE 3.1 하드코딩 금지). 프롬프트·API 타이틀이 전부 이 값을 참조한다.
    # 프론트엔드(정적 HTML)는 app/static/common.js의 BRAND_NAME 상수가 대응하는 단일 소스다.
    #
    # ★쓸 수 있는 이름은 둘뿐이다 — 프로젝트명 "올바른 보험비서", 팀명 "비서단".
    #   커머스 실습 시절의 옛 이름은 더 이상 쓰지 않는다(2026-08-04 정리).
    BRAND_NAME: str = "올바른 보험비서"

    # --- LLM 프로바이더 선택 ---
    # 실행 스크립트가 이 값을 덮어쓰지 않는다. `.env`에서 고른 provider가 실제 호출 경로다.
    LLM_PROVIDER: Literal["local", "openai", "gemini"] = "local"
    # 고객 용어 챗봇에서 검색된 약관 원문을 쉬운 말로 설명할 때 LLM을 실제로 호출한다.
    # false면 기존 원문 인용·고정 문구 경로만 사용한다.
    LLM_CHAT_ENABLED: bool = True
    LLM_REQUEST_TIMEOUT_SECONDS: float = 120.0
    LLM_HEALTH_TIMEOUT_SECONDS: float = 3.0

    # --- 모델 레지스트리(Phase 1) ---
    # 활성 모델 '프로필 선택자'(모델 ID 자체가 아니라 model_registry.yaml의 profile_id).
    # 실제 모델 ID·revision·checksum은 model_registry.yaml에서 해석한다(RULE 3.1: 소스 모델ID 금지).
    ACTIVE_MODEL_PROFILE: str = "local_gemma4_e4b"

    # --- 로컬(Gemma GGUF, OpenAI 호환 서버) ---
    # 앱 개발 서버(8000)와 충돌하지 않도록 모델 서버는 8002를 쓴다.
    LOCAL_BASE_URL: str = "http://127.0.0.1:8002/v1"
    LOCAL_MODEL: str = "gemma-4-e4b"
    LOCAL_API_KEY: str = "not-needed"

    # --- OpenAI ---
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # --- Gemini ---
    GOOGLE_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # --- pgvector (Phase 3, 학습 트랙) ---
    # 접속 정보(모델ID 아님) — userspace PG(conda pgv env). 미기동이면 접속 실패→명시 오류(무폴백).
    PGVECTOR_DSN: str = "host=127.0.0.1 port=5433 user=postgres dbname=mall_vec"

    # --- 합성 에이전트 트랙 저장소 ---
    # 테스트/최소 설치는 file, 운영 데모는 postgres를 명시적으로 선택한다.
    # postgres 선택 후 장애가 나면 파일로 폴백하지 않는다.
    DEMO_STORE_BACKEND: Literal["file", "postgres"] = "file"
    DEMO_PG_DSN: str = (
        "host=127.0.0.1 port=5433 user=postgres dbname=insurance_demo"
    )

    # --- 실제 보험 업무 원장(core/app/ops) ---
    # 비어 있으면 명시적으로 실패한다. SQLite DATABASE_URL이나 demo/file로 폴백하지 않는다.
    INSURANCE_PG_DSN: str = ""
    INSURANCE_ADMIN_PG_DSN: str = ""
    CLAUSE_STORE: Literal["file", "pg"] = "file"
    AUTH_PERSISTENCE: Literal["sqlite", "postgres"] = "sqlite"
    OPS_PERSISTENCE: Literal["sqlite", "postgres"] = "sqlite"
    SQLITE_LEGACY_ENABLED: bool = True
    PRECHECK_PERSISTENCE: Literal["off", "postgres"] = "off"
    OUTCOME_PERSISTENCE: Literal["file", "postgres"] = "file"
    VERIFIED_COHORT_STORE: Literal["file", "postgres"] = "file"
    AGENT_REAL_LEDGER_ENABLED: bool = False
    INSURANCE_IDEMPOTENCY_SECRET: str | None = None

    # --- 등록 외부 에이전트 API ---
    # 고객 UI와 다른 프로세스·포트·DB를 쓴다. 활성화해 놓고 DB가 끊겨도 공개 API나
    # 파일 저장소로 폴백하지 않고 503으로 실패한다.
    AGENT_API_ENABLED: bool = False
    AGENT_PG_DSN: str = (
        "host=127.0.0.1 port=5433 user=insurance_agent_runtime dbname=insurance_agent"
    )
    AGENT_ADMIN_PG_DSN: str = (
        "host=127.0.0.1 port=5433 user=insurance_agent_admin dbname=insurance_agent"
    )
    AGENT_BIND_HOST: str = "127.0.0.1"
    AGENT_PORT: int = 8082
    ALLOW_REMOTE_AGENT_BIND: bool = False
    # subject·요청·응답·trace의 keyed hash 전용. JWT SECRET_KEY와 재사용하지 않는다.
    AGENT_HASH_SECRET: str | None = None

    # --- 시뮬레이터가 두드릴 고객 웹 주소 ---
    # ★가상 에이전트가 **실제 HTTP 로** 붙게 한다. 관리 프로세스 안에서 저장소를 직접
    #   부르면 "에이전트가 접속해서 쌓는다"는 것이 시연되지 않고, 라우터·검증·멱등을
    #   전부 건너뛴 채 파일만 늘어난다. 서버가 안 떠 있으면 **명시적으로 실패**한다(무폴백).
    CUSTOMER_BASE_URL: str = "http://127.0.0.1:8080"

    # --- 전달 계층(Django BFF) 전환 스위치 ---
    # 계획: docs/plans/2026-08-25_0955_전달계층_Django분리_계획.md
    #
    # ★언제든 FastAPI 단독으로 되돌릴 수 있어야 한다. 기본값이 그 상태(`direct`)다.
    #   `direct` : 클라이언트 → FastAPI (지금 구조. L0 기준선)
    #   `django` : 클라이언트 → Django 전달 계층 → FastAPI (L1)
    #   구현이 없는 값으로 조용히 떨어지지 않게 Literal로 잠근다(무폴백).
    DELIVERY_MODE: Literal["direct", "django"] = "direct"
    DELIVERY_DJANGO_BASE_URL: str = "http://127.0.0.1:8000"

    # ★벤치 전용 엔드포인트(`GET /_bench/noop`)는 **기본 비활성**이다.
    #   프레임워크·전달 계층의 순수 오버헤드를 재려면 코어가 거의 0인 경로가 필요한데,
    #   그런 경로를 운영 표면에 상시 노출할 이유는 없다.
    BENCH_ENDPOINTS_ENABLED: bool = False

    #: ★`CLAUSE_STORE` 는 예전에 `os.getenv(...).strip().lower()` 로 읽혔다(2026-08-25 이전).
    #:   그래서 `CLAUSE_STORE=PG` 나 앞뒤 공백이 있어도 동작했다.
    #:   Settings 필드로 옮기면서 그 관용을 **말없이 없애면** 기존 환경이 기동에서 깨진다.
    #:   대소문자·공백만 흡수하고, 값 자체가 틀리면 Literal 이 그대로 막는다(무폴백).
    @field_validator("CLAUSE_STORE", "DELIVERY_MODE", mode="before")
    @classmethod
    def _normalize_choice(cls, v):
        return v.strip().lower() if isinstance(v, str) else v

    # --- 보험 조항 리랭킹 ---
    # 조항 임베딩 모델·revision·차원·접두사는 config/accepted_extraction.json의
    # embed_profile이 단일 진실원이다. 환경변수 기본값으로 덮어쓰지 않는다.
    RERANKER_PROVIDER: Literal["cross_encoder", "llm"] = "cross_encoder"
    # S6 fixed-candidate bake-off winner. BGE is the lower-latency fallback.
    RERANKER_MODEL: str = "Qwen/Qwen3-Reranker-4B"
    RERANKER_FALLBACK_MODEL: str = "BAAI/bge-reranker-v2-m3"
    RERANKER_DEVICE: str = "auto"
    RERANKER_DTYPE: Literal["auto", "float16", "bfloat16", "float32"] = "float16"
    RERANKER_BATCH_SIZE: int = 1
    RERANKER_MAX_LENGTH: int = 768
    RERANKER_OVER_FETCH: int = 20
    RERANKER_TRUST_REMOTE_CODE: bool = False

    # --- 보험 조항 리랭킹 ---
    #: 구형 커머스 `/api/rag` 스위치와 런타임은 격리했다. 이 스위치는 보험 조항 검색 전용이다.
    #:
    #: ★기본 꺼짐. 4B 리랭커를 요청 안에서 동기로 돌리는 것은 **프로토타입 한정**이다.
    #:   운영 기본값으로 켜려면 전용 워커·동시성 제한·타임아웃이 먼저 필요하다.
    INSURANCE_CLAUSE_RERANK_ENABLED: bool = False
    #: 리랭커에 넣을 후보 상한. 후보가 늘면 지연이 선형으로 는다.
    CLAUSE_RERANK_MAX_CANDIDATES: int = 40
    #: 동시에 도는 리랭킹 수. 4B 는 GPU 를 통째로 쓴다 — 겹치면 OOM 이다.
    CLAUSE_RERANK_CONCURRENCY: int = 1

    #: ★**채점에 무엇을 넣는가.** 실측으로 갈린 값이다(2026-08-05 · 417질의 · Qwen3-4B).
    #:
    #:     chunk       조각(`ClauseHit.text`)      hit@1 0.6379
    #:     full_clause 조 전체(`citable_text`)     hit@1 0.5875   ← 5.04%p 낮다
    #:
    #:   가장 크게 갈리는 곳이 **면책을 다른 말로 물었을 때**다(+19.81%p).
    #:   조 전체에는 여러 주제가 섞여 있어 면책 신호가 묻힌다.
    #:   `max_length` 를 768→1536 으로 올려도 이 차이는 **그대로다**(절단 탓이 아니다).
    #:
    #: ★그래도 **인용·판정은 조 전체를 본다.** 순위 매기기와 뜻 지키기는 다른 일이다 —
    #:   법률문은 예외가 뒤에 오므로(「…보상합니다. 다만 …」) 근거는 조 전체라야 한다.
    #:   → 리포트 `docs/reports/2026-08-05_0100_리랭커_붙는자리_실측.md`
    CLAUSE_RERANK_SCORE_BODY: Literal["chunk", "full_clause"] = "chunk"
    #: 조항 리랭킹 전용 절단 길이. 커머스 `RERANKER_MAX_LENGTH`(768)와 나눠 둔다.
    #: ★768 에서는 후보가 전부 같은 앞부분만 남아 `constant scores` 로 멈추는 질의가 있었다.
    #:   1536 에서 사라졌고, 조각 채점은 **지연이 늘지 않는다**(2,292 → 2,291ms).
    CLAUSE_RERANK_MAX_LENGTH: int = 1536
    #: 채점 프롬프트에 넣는 본문 길이 상한(자). 조 전체는 3만 자까지 있다.
    CLAUSE_RERANK_SCORE_CHARS: int = 1200

    #: ★리랭킹 시한(초). 넘으면 **504** 로 나간다.
    #:   실측(2026-08-05 · RTX 4070 SUPER · 후보 20 · Qwen3-4B): 질의당 2.3초.
    #:   30초는 그 열 배가 넘는 여유다 — 이보다 오래 걸리면 무언가 잘못된 것이다.
    #:
    #: ★★**시한이 지나도 추론은 안 멈춘다.** 파이썬 스레드는 강제 종료가 없고
    #:   torch 추론은 중간에 끊기지 않는다. 시한은 「이 요청을 포기한다」는 뜻이지
    #:   「계산을 멈춘다」가 아니다. 그동안 워커는 새 요청을 받지 않는다(503).
    #:   진짜 취소가 필요하면 별도 프로세스로 띄워 죽여야 한다 — 아직 안 했다.
    CLAUSE_RERANK_TIMEOUT_SECONDS: float = 30.0
    #: 워커 대기열 길이. 차면 503 — 쌓아 두면 밖에서 안 보인다.
    CLAUSE_RERANK_QUEUE_SIZE: int = 8

    #: ★리랭크 워커 방식 — `thread` | `process`
    #:
    #:   `thread`(기본)  모델을 스레드가 붙든다. 가볍지만 **시한이 지나도 추론을 못 멈춘다**
    #:                   (파이썬 스레드는 강제 종료가 없다). 계산이 끝날 때까지
    #:                   새 요청을 503 으로 거절한다 — 한 건이 막히면 한동안 멈춘다.
    #:   `process`       자식 프로세스가 붙든다. 시한이 지나면 **죽인다** — 실제로 멈춘다.
    #:                   OOM 도 자식만 죽고 서버는 산다.
    #:                   대가: 죽인 뒤 무게추를 **다시** 올린다(4B ≈ 수십 초).
    #:
    #: ★기본을 `thread` 로 두는 이유 — 죽이는 것은 싸지 않다. 시한 초과가 잦거나
    #:   OOM 이 실제로 관측될 때 `process` 로 바꾼다. 그 판단 근거는 메트릭에 있다
    #:   (`clause_rerank_jobs_total{result="timeout"}`).
    CLAUSE_RERANK_WORKER: Literal["thread", "process"] = "thread"

    #: ★메트릭 스크레이프 전용 토큰. **비어 있으면 그 경로가 열리지 않는다**(무폴백).
    #:
    #:   `/api/admin/metrics` 는 관리자 로그인을 요구해 Prometheus 가 그냥은 못 긁는다.
    #:   그렇다고 무인증으로 열면 운영 지표가 그대로 노출된다.
    #:   그래서 **읽기 전용 · 메트릭 전용** 토큰을 따로 둔다 —
    #:   관리자 계정과 분리돼 있어 이 값이 새도 다른 것은 못 한다.
    #:
    #: ★★토큰 비교는 `secrets.compare_digest` 로 한다. `==` 로 하면 앞자리부터
    #:   달라지는 지점이 응답 시간에 남아(타이밍 공격) 토큰을 한 글자씩 맞출 수 있다.
    METRICS_SCRAPE_TOKEN: str = ""

    #: ★사전판정 응답에 **참고 조항**을 붙일까(벡터 의미검색).
    #:
    #:   `/v1/prechecks` 는 지금까지 벡터 색인을 **한 번도 안 봤다.** 판정은
    #:   질병기호 대조로만 하고, 대부분의 답이 「면책엔 없다, 보상하는 사항이 정한다」로
    #:   끝나면서 **그 조항을 보여 주지 않았다.** 이 스위치가 그 자리를 메운다.
    #:
    #: ★★**켜도 판정은 안 바뀐다.** 유사도는 근거가 아니다 — 결과는 `related_clauses`
    #:   로만 나가고 `citations` 에 안 섞이며, 급도 `retrieved_clause` 로 따로 붙는다.
    #:
    #: ★기본이 꺼짐인 이유 — 판정 한 건마다 임베딩 1회 + 벡터 조회 1회가 늘고,
    #:   이 참고 조항이 실제로 도움이 되는지 아직 실측하지 않았다.
    PRECHECK_RELATED_SEARCH_ENABLED: bool = False
    #: 참고 조항을 몇 개까지 붙일까. 많이 붙일수록 화면에서 근거와 참고가 헷갈린다.
    PRECHECK_RELATED_SEARCH_LIMIT: int = 5

    # --- 음성 (Phase 11, STT/TTS) ---
    # GPU 미검출 환경(Codex 검토) — CPU + int8로 검증한 크기만 기본값으로 둔다.
    STT_MODEL: str = "small"
    STT_DEVICE: str = "cpu"
    STT_COMPUTE_TYPE: str = "int8"
    STT_LANGUAGE: str = "ko"
    # SAPI5 보이스 ID 부분 문자열로 찾는다(전체 ID는 OS마다 다름) — 없으면 ConfigError.
    TTS_VOICE_MATCH: str = "KO-KR"

    # --- PDF 한글 폰트 (정책 문서·관리자 보고서 공용) ---
    # 없으면 조용히 깨진 글자로 만들지 않고 ConfigError(무폴백). 비Windows는 이 경로만 바꾸면 됨
    # (예: /usr/share/fonts/truetype/nanum/NanumGothic.ttf).
    PDF_FONT_REGULAR: Path = Path("C:/Windows/Fonts/malgun.ttf")
    PDF_FONT_BOLD: Path = Path("C:/Windows/Fonts/malgunbd.ttf")

    # --- 얼굴 로그인 2차 인증 (Phase 13) ---
    # 검출·정렬: insightface RetinaFace(buffalo_l, 우수). 인식 임베딩: AdaFace IR-101(기본) —
    # 저품질 벤치마크(TinyFace/IJB-S) SOTA이고 이 프로젝트에서도 열화 이미지 매칭이 ArcFace(r50)보다
    # 전 항목 우위임을 실측(블러 k21 0.578→0.665, 저해상 0.15배 0.299→0.389). 라이브니스: Silent-Face.
    FACE_EMBED_MODEL: str = "buffalo_l"  # insightface 모델팩(검출·정렬·자세, 최초 사용시 자동 다운로드)
    # 인식 백엔드 선택(실측 근거로 기본=adaface):
    #   "adaface"   — AdaFace IR-101. 저품질(흐림·저조도·저해상) 최강. CPU 느림(~550ms/장).
    #   "lvface"    — LVFace-S(ViT). 일반/고품질 벤치마크 SOTA·빠름(~96ms)이나 저품질은 오히려 약함.
    #   "insightface" — buffalo_l r50. 중간·빠름(~100ms).
    # 웹캠 로그인은 저품질이 실제 조건이라 adaface가 이 용도에 최선(LVFace의 SOTA는 고품질 한정).
    FACE_RECOGNITION: str = "adaface"
    FACE_ADAFACE_ONNX: Path = ROOT_DIR / "data" / "models" / "adaface_ir101_webface12m.onnx"
    FACE_LVFACE_ONNX: Path = ROOT_DIR / "data" / "models" / "LVFace-S_Glint360K.onnx"
    FACE_ANTISPOOF_ONNX: Path = ROOT_DIR / "data" / "models" / "minifasnet_v2.onnx"
    FACE_ANTISPOOF_SCALE: float = 2.7  # Silent-Face 2.7 모델의 크롭 스케일(원본 파이프라인 기준)
    # onnxruntime 실행 프로바이더(EP). 인식 모델(AdaFace/LVFace)은 DirectML(Windows iGPU/GPU)이
    # 훨씬 빠름(실측 AdaFace 531ms→74ms, 7배). 설치가 CPU 전용(plain onnxruntime)이면 Dml은
    # 자동으로 제외되고 CPU로 돈다(_resolve_providers가 가용한 것만 남김). 라이브니스는 초소형
    # 모델이라 DirectML 오버헤드가 더 커서 CPU 고정.
    FACE_RECOG_PROVIDERS: list[str] = ["DmlExecutionProvider", "CPUExecutionProvider"]
    FACE_LIVENESS_PROVIDERS: list[str] = ["CPUExecutionProvider"]
    # 코사인 유사도 임계(정규화 임베딩). AdaFace 기준값(타인 유사도 ~0이라 여유 큼) — 실데이터
    # 튜닝 아님(문서화). insightface 백엔드로 바꾸면 이 값도 재튜닝 필요(관례상 ~0.35~0.40).
    FACE_MATCH_THRESHOLD: float = 0.30
    # 라이브니스 live 클래스 확률 임계(0~1). 라이브러리 기본 근사 — 실환경 검증 아님(문서화).
    FACE_LIVENESS_THRESHOLD: float = 0.50
    FACE_MAX_ATTEMPTS: int = 5  # 얼굴 2차인증 연속 실패 허용 횟수(초과 시 잠금, 데모: 인메모리)
    # 지식 바운티 L1 기계 검증 임계값(docs/plans/2026-07-22_2100_지식바운티_검증모델_재설계.md).
    # 인용 대조: 인용문이 색인 원문과 **문자 수준**으로 이만큼 일치해야 통과(의미 유사도 아님).
    #   → 실제 문서명 + 원문과 의미만 비슷한 허위 문장이 통과하던 결함을 막기 위함(Codex 지적).
    # 중복성: 기존 지식과 이 유사도 이상이면 중복으로 반려.
    # 업로드 크기 상한(모델 연산 DoS 표면 축소, Codex 지적). 초과분은 무폴백으로 ValidationErr.
    FACE_MAX_UPLOAD_BYTES: int = 8 * 1024 * 1024    # 얼굴 이미지 1장 상한(8MB)
    VOICE_MAX_UPLOAD_BYTES: int = 16 * 1024 * 1024  # STT 오디오 상한(16MB)
    # 청구 증거 파일(진료비 영수증·진단서 스캔본) 1건 상한(16MB) — 코덱스 리뷰 지적으로 신설.
    OBSERVATION_EVIDENCE_MAX_UPLOAD_BYTES: int = 16 * 1024 * 1024
    # ★1건 상한만으로는 누적 저장량을 못 막는다(코덱스 지적 — 익명 공개 엔드포인트라
    #   유니크 파일을 계속 올려 디스크를 채울 수 있음). 저장 전 디렉터리 총량을 잰다.
    OBSERVATION_EVIDENCE_MAX_TOTAL_BYTES: int = 2 * 1024 * 1024 * 1024  # 2GB
    # 등록 다중 샷 개수 상한(파일별 상한만으로는 개수를 늘린 합산 DoS를 못 막음 — Codex 지적).
    FACE_MAX_ENROLL_IMAGES: int = 10                # 등록 요청당 이미지 최대 장수(기본 샷 3의 여유배)

    # 품질 게이팅(Codex 권고): 저품질 입력을 조용히 통과시키지 않고 명시적 재촬영 요구(무폴백).
    # 등록(strict)이 검증(loose)보다 엄격 — 나쁜 기준 임베딩이 이후 매칭을 오염시키는 걸 막는다.
    # 임계는 112×112 정렬 얼굴 기준 시작값이며 실 genuine/impostor 분포로 튜닝 필요(문서화).
    FACE_QUALITY_REGISTER: dict[str, float] = {
        "min_blur": 100.0,   # 라플라시안 분산(낮으면 흐림)
        "min_bright": 45.0, "max_bright": 210.0,  # 정렬 crop 평균 밝기
        "min_face_px": 100.0,  # 원본 얼굴 폭(px)
        "max_yaw": 15.0, "max_pitch": 15.0,  # 정면 이탈 각(roll은 정렬로 보정되어 제외)
        "min_det": 0.60,  # 검출 신뢰도
    }
    FACE_QUALITY_VERIFY: dict[str, float] = {
        "min_blur": 60.0,
        "min_bright": 35.0, "max_bright": 220.0,
        "min_face_px": 80.0,
        "max_yaw": 25.0, "max_pitch": 20.0,
        "min_det": 0.50,
    }
    # CLAHE(대비 보정)는 정상광엔 오히려 임베딩을 흔들 수 있어(실측), 정렬 crop 평균 밝기가
    # 이 값 미만일 때만 luminance 채널에 약하게 적용(등록·검증 동일 파이프라인).
    FACE_CLAHE_BRIGHTNESS: float = 80.0
    FACE_CLAHE_CLIP: float = 2.0
    FACE_ENROLL_SHOTS: int = 3  # 등록 시 촬영 장수(품질통과분 임베딩 평균 — 견고성↑)

    # --- Lab (비용 추정) ---
    # 1M 토큰당 USD [input, output]. 로컬(local)은 과금 없음이라 미등록 → 비용추정 불가.
    PRICE_TABLE: dict[str, list[float]] = {
        "gpt-4o-mini": [0.15, 0.60],
        "gemini-2.5-flash": [0.075, 0.30],
    }

    # --- 경로 / DB ---
    DATA_DIR: Path = ROOT_DIR / "data"
    DB_DIR: Path = ROOT_DIR / "data" / "db"
    # CWD 의존을 피하기 위해 절대 경로 기반 (Codex 합의)
    DATABASE_URL: str = f"sqlite:///{(ROOT_DIR / 'data' / 'db' / 'insurance.sqlite3').as_posix()}"

    # --- 인증 (JWT) ---
    # SECRET_KEY는 하드코딩 금지: 미설정이면 auth 사용 시 ConfigError (RULE 3.1/3.2)
    SECRET_KEY: str | None = None
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

@lru_cache
def get_settings() -> Settings:
    """설정 싱글턴. 테스트에서 환경 변경 시 get_settings.cache_clear() 사용."""
    return Settings()
