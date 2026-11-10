# One image, one service: the React build is served by the FastAPI backend on Cloud Run.
# The same image runs the migration job (alembic) and the scheduled jobs (python -m app.jobs).
# Build:  docker build -t groundwork .      (or Cloud Build: infra/cloudbuild.yaml)

FROM node:20.18-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080 ENVIRONMENT=production FRONTEND_DIST=/app/frontend/dist
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
RUN pip install ".[postgres,gcp]" && find / -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null; true
COPY --from=web /web/dist /app/frontend/dist
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin groundwork
USER 10001
EXPOSE 8080
# One worker per instance (Cloud Run scales instances); SIGTERM drains in-flight requests for up to 20 s.
# Migrations run as a separate Cloud Run Job, never on container start.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips="*" --timeout-graceful-shutdown 20 --no-server-header
