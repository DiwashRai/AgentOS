# AgentOS server image: the API + the built SPA. No agent CLIs, no git credentials: workers run
# on runners (GitHub Actions / self-hosted), never in this container. Works with Docker and Podman.
#   just container-build                  (docker)
#   just engine=podman container-build    (podman; adds --format docker so HEALTHCHECK is kept)
# Run/upgrade notes: backend/deploy/README.md.
#
# Images are fully qualified (docker.io/...) so Podman never has to resolve a short name.

# ---------------------------------------------------------------- 1. web: build web/dist
FROM docker.io/library/node:24-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------------------------------------------------------------- 2. deps: the locked venv
FROM docker.io/library/python:3.12-slim AS deps
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
# The server runs from source (--app-dir server), so only the dependencies are installed.
RUN uv sync --frozen --no-dev --no-install-project

# ---------------------------------------------------------------- 3. runtime
FROM docker.io/library/python:3.12-slim
# A fixed numeric uid/gid: volume ownership stays stable across rebuilds (and engines).
RUN groupadd --gid 10001 agentos \
 && useradd --uid 10001 --gid agentos --no-log-init --home-dir /home/agentos --create-home \
      --shell /usr/sbin/nologin agentos \
 && mkdir -p /data \
 && chown agentos:agentos /data

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    AGENTOS_HOST=0.0.0.0 \
    AGENTOS_PORT=8000 \
    AGENTOS_DATA_DIR=/data \
    AGENTOS_WEB_DIR=/app/web

# The layout mirrors backend/: config.py finds (and /api/worker/worker.py serves)
# ../../worker/worker.py relative to the agentos package.
WORKDIR /app
COPY --from=deps /app/.venv /app/.venv
COPY backend/worker/worker.py worker/worker.py
COPY backend/server/agentos server/agentos
RUN python -m compileall -q server/agentos worker
COPY --from=web /src/web/dist web

USER 10001:10001
VOLUME /data
EXPOSE 8000

# No curl in the image: python's urllib does the probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os, urllib.request as u; u.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('AGENTOS_PORT', '8000'), timeout=4)"

# Migrations (with an automatic backup of an existing db) run in the app's lifespan.
# A short graceful shutdown so open connections don't hold up a stop.
CMD ["sh", "-c", "exec uvicorn --factory agentos.main:create_app --app-dir server --host \"$AGENTOS_HOST\" --port \"$AGENTOS_PORT\" --timeout-graceful-shutdown 5"]
