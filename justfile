# AgentOS task runner. `just check` runs every check; `just gates` runs the gates.
set windows-shell := ["pwsh", "-NoLogo", "-NoProfile", "-Command"]

default: check

# One-time (and after lockfile changes): install deps and enable the git hooks.
setup:
    cd backend; uv sync
    cd web; npm ci
    git config core.hooksPath .githooks

# Every check: both halves and the gate scripts' own.
check: check-backend check-web check-tools

# Backend checks: format, lint, types (incl. the worker on 3.11), architecture, tests.
[working-directory('backend')]
check-backend:
    uv run ruff format --check .
    uv run ruff check .
    uv run mypy
    uv run mypy --python-version 3.11 worker/worker.py worker/agentos_worker
    uv run lint-imports
    uv run pytest -n auto

# Web checks: types, lint, format, tests.
[working-directory('web')]
check-web:
    npm run check

# The gate scripts' own checks (tools/gates, run in the backend's environment).
check-tools:
    uv run --project backend ruff format --check tools/gates
    uv run --project backend ruff check tools/gates
    uv run --project backend mypy --strict tools/gates
    uv run --project backend pytest -q -p no:randomly -p no:cacheprovider tools/gates

# Every gate (measured limits on how the code grows; tools/gates/gates.py). Fails listing each
# offender as path:line  name  score > limit. Raises are checked against HEAD's baseline; pass
# `--base REF` to check against the merge base with REF instead.
gates *args:
    uv run --project backend python tools/gates/gates.py {{ args }}

# The gates for a branch: raises checked, and new `raises` entries listed, against its merge base.
gates-pr base="origin/cleanup":
    uv run --project backend python tools/gates/gates.py --base {{ base }}

# Markdown for a PR description: the gate numbers before (the baseline at the merge base with
# `base`) and after (the working baseline, ratcheted), what improved, what got worse, the raises.
card base="origin/cleanup":
    @uv run --project backend python tools/gates/card.py --base {{ base }}

# Lower quality-baseline.json where the code improved (never raises it). Commit it with the change.
ratchet:
    uv run --project backend python tools/gates/gates.py --ratchet

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

# Real backend on :8000 (one instance only). Short graceful shutdown so open connections
# don't block --reload.
[working-directory('backend')]
dev-backend:
    if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { throw "port 8000 is already in use: another backend is running" }
    uv run uvicorn --factory agentos.main:create_app --reload --timeout-graceful-shutdown 2 --host 127.0.0.1 --port 8000 --app-dir server

# Web dev server against the real backend (:8000).
[working-directory('web')]
dev-web:
    npm run dev

# Fast checks used by the pre-commit hook, scoped to the staged half.
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

# ---- Container: the server only (API + built SPA), as deployed. See backend/deploy/README.md.
# Docker by default; Podman: `just engine=podman container-build` (or set CONTAINER_ENGINE).
# Every variable below can be overridden the same way, e.g. `just container_port=9000 container-run`.
engine := env("CONTAINER_ENGINE", "docker")
container_image := "agentos:latest"
container_name := "agentos"
container_volume := "agentos-data"
container_port := "8080"
container_env := "backend/deploy/.env.container"

# Build the image from the working tree. Podman needs --format docker to keep the HEALTHCHECK.
container-build:
    {{ engine }} build {{ if engine == "podman" { "--format docker " } else { "" } }}-f Containerfile -t {{ container_image }} .

# /data on a named volume (db + settings), secrets from the env file. Replacing the container
# keeps the volume, so this is also the upgrade step (after container-build).
# (Re)start the container on host port 8080.
container-run:
    if (-not (Test-Path '{{ container_env }}')) { throw "{{ container_env }} not found: copy backend/deploy/container.env.example and fill it in" }
    -{{ engine }} rm --force {{ container_name }} 2>$null
    {{ engine }} run --detach --name {{ container_name }} --restart unless-stopped --publish {{ container_port }}:8000 --volume {{ container_volume }}:/data --env-file '{{ container_env }}' {{ container_image }}

# Follow the container's logs.
container-logs:
    {{ engine }} logs --follow {{ container_name }}

# Stop and remove the container. The data volume stays.
container-stop:
    {{ engine }} stop {{ container_name }}
    {{ engine }} rm {{ container_name }}
