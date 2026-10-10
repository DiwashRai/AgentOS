# AgentOS task runner. `just check` is the single gate CI and agents run.
set windows-shell := ["pwsh", "-NoLogo", "-NoProfile", "-Command"]

default: check

# Every gate, both halves.
check: check-backend check-web

# Backend gates: format, lint, types, architecture, tests.
[working-directory('backend')]
check-backend:
    uv run ruff format --check .
    uv run ruff check .
    uv run mypy
    uv run lint-imports
    uv run pytest

# Web gates: types, lint, format, tests.
[working-directory('web')]
check-web:
    npm run check

# Auto-fix formatting and safe lint issues everywhere.
fmt: fmt-backend fmt-web

[working-directory('backend')]
fmt-backend:
    uv run ruff format .
    uv run ruff check --fix .

[working-directory('web')]
fmt-web:
    npm run fmt

# Regenerate the web API types from the backend's OpenAPI schema (backend owns this).
gen-api:
    cd backend; uv run python -m agentos.openapi_export ../web/src/api/openapi.json
    cd web; npm run gen:api

# Dev servers (run each in its own terminal).
[working-directory('backend')]
dev-backend:
    uv run uvicorn --factory agentos.main:create_app --reload --host 127.0.0.1 --port 8000 --app-dir server

[working-directory('web')]
dev-web:
    npm run dev

# Fast gates used by the pre-commit hook, scoped to the staged half.
[working-directory('backend')]
pre-commit-backend:
    uv run ruff format --check .
    uv run ruff check .

[working-directory('web')]
pre-commit-web:
    npx prettier --check .
    npx eslint . --max-warnings 0

# Point git at the repo's hooks (run once per clone).
setup-hooks:
    git config core.hooksPath .githooks
