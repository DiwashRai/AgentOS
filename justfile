# AgentOS task runner. `just check` is the single gate CI and agents run.
set windows-shell := ["pwsh", "-NoLogo", "-NoProfile", "-Command"]

default: check

# One-time (and after lockfile changes): install deps and enable the git hooks.
setup:
    cd backend; uv sync
    cd web; npm ci
    git config core.hooksPath .githooks

# Every gate, both halves.
check: check-backend check-web

# Backend gates: format, lint, types (incl. the worker on 3.11), architecture, tests.
[working-directory('backend')]
check-backend:
    uv run ruff format --check .
    uv run ruff check .
    uv run mypy
    uv run mypy --python-version 3.11 worker/worker.py
    uv run lint-imports
    uv run pytest

# Web gates: types, lint, format, tests.
[working-directory('web')]
check-web:
    npm run check

# Auto-fix formatting. Sessions use only their own half: fmt-backend or fmt-web.
fmt: fmt-backend fmt-web

[working-directory('backend')]
fmt-backend:
    uv run ruff format .
    uv run ruff check --fix .

[working-directory('web')]
fmt-web:
    npm run fmt

# Regenerate the web API types from the backend's OpenAPI schema (backend session only).
gen-api:
    cd backend; uv run python -m agentos.openapi_export ../web/src/api/openapi.json
    cd web; npm run gen:api

# Real backend on :8000 (one instance only). Short graceful shutdown so open SSE streams
# don't block --reload.
[working-directory('backend')]
dev-backend:
    if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { throw "port 8000 is already in use: another backend is running" }
    uv run uvicorn --factory agentos.main:create_app --reload --timeout-graceful-shutdown 2 --host 127.0.0.1 --port 8000 --app-dir server

# Frozen stub backend on :8001 for UI work, from a worktree at tag `stubs-v1`. It never changes
# while the real backend is being built.
dev-stubs:
    if (-not (Test-Path ../AgentOS-stubs)) { git worktree add ../AgentOS-stubs stubs-v1 }
    cd ../AgentOS-stubs/backend; uv run uvicorn --factory agentos.main:create_app --host 127.0.0.1 --port 8001 --app-dir server

# Web dev server against the real backend (:8000).
[working-directory('web')]
dev-web:
    npm run dev

# Web dev server against the frozen stubs (:8001).
[working-directory('web')]
dev-web-stubs:
    $env:AGENTOS_BACKEND_URL = 'http://127.0.0.1:8001'; npm run dev

# Fast gates used by the pre-commit hook, scoped to the staged half.
[working-directory('backend')]
pre-commit-backend:
    uv run ruff format --check .
    uv run ruff check .

[working-directory('web')]
pre-commit-web:
    npm run fmt:check
    npm run lint

# Point git at the repo's hooks (run once per clone; `just setup` does this).
setup-hooks:
    git config core.hooksPath .githooks
