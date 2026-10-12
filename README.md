# AgentOS

AgentOS is a web app for running coding agents on tickets. You create a ticket (or import one from Jira), shape its context files (an objective, a plan, a log) and its stage pipeline, then dispatch it. A disposable worker runs the stages with an agent CLI (`claude` by default) on a branch, syncs the context files back, and stops at a gate so a person can read the plan, edit the context and continue. The last stage opens a PR. Every run stays inspectable and repeatable because the context lives with the ticket rather than inside one long chat session.

**The loop:** create → dispatch → stage(s) → gate → edit context → continue → PR.

## Prerequisites
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for the server), Node 24 + npm, [just](https://just.systems/), PowerShell 7 (`pwsh`, the justfile's shell) and git.
- For real runs: an agent CLI on the worker host (`claude`) and `gh` (the worker uses it to find the branch's PR).

## Setup
```
just setup                                  # uv sync, npm ci, git hooks (.githooks)
cp backend/.env.example backend/.env        # dev defaults: dev auth, 127.0.0.1:8000, local launcher
```
- `backend/.env` holds startup config, URLs and secrets. Every variable is `AGENTOS_`-prefixed; each is described in `backend/.env.example`.
- Secrets for the agents themselves (e.g. `ANTHROPIC_API_KEY`, `GH_TOKEN`) go in a separate dotenv file named by `AGENTOS_WORKER_ENV_FILE`. The local launcher removes `AGENTOS_*` from the worker's environment and adds that file.
- `AGENTOS_WORKSPACES_DIR` (default `~/.agentos/workspaces`) must be outside the data dir and outside any git repo. The server refuses to start otherwise.
- `backend/agentos.toml` is optional. It only seeds a fresh database (copy `backend/agentos.toml.example`). After the first start, the Space, repos, intake queries, agent runtimes and runner are edited in the app under **Settings**.
- Jira sign-in (with `AGENTOS_DEV_AUTH` off): set `AGENTOS_JIRA_URL` and `AGENTOS_ADMINS`. A first login waits for an admin to approve it. Jira import also needs the `AGENTOS_JIRA_SERVICE_*` service account.

## Run locally
```
just dev-backend    # API on http://127.0.0.1:8000 (reloads on edits)
just dev-web        # UI on http://127.0.0.1:5173 (Vite proxies /api to :8000)
```
Open http://127.0.0.1:5173 and sign in with any username. Dev auth only works on loopback, and dev users are admins.

## Checks
| Command | What |
|---|---|
| `just check` | Every gate: ruff, mypy (incl. the worker on Python 3.11), import-linter, pytest, then tsc, ESLint, Prettier, Vitest |
| `just check-backend` / `just check-web` | One half only |
| `just fmt-backend` / `just fmt-web` | Auto-format one half |
| `just gen-api` | Regenerate `web/src/api/openapi.json` + `schema.ts` after an API change. Commit them with the backend change; a test fails when they're stale |

## Deploy
The server ships as one container (API + built UI, data on a `/data` volume), and workers run on GitHub Actions runners. See [backend/deploy/README.md](backend/deploy/README.md) (`just container-build` / `container-run`, upgrades, runner setup).

## Layout
```
backend/
  server/agentos/   FastAPI + SQLite server: one package per feature, a pure core, plugins
    migrations/     numbered SQL migrations
  worker/worker.py  the worker: one stdlib-only file, served by the server
  deploy/           deploy guide, container env example, GitHub Actions worker workflow
web/src/
  api/              generated types + the query hooks (the only server access)
  app/ features/    shell and routes; one folder per view
  domain/ ui/       pure selectors and words; kit primitives
.githooks/          pre-commit (fast gates for the staged half)
Containerfile       the server image
justfile            every command
```

## Docs
- [ARCHITECTURE.md](ARCHITECTURE.md): how the system fits together, and why.
- [AGENTS.md](AGENTS.md): rules for coding agents (and humans) working in this repo. [backend/AGENTS.md](backend/AGENTS.md) and [web/AGENTS.md](web/AGENTS.md) hold each half's conventions.
- `SPEC.md`, `TASKS.md` (if present): local planning notes, not committed. The code wins over them.
