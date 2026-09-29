# Stage 1: build the React UI.
FROM node:22-alpine AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build

# Stage 2: FastAPI runtime serving the API, the session WebSocket and the built UI (one origin).
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    MIRROR_DATA_DIR=/data MIRROR_STATIC_DIR=/app/web PORT=8000
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=web /web/dist ./web
# A fixed non-root uid (1000) works on Hugging Face Spaces, Render and plain Docker.
RUN useradd --create-home --uid 1000 mirror && mkdir -p /data && chown mirror /data
USER mirror
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/v1/health' % os.environ.get('PORT', '8000'), timeout=2)"
# Hosts such as Render tell the app which port to listen on through $PORT.
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
