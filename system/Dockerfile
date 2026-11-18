# Образ системы: собранная админка (React PWA) + бэкенд FastAPI, бот и воркер.
FROM node:22.11.0-alpine AS front
WORKDIR /front
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12.7-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FRONTEND_DIST=/app/frontend-dist \
    PERSONAL_DOCS_DIR=/personal_docs \
    MEDIA_DIR=/data/media
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
COPY --from=front /front/dist ./frontend-dist
RUN useradd -u 10001 -m app && mkdir -p /data/media/posters && chown -R app /data
USER app
EXPOSE 8000
# Миграции и начальное наполнение — при старте api; воркер их не запускает
CMD ["sh", "-c", "alembic upgrade head && python -m app.cli seed && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips='*'"]
