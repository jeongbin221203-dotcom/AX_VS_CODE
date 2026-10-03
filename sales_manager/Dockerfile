# 영업관리 시스템 — 앱 서버·워커 공용 이미지
#   docker build -t sales-crm .
#   앱:   docker run ... sales-crm                       (serve.py, 8000 포트)
#   워커: docker run ... sales-crm python manage.py worker
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    SALES_ENV=production SALES_HOST=0.0.0.0 SALES_PORT=8000 \
    SALES_LOG_STDOUT=1 SALES_LOG_FORMAT=json

# pg_dump/pg_restore (백업·복구), 한글 글꼴은 static/fonts 에 포함되어 있다
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --system --uid 10001 --home /app sales \
 && mkdir -p /app/data/logs /app/data/backups \
 && chown -R sales /app/data
USER sales

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1
CMD ["python", "serve.py"]
