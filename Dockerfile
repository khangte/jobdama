# jobdama: 채용공고 링크 → 노션 저장 웹
# 빌드:  docker compose build
# 실행:  docker compose up -d   (http://localhost:8765)
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    PATH=/opt/venv/bin:$PATH

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app

# 의존성 먼저 설치(코드만 바뀌면 이 단계는 캐시를 재사용)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache

# Headless Shell과 실행에 필요한 시스템 패키지(폰트 포함)
RUN playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app

# WSL 기본 사용자와 같은 UID로 실행해 ./snapshots 마운트 폴더에 쓸 수 있게 한다
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/snapshots \
    && chown -R app:app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request as u; u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:8000/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
