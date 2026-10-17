# AgentOS: repo rules for agents

AgentOS is a web app for creating tickets, editing their context and pipeline, and dispatching them to disposable workers that run agent CLIs (Claude Code, Copilot CLI). The loop: create → dispatch → gate → edit → continue → PR. A FastAPI + SQLite server (`backend/server/agentos`), a React web app (`web/`) and a stdlib-only worker (`backend/worker/worker.py`) that runs on a GitHub Actions runner or a user's own client (`worker.py --client`, which pulls that user's runs) and dials back to the server.

Read next, as your task needs: `README.md` (setup, running), `ARCHITECTURE.md` (the system and why), `backend/AGENTS.md` (backend conventions), `web/AGENTS.md` (frontend conventions), `backend/deploy/README.md` (container, runners). Local-only planning docs (`SPEC.md`, `TASKS.md`, `CONTRACT_REQUESTS.md`, `design/`) are gitignored: use them if present, never assume them.

## Working principles
- **Simplicity.** A minimal but watertight MVP beats a broad, leaky one. Cut breadth, not correctness: the git protections, the token lifecycle and the run state machine stay watertight.
- **Deterministic gates.** Behaviour is pinned by tests and static checks, not by review alone.
- **Code wins.** Once code exists, it is the truth; planning docs only record decisions.
- **Vertical slices.** Each step ends in something visible plus an automated check.
- **Environment-specific behind config or a plugin.** This repo gets cloned into a workplace and diverges (Copilot CLI instead of Claude, Jira as the main ticket source, GitHub Actions runners). Launchers (`agentos.launchers`) and stores (`agentos.stores`) are plugins; Jira is one HTTP adapter (`agentos.sources.jira`) that login, import and intake call directly; runtimes are argv templates in Settings.

## Code rules
- Docstrings and comments describe what the code does now, never which ticket or request added it.
- Tests cover behaviour, requirements or tricky algorithms only, at the highest seam that shows it.
- An error test checks the error code plus at most one crucial word, never a sentence.
- Language specifics: `backend/AGENTS.md` (blank lines between steps, try/except at boundaries) and `web/AGENTS.md` (UI tests find elements by role and name).

## Layout
| Path | What |
|---|---|
| `backend/server/agentos/` | Server: one subpackage per feature (`tickets`, `runs`, `files`, ...), pure core (`models.py`, `validate.py`, `pipeline/`), plugins (`launchers/`, `stores/`), the Jira adapter (`sources/jira.py`), SQL migrations (`migrations/000N_*.sql`) |
| `backend/server/tests/` | Server tests |
| `backend/worker/worker.py`, `worker_tests/` | The worker (stdlib only, Python 3.11; also the personal client, `worker.py --client`) and its tests |
| `backend/deploy/` | Container env example, the GitHub Actions worker workflow template, deploy README |
| `web/src/` | `app/`, `features/`, `domain/`, `ui/`, `editor/` (the editor kit), `styles/`, `api/` (generated `openapi.json` + `schema.ts`, plus hand-written client code) |
| `.github/workflows/agentos-worker.yml` | Installed copy of `backend/deploy/github/agentos-worker.yml`; a test keeps them byte-identical |
| `Containerfile`, `.dockerignore` | Server image (API + built SPA) |
| `justfile`, `.githooks/` | Task runner and the pre-commit hook |
| `tools/gates/` | The gates (`gates.py`, `ts_measures.mjs`, their tests); `web/eslint.gates.config.js` is theirs too |

## Setup
Prerequisites: Python 3.12 + uv, Node 24, `just`, pwsh (the recipes run in pwsh on Windows).
1. `just setup`: `uv sync`, `npm ci`, and points git at `.githooks`. Re-run after a lockfile change.
2. Copy `backend/.env.example` → `backend/.env`. The server runs from `backend/`, so relative paths resolve there; runtime data goes to `backend/data/` (gitignored).

## Commands (repo root)
| Command | What |
|---|---|
| `just setup` | Install deps, enable git hooks |
| `just check` | Every check, both halves plus `check-tools` (the default recipe) |
| `just check-backend` / `just check-web` | One half's checks. Prefer these |
| `just check-tools` | The gate scripts' own checks (`tools/gates`: format, lint, mypy, tests) |
| `just gates` | Every gate: pass/fail per gate, each offender as `path:line  name  score > limit` |
| `just ratchet` | Lower `quality-baseline.json` where the code improved; never raises it |
| `just fmt-backend` / `just fmt-web` | Auto-format one half (`just fmt` does both) |
| `just gen-api` | Export the backend's OpenAPI schema to `web/src/api/openapi.json` and regenerate `schema.ts` |
| `just dev-backend` | Server on `127.0.0.1:8000` with `--reload`; refuses to start if :8000 is taken |
| `just dev-web` | Vite on `127.0.0.1:5173` (strict port), proxying `/api` to :8000 (`AGENTOS_BACKEND_URL` overrides) |
| `just pre-commit-backend` / `just pre-commit-web` | The fast checks the hook runs |
| `just setup-hooks` | Only the `core.hooksPath` step of setup |
| `just container-build` / `-run` / `-logs` / `-stop` | Server image. Docker by default (`just engine=podman ...` or `CONTAINER_ENGINE`); host port 8080; needs `backend/deploy/.env.container` copied from `container.env.example`. See `backend/deploy/README.md` |

## Checks
- `check-backend`: `ruff format --check`, `ruff check`, `mypy` (strict, pydantic plugin), `mypy --python-version 3.11 worker/worker.py`, `lint-imports`, `pytest -n auto` (parallel, random order; see `backend/AGENTS.md` › Testing).
- `check-web` = `npm run check`: `tsc`, `eslint --max-warnings 0`, `prettier --check`, `check:spacing` (no raw px/em/rem spacing in CSS modules; `web/scripts/check-spacing.ts`), `vitest run`.
- The pre-commit hook runs the fast checks (format + lint) for each half that has staged files, then `just gates` for any code or baseline change. The generated `web/src/api/openapi.json` and `schema.ts` alone don't trigger the web checks. Run the full scoped check yourself before committing. Never skip the hook (`--no-verify`).
- No CI runs the test suites: the hook runs format, lint and the gates only, so the scoped check (or an AgentOS run's check stage) is the only place tests run before review.
- **Never weaken a check or a gate to make it pass.** No blanket `# noqa`, `# type: ignore`, `eslint-disable` or `as any`: each suppression names the specific code and gives a one-line reason.
- New code goes where the architecture puts it. import-linter contracts in `backend/pyproject.toml` enforce the pure core, the feature layers, the plugin boundaries and acyclic subpackages; read them there, and see `backend/AGENTS.md`.
- `worker.py` imports only the standard library (a test enforces it) and must run on Python 3.11; ruff targets py311 for the whole backend for that reason.
- ruff also bans broad excepts outside top-level handlers (`BLE001`; each allowed one says why) and clock reads in the pure core (`TID251`: it takes `now` as an argument).

## Gates
Measured limits on how the code grows, separate from the checks: `just gates` (`tools/gates/gates.py`). No function over cognitive complexity 15 or cyclomatic complexity 10 (TypeScript: ESLint `complexity`; Python: `limits.cyclomatic_python_tool`, `lizard` by default or `ruff` C901; the named cyclomatic exceptions are that tool's scores, so a switch goes with `--rebaseline-cyclomatic` and a `raises` entry); no defensive patterns (`isinstance` and its look-alikes: `type(x) is`, `x.__class__`, `case {...}`, `case [...]`; `try`, broad excepts, `getattr` defaults, `hasattr`, `suppress`, and in the core `cast`) beyond each file's cap; every Python file in a layer; in the core, no `Any` (mypy with every `Any` an error, per file; any other mypy error there, unreadable mypy output, or a `[[tool.mypy.overrides]]` naming a core module fails too) and no domain class (not a pydantic model) with 2 or more optional fields (judged by the field's outermost type); `is None` and optional parameters aren't gated (they are syntax; the types carry it); no subpackage importing more than 5 others (`main` exempt); no suppressions (`noqa`, `type: ignore`, `# mypy:` comments, `@no_type_check`, eslint-disable) beyond each file's cap; no file over 1,000 code lines; raw DOM tags per feature folder only go down. Python and TypeScript alike.
- Three roles (layers in `quality-baseline.json`, first glob wins): the **core** (`models.py`, `errors.py`, `pipeline/`, `*/board.py`, `*/rules.py`) holds domain types and rules on values that are already parsed: no `Any`, no untrusted dicts, no optional fields standing in for a missing state type, no repeated checks; honest optionals (`owner: Person | None`) are fine. The **boundary** (`validate.py`, routes, deps, plugins, `db.py`, `config.py`, the other I/O modules) parses chaotic input into typed values, so the kinds in `allowed.uncapped` (`isinstance`, `try`, `cast`) are uncapped there. The **shell** (services, reapers, dispatchers, `main.py`) loads, calls the core and saves. The worker is its own layer.
- A `try` that only cleans up (a `finally` with no `return`/`break`/`continue`, and excepts that end in a bare `raise` with no way out before it) directly inside a function decorated with contextlib's own `contextmanager`/`asynccontextmanager` (as imported) or an `__enter__`/`__exit__` method isn't counted, nor is its re-raising broad except. Everywhere else, clean up with `with` (`contextlib.closing`, `ExitStack`).
- Existing offenders are named in `quality-baseline.json` (repo root): they may not get worse, and nothing new may join them (with no baseline every cap is 0). `just ratchet` locks in an improvement; commit the lower baseline with the change. Raising a number needs a `raises` entry (`date`, `what`, `from`, `to`, `reason`); `gates` checks against the committed baseline (`just gates-pr` against the branch's merge base with `origin/cleanup`, `just gates --base REF` with any ref) and lists every new entry under "raises to approve". The baseline is checked against its schema on load. `raises` entries are added by the owner; an agent proposes one in its report instead.
- Known gaps, for reviewers (the gates don't see these): sentinel defaults instead of None (`owner: str = ""`); a dataclass turned into a pydantic model; fields set in `__init__` or inherited; optional aliases imported from another module; edits to `tools/gates` that loosen a measure.
- Run `just gates` before finishing a change, alongside the scoped check.
- A failing gate means simplifying the code, not splitting it to dodge a limit or disguising a pattern (look-alikes are counted too).

## API contract
- The API shapes are `backend/server/agentos/models.py` plus the route signatures. The web types are generated from them; never hand-edit `web/src/api/openapi.json` or `schema.ts`.
- Error codes are the closed `ErrorCode` set in `models.py`. The UI branches on them (e.g. 409 `stale_version` / `run_active` / `bundle_locked`). Add a new code there; never invent one at a raise site.
- A contract change: edit the backend, run `just gen-api`, and commit the backend change and the regenerated `web/src/api/*` files together. `test_api_drift.py` fails if the committed schema is stale.
- Frontend work never edits the contract. It asks for the change (in `CONTRACT_REQUESTS.md` if present, otherwise to the user or backend owner) and carries on with a workaround.

## Config and settings
`.env` holds only what the server needs to start: URLs, host/port, secrets, the launcher choice, auth mode (every key is `AGENTOS_*`). Everything an admin edits (Space, repos, intake queries, agent runtimes, runner target) lives in the database and is edited in the app's Settings. A fresh database starts with built-in defaults for every area. Details, including how to add an env key, are in `backend/AGENTS.md`.

## Working as an orchestrator with subagents
A long session acts as an **orchestrator**: it breaks its work into tasks, delegates each to a **subagent** with a self-contained brief, reviews the result (reads the diff, runs the gates), integrates and commits. It writes code itself only for small glue or fixes. Why: the orchestrator keeps its context for goals, decisions and integration instead of file dumps; subagents start with no memory, so the brief is all they know; review and parallel work come for free.

**A brief always contains:**
1. **The task and why**: the plan or ticket it belongs to, and how it fits the loop.
2. **Scope**: the exact files or modules it may create or edit. Everything else is off limits.
3. **Fixed interfaces**: contracts it must not change (`models.py`, route signatures, generated API files, test fixtures such as `job_v2.json`). If one looks wrong, it reports back.
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
- `uvicorn --reload` on Windows restarts by sending Ctrl+C to its console. The client's run workers use `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`, so a Ctrl+C in its console doesn't kill in-flight runs.
- Client mode (`worker.py --client`) strips every `AGENTOS_*` variable from the run workers it starts; give it `--env-file` for agent and git credentials.
- `claude` runs headless with the prompt on stdin plus `-p --output-format stream-json --verbose`. The built-in runtime argv is in `backend/server/agentos/pipeline/runtime.py`.
- Never name a test fixture `.env` or `*.db`: `.gitignore` drops them.
