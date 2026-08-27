# customer/admin/migrate 공용 이미지.
# 실행 역할은 docker-compose.yml의 command:로 정한다(이 이미지 자체는 특정 역할을 CMD로
# 고정하지 않는다) — scripts/run_customer_server.py / run_admin_server.py는
# host="127.0.0.1"로 고정돼 있어 컨테이너 밖에서 접속이 안 되므로 그 스크립트 대신
# uvicorn을 직접 호출한다(docs/plans/2026-08-25_1727_dockerize_ec2_배포_계획.md 참고).
FROM python:3.12-slim

# fonts-nanum: PDF 생성(reportlab)이 한글 폰트를 요구한다(app/core/config.py의
#   PDF_FONT_REGULAR/PDF_FONT_BOLD 기본값은 Windows 경로라 아래에서 덮어쓴다).
# ca-certificates: OpenAI 계열 SDK가 아니어도 각종 HTTPS 호출(HF 모델 다운로드 등)에 필요.
# libpq-dev는 넣지 않는다 — psycopg[binary]가 wheel에 libpq를 포함한다.
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-nanum \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 실제 설치된 폰트 파일명은 빌드 후 다음으로 확인했다: dpkg -L fonts-nanum | grep '\.ttf$'
ENV PDF_FONT_REGULAR=/usr/share/fonts/truetype/nanum/NanumGothic.ttf \
    PDF_FONT_BOLD=/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 의존성 설치를 먼저 해서 코드만 바뀔 때 레이어 캐시를 살린다.
# ★전달 계층은 선택 설치다. 기본 false를 유지해 customer/admin/migrate 같은 FastAPI
# 단독 이미지에 Django를 강요하지 않고, compose의 delivery 빌드만 true로 명시한다.
ARG INSTALL_DELIVERY=false
COPY requirements/runtime.txt requirements/delivery.txt requirements/
RUN pip install --no-cache-dir -r requirements/runtime.txt \
    && if [ "$INSTALL_DELIVERY" = "true" ]; then \
         pip install --no-cache-dir -r requirements/delivery.txt; \
       elif [ "$INSTALL_DELIVERY" != "false" ]; then \
         echo "INSTALL_DELIVERY must be true or false" >&2; \
         exit 64; \
       fi

COPY . .

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# 문서 목적의 기본값 — 실제 실행 커맨드는 docker-compose.yml의 command:가 덮어쓴다.
CMD ["python", "-m", "scripts.manage", "ready"]
