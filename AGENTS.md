# AgentOS: repo rules for agents

AgentOS is a web app for creating tickets, editing their context and pipeline, and dispatching them to disposable workers that run agent CLIs (Claude Code, Copilot CLI). The loop: create → dispatch → gate → edit → continue → PR. A FastAPI + SQLite server (`backend/server/agentos`), a React web app (`web/`) and a stdlib-only worker (`backend/worker/worker.py`) that runs on a local process or a GitHub Actions runner and dials back to the server.

Read next, as your task needs: `README.md` (setup, running), `ARCHITECTURE.md` (the system and why), `backend/AGENTS.md` (backend conventions), `web/AGENTS.md` (frontend conventions), `backend/deploy/README.md` (container, runners). Local-only planning docs (`SPEC.md`, `TASKS.md`, `CONTRACT_REQUESTS.md`, `design/`) are gitignored: use them if present, never assume them.

## Working principles
- **Simplicity.** A minimal but watertight MVP beats a broad, leaky one. Cut breadth, not correctness: the git protections, the token lifecycle and the run state machine stay watertight.
- **Deterministic gates.** Behaviour is pinned by tests and static checks, not by review alone.
- **Code wins.** Once code exists, it is the truth; planning docs only record decisions.
- **Vertical slices.** Each step ends in something visible plus an automated check.
- **Environment-specific behind config or a plugin.** This repo gets cloned into a workplace and diverges (Copilot CLI instead of Claude, Jira as the main ticket source, GitHub Actions runners). Launchers (`agentos.launchers`) and ticket sources (`agentos.sources`) are plugins; runtimes are argv templates in Settings.

## Layout
| Path | What |
|---|---|
| `backend/server/agentos/` | Server: one subpackage per feature (`tickets`, `runs`, `files`, ...), pure core (`models.py`, `validate.py`, `pipeline/`), plugins (`launchers/`, `sources/`), SQL migrations (`migrations/000N_*.sql`) |
| `backend/server/tests/` | Server tests |
| `backend/worker/worker.py`, `worker_tests/` | The worker (stdlib only, Python 3.11) and its tests |
| `backend/deploy/` | Container env example, the GitHub Actions worker workflow template, deploy README |
| `web/src/` | `app/`, `features/`, `domain/`, `ui/`, `styles/`, `api/` (generated `openapi.json` + `schema.ts`, plus hand-written client code) |
| `.github/workflows/agentos-worker.yml` | Installed copy of `backend/deploy/github/agentos-worker.yml`; a test keeps them byte-identical |
| `Containerfile`, `.dockerignore` | Server image (API + built SPA) |
| `justfile`, `.githooks/` | Task runner and the pre-commit hook |

## Setup
Prerequisites: Python 3.12 + uv, Node 24, `just`, pwsh (the recipes run in pwsh on Windows).
1. `just setup`: `uv sync`, `npm ci`, and points git at `.githooks`. Re-run after a lockfile change.
2. Copy `backend/.env.example` → `backend/.env`. The server runs from `backend/`, so relative paths resolve there; runtime data goes to `backend/data/` (gitignored).

## Commands (repo root)
| Command | What |
|---|---|
| `just setup` | Install deps, enable git hooks |
| `just check` | Every gate, both halves (the default recipe) |
| `just check-backend` / `just check-web` | One half's gates. Prefer these |
| `just fmt-backend` / `just fmt-web` | Auto-format one half (`just fmt` does both) |
| `just gen-api` | Export the backend's OpenAPI schema to `web/src/api/openapi.json` and regenerate `schema.ts` |
| `just dev-backend` | Server on `127.0.0.1:8000` with `--reload`; refuses to start if :8000 is taken |
| `just dev-web` | Vite on `127.0.0.1:5173` (strict port), proxying `/api` to :8000 (`AGENTOS_BACKEND_URL` overrides) |
| `just pre-commit-backend` / `just pre-commit-web` | The fast gates the hook runs |
| `just setup-hooks` | Only the `core.hooksPath` step of setup |
| `just container-build` / `-run` / `-logs` / `-stop` | Server image. Docker by default (`just engine=podman ...` or `CONTAINER_ENGINE`); host port 8080; needs `backend/deploy/.env.container` copied from `container.env.example`. See `backend/deploy/README.md` |

## Gates
- `check-backend`: `ruff format --check`, `ruff check`, `mypy` (strict, pydantic plugin), `mypy --python-version 3.11 worker/worker.py`, `lint-imports`, `pytest`.
- `check-web` = `npm run check`: `tsc`, `eslint --max-warnings 0`, `prettier --check`, `vitest run`.
- The pre-commit hook runs the fast gates (format + lint) for each half that has staged files. The generated `web/src/api/openapi.json` and `schema.ts` alone don't trigger the web gates. Run the full scoped gate yourself before committing.
- **Never weaken a gate to make it pass.** No blanket `# noqa`, `# type: ignore`, `eslint-disable` or `as any`: each suppression names the specific code and gives a one-line reason.
- New code goes where the architecture puts it. import-linter contracts in `backend/pyproject.toml` enforce the pure core, the feature layers, the plugin boundaries and acyclic subpackages; read them there, and see `backend/AGENTS.md`.
- `worker.py` imports only the standard library (a test enforces it) and must run on Python 3.11; ruff targets py311 for the whole backend for that reason.

## API contract
- The API shapes are `backend/server/agentos/models.py` plus the route signatures. The web types are generated from them; never hand-edit `web/src/api/openapi.json` or `schema.ts`.
- Error codes are the closed `ErrorCode` set in `models.py`. The UI branches on them (e.g. 409 `stale_version` / `run_active` / `bundle_locked`). Add a new code there; never invent one at a raise site.
- A contract change: edit the backend, run `just gen-api`, and commit the backend change and the regenerated `web/src/api/*` files together. `test_api_drift.py` fails if the committed schema is stale.
- Frontend work never edits the contract. It asks for the change (in `CONTRACT_REQUESTS.md` if present, otherwise to the user or backend owner) and carries on with a workaround.

## Config and settings
`.env` holds only what the server needs to start: URLs, host/port, secrets, the launcher choice, auth mode (every key is `AGENTOS_*`). Everything an admin edits (Space, repos, intake queries, agent runtimes, runner target) lives in the database and is edited in the app's Settings. `agentos.toml` is optional and only seeds settings on a first start. Details, including how to add an env key, are in `backend/AGENTS.md`.

## Working as an orchestrator with subagents
A long session acts as an **orchestrator**: it breaks its work into tasks, delegates each to a **subagent** with a self-contained brief, reviews the result (reads the diff, runs the gates), integrates and commits. It writes code itself only for small glue or fixes. Why: the orchestrator keeps its context for goals, decisions and integration instead of file dumps; subagents start with no memory, so the brief is all they know; review and parallel work come for free.

**A brief always contains:**
1. **The task and why**: the plan or ticket it belongs to, and how it fits the loop.
2. **Scope**: the exact files or modules it may create or edit. Everything else is off limits.
3. **Fixed interfaces**: contracts it must not change (`models.py`, route signatures, generated API files, test fixtures such as `manifest_v1.json`). If one looks wrong, it reports back.
4. **Done means**: the behaviour, the tests to add, the gate to run (`just check-backend` / `just check-web`), and what to report (summary, files touched, open questions, deviations).
5. **Read first**: this file, the relevant `backend/AGENTS.md` or `web/AGENTS.md`, and the relevant docs.

**Subagents:** don't commit or push; stay in scope and don't refactor beyond it; never weaken a gate; report blockers and assumptions instead of improvising around them.

**Orchestrators:** run parallel subagents only on disjoint files; re-run the gates yourself before committing; record decisions where later agents will find them, and tell running subagents to re-read changed instruction files, because agent CLIs don't reliably live-reload `AGENTS.md`.

## Commits and shared working trees
- Small commits, one task each, prefixed `backend: ...` or `web: ...`. Don't push unless the user asks.
- Multi-line messages: write the message to a file and `git commit -F <file>`. A PowerShell here-string piped to `-F -` doesn't reach git's stdin.
- Several sessions may share one working tree (e.g. a backend and a frontend session). Then:
  - Own your half; never edit files another session owns, since their uncommitted work may be in progress. Ask the user before changing root files.
  - Stage and commit with a pathspec, so only your paths go in whatever else is staged: `git add :/backend` then `git commit -F <file> -- :/backend` (add `:/web/src/api` for a contract change); for the web, `git add :/web` then `git commit -F <file> -- :/web ':(top,exclude)web/src/api'`. Never `git add -A` or `git add .`.
  - If git reports `.git/index.lock` exists, another session is mid-commit (the hook takes a few seconds). Wait and retry; never delete the lock.
  - Format only your half (`just fmt-backend` / `just fmt-web`), never `just fmt`.

## Dev servers
- Run them as background processes and read their logs when debugging. Before starting one, check the port (`Get-NetTCPConnection -LocalPort 8000 -State Listen`, likewise 5173) and reuse a running server.
- Only stop servers you started, and stop them gracefully. A force-killed `--reload` backend leaves an orphaned `python ... multiprocessing-fork` child holding :8000; clear it with `Get-CimInstance Win32_Process | ? CommandLine -match 'multiprocessing-fork' | % { Stop-Process -Id $_.ProcessId -Force }` (only if that backend was yours).
- Dev auth (`AGENTOS_DEV_AUTH=1`) needs a loopback `AGENTOS_HOST` and `AGENTOS_PUBLIC_URL` and can't run in the container; otherwise sign-in is via Jira.

## Environment gotchas
- Windows 11 dev machine; `just` recipes run in pwsh. Pinned tool versions are in `web/package.json` and `backend/uv.lock`. TypeScript stays on 5.9 (typescript-eslint and openapi-typescript don't support 7); ESLint stays on 9 (jsx-a11y doesn't support 10).
- Files are LF everywhere (`.gitattributes`, `.editorconfig`), except `backend/worker/worker_tests/fixtures/**`, which holds deliberate CRLF content. Python on Windows writes CRLF by default: use `write_text(..., newline="\n")` or bytes for anything generated or synced.
- Vite binds `127.0.0.1` explicitly, because `localhost` can resolve to `::1` only on Windows.
- pytest uses the default (prepend) import mode; importlib mode shadows `worker.py` with a `worker` namespace package. Worker tests live in `backend/worker/worker_tests/` so both suites can have a `conftest.py`. Keep test file basenames unique across suites.
- `uvicorn --reload` on Windows restarts by sending Ctrl+C to its console. Processes the server spawns (workers) must use `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`, or a backend edit kills in-flight runs.
- The local launcher strips every `AGENTOS_*` variable from the worker's environment; pass agent credentials through `AGENTOS_WORKER_ENV_FILE`.
- `claude` runs headless with the prompt on stdin plus `-p --output-format stream-json --verbose`. The built-in runtime argv is in `backend/server/agentos/pipeline/runtime.py`.
- Never name a test fixture `agentos.toml`, `.env` or `*.db`: `.gitignore` drops them.
