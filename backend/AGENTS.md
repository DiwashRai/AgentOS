# Backend conventions

Applies to `backend/**`: the FastAPI + SQLite server (`server/agentos/`), the single-file worker (`worker/worker.py`) and their tests. Repo-wide rules (commands, gates, commits, multi-session work, Windows gotchas) are in `../AGENTS.md`; the design rationale is in `../ARCHITECTURE.md`. If present, `../SPEC.md` has the long-form background; code wins over it.

## Layout
- `server/agentos/`: one package per feature (`auth`, `clients` (a user's client tokens), `tickets`, `files`, `social` (chat), `runs`, `templates`, `workflows`, `worker_api`, `intake`, `settings`), each split into `routes.py` / `deps.py` / `service.py` (+ `board.py`, `dispatcher.py`, `reaper.py` where needed).
- Shared modules: `models.py` (the API contract), `db.py`, `config.py`, `context.py`, `clock.py`, `errors.py` + `http_errors.py`, `broker.py` + `stream.py`, `space_store.py`, `csrf.py`, `main.py`.
- Pure core: `models.py`, `validate.py`, `pipeline/*` (scaffold, render, segment, manifest, plan, runtime, space), `tickets/board.py`.
- Plugins: `launchers/` (`local`, `github`; protocol in `launchers/base.py`) and `sources/` (`jira`; protocol in `sources/base.py`).
- `migrations/NNNN_name.sql`; `worker/worker.py` + `worker/worker_tests/`; `server/tests/` (+ `fixtures/`); `deploy/` (container + GitHub Actions runner, see `deploy/README.md`).

## Architecture (enforced by import-linter)
The contracts are in `pyproject.toml` `[tool.importlinter]`; each prints guidance when broken. In plain words:
- The pure core imports no web framework, `httpx`, `sqlite3`, `agentos.db` or `agentos.config`: it takes plain values, so it can be tested without I/O.
- Inside a feature: `routes → deps → (dispatcher | reaper) → service → board`. Never import upwards.
- Routes, deps, `meta` and `stream` never import `agentos.db` or `sqlite3`: they go through a service.
- Only `agentos.db` imports `sqlite3` (keeps a Postgres swap to one module).
- Only the HTTP layer imports FastAPI/Starlette/uvicorn. Services raise the pure `errors.ApiError`; `http_errors.py` turns it into a response.
- Plugins are adapters: no db, services, routes or FastAPI. They get what they need as arguments (the dispatcher counts active runs; the launcher only reports `max_concurrency`).
- No import cycles between subpackages. Direction: `worker_api → runs → social → tickets → workflows → templates | files | clients → auth`. `clients` is a leaf (its tokens, `ONLINE_WINDOW` / `online_user_ids`, and `CLIENT_LAUNCHER`, which `tickets.service` needs and can't import from `runs`). To read another feature's tables, query them in your own service rather than importing upwards.

**Fix the code, never the contract.** Don't loosen a contract to make an import work.

## Request pattern
- A route takes `ctx: Ctx` (`http_deps.py`) plus the narrowest auth dep, calls ONE service function with plain values (`ctx.db`, `ctx.broker`, `ctx.space`, ids, models) and returns its result. `tickets/routes.py` is the model to copy.
- Routers declare `responses=ERROR_RESPONSES` so the generated client knows the error shape.
- Services own transactions and raise `ApiError(status, code, message)` with a code from `models.ErrorCode`.
- Auth deps (`auth/deps.py`): `CurrentUser` (any approved user; `pending` gets 403 `pending_approval`), `Writer`, `Admin`, and `SignedIn` (`pending` included; only `GET /api/me`). Role deps run before body validation, so a too-low role gets 403 even with a bad body.
- Every non-GET `/api/*` request except `/api/worker/*` needs the `X-AgentOS: 1` header (`csrf.py`).
- `AppContext` (`context.py`) is built once in the lifespan and holds settings, db, launcher, space store, Jira client, broker and caches. Loops and tests use it directly.
- `create_app()` is cheap and side-effect free (the OpenAPI export runs it without config). Config checks, migrations, seeding and the background loops run in the lifespan in `main.py`.

## API contract
- `models.py` is the contract. Datetimes are `AwareDatetime` (ruff `DTZ` is on). PATCH bodies extend `PatchModel`: an omitted field is unchanged, and an explicit null is rejected unless the field is in `clearable`. `RunEvent` is a discriminated union on `type`; the worker sends exactly these.
- Add error codes to `ErrorCode`; never invent one at a raise site. The UI branches on them.
- `server/tests/test_response_models.py` validates every GET against its model (FastAPI serializes without validating). Extend it for every new GET.
- `server/tests/test_api_drift.py` fails when `web/src/api/openapi.json` is stale: run `just gen-api` and commit the generated files with the backend change.
- SSE payload models that appear in no JSON route are added to the schema via `EXTRA_SCHEMAS` in `main.py`.

## Data and migrations
- Use `db.tx()` for writes (BEGIN IMMEDIATE, commit or roll back) and `db.read()` for reads (one snapshot; writes raise). Import `Conn`, `Row`, `IntegrityError`, `OperationalError` from `agentos.db`.
- Timestamps are stored via `clock.to_db` and read via `from_db` / `from_db_opt`; get "now" from `clock.utcnow()`.
- Migrations are `migrations/NNNN_name.sql`, numbered 1..N with no gaps (`db.migrations()` refuses otherwise). Never edit a shipped migration; add a new one. Before migrating an existing db, `migrate()` backs it up to `{data_dir}/backups/` (5 newest kept). A server refuses a db newer than it knows.
- Schema rules: enum columns have no CHECK constraints (the `Literal`s in `models.py` are the only definition); `users` + `user_identities` (one user, many login providers); `templates` are append-only and tickets pin `template_id` (the template holds the scaffold and agent defaults; a ticket's pipeline is copied from a `workflows` row, recorded as `workflow_id`/`_name`/`_version`); `stage_runs` is keyed by `(run_id, idx)`, one row per stage execution in execution order (a loop block's later rounds insert theirs when they start, with `round` and a check's `verdict`); a run's manifest is built and stored at claim; idempotent inserts use `ON CONFLICT DO NOTHING`.
- Batch-load for lists (one query per table, never one per ticket).

## Settings and config
- `config.Settings` reads `AGENTOS_*` env + `backend/.env` with `extra="forbid"`: a new var must be added to `Settings`, and a typo fails startup.
- Admin-editable settings (Space, repo catalog, intake queries, runtimes, runner) live in the db `settings` table, one JSON document per area. `space_store.SpaceStore` is the cached read side (`ctx.space`, `ctx.runtimes`, `ctx.runner`); every save must call `store.invalidate()` so it applies without a restart.
- `agentos.toml` and the moved env vars (`config.MOVED_ENV`) only seed a fresh db (`context.seed_settings`); afterwards they're ignored with a startup warning.
- New settings go in the db, not env, unless they are a URL, a secret, startup/dev config, or decide who may sign in. `deploy/README.md` lists what the env keeps.

## Broker and stream
- `broker.py` fans notifications out to `GET /api/stream` (SSE). Events are notifications only; the client refetches.
- Services that change what the UI shows take `broker` as a plain argument and publish **after** the transaction commits, from plain sync code: `broker.ticket(key)`, `broker.run(key, run_id)`, `broker.chat(key, message_id)`. A run or chat change also publishes `ticket(key)`.
- Every new mutation of a ticket, its files, a run or chat must publish.

## Runs
- `runs/service.py` owns the state machine. Every transition is a guarded `UPDATE … WHERE id = ? AND status IN (…)`; zero rows means the run moved already (no-op or conflict; the caller decides).
- `finish()` is the only terminal path: it runs in the caller's transaction, cancels unfinished stage_runs and stamps `finished_at`. The caller publishes after commit.
- `runs/dispatcher.py` `tick(ctx)` launches queued runs within `launcher.max_concurrency`; `runs/reaper.py` `Reaper(ctx).tick(now)` (injected clock) fails runs whose worker never connects, goes silent or exceeds the time budget (rules in its docstring). `main.py` runs both as loops.
- Dispatch and worker tokens are stored only as SHA-256 hex digests and never cleared (a token's validity follows the run's status). Client tokens (`clients/service.py`) are digests too; revoking deletes the row.
- Client runs (`runs.launcher = 'client'`, `DispatchRequest.target = "client"`, owner only, else 403 `forbidden`; a user with no client token gets 422 `validation_error`): the dispatcher never sees them (`client` is never a `LauncherName`, so `mark_next_dispatched` and `max_concurrency` skip them). `runs.service.take_client_run` hands the user's oldest queued one to a polling client (guarded `queued → dispatched`, the client's name as `launch_ref`); `POST /api/worker/poll` (`worker_api/routes.py`, `async def`, waits with asyncio and runs each take through `asyncio.to_thread`) calls it about once a second for up to `wait_s` (≤ 25). Each try reads first (`client_run_queued`, `db.read()`) and opens the write transaction only when a run is queued, so waiting polls never take the write lock. `take: false` is a busy client's heartbeat: it records last seen and answers at once. Never make the poll a sync route: idle long-polls would starve the threadpool. While a client run waits (queued or dispatched; its manifest is built at claim) it keeps what its owner dispatched: the bundle is locked (409 `bundle_locked`) and a PATCH of `owner_id`, `title`, `description` or `type` gets 409 `run_active` (`clients.service.waiting_client_run_id`). Reaper rules key on the run's own row: a queued client run is never reaped, a taken one is reaped like a local run; only `mailbox` rows skip rules 3–5, and only with the mailbox launcher configured. `RunSummary.target` / `client_online` come from `tickets.service.run_target` / `client_online` (online creators batch-loaded in one query).
- The ticket's bundle is read-only (409 `bundle_locked`) while a run is `running`, and while a client run waits (above).
- Loop blocks (`Pipeline.loops`, request 24; `design/review-loop.md` if present): `pipeline/segment.plan_run` gives a run's stages (plus the block's start when dispatched from inside one) and its time budget (every round and repair); `pipeline/manifest` renders every later-round and verdict-repair call, session ids included, so the worker only appends text to prompts. The worker runs the rounds and decides each check by code (exit code, or a JSON verdict it validates, repairs and fails closed on). A check's "fail" fails its stage only in the block's last round, so the board's failure anchor (and Retry) lands on it.
- Mailbox runs (`runs.launcher = 'mailbox'`): `mailbox/dispatcher.py` claims server-side at launch (`dispatched → running`), and `mailbox/reaper.py` polls the Actions run and replays the outbox through the unchanged `worker_api.service` functions. With a `MailboxLauncher`, `main.py` runs those loops instead of `runs.dispatcher.tick`, and the runs reaper skips its heartbeat, budget and cancel rules for mailbox runs. Stages and logs land when the GitHub job ends.
- Helpers other features call: `files.service.write_version` / `current_bundle`; `tickets.service.get_ticket_row`, `ticket_ref(row, space)` / `run_ticket_ref`, `ticket_pipeline`, `active_run_id`.

## Board
- `tickets/board.py` is pure: `derive(facts) -> TicketBoard` (viewer-independent), then `for_viewer(board, viewer_id) -> ViewerBoard`; `next_stage(pipeline, latest)` picks where a run continues. Services load the facts and pass them in.
- UI copy (e.g. `now.text`) is asserted in `test_board.py` and `test_ticket_board.py` only; elsewhere assert structured fields.

## Worker contract
- `worker/worker.py` is one stdlib-only file (`test_worker_stdlib.py`), served to runners at `GET /api/worker/worker.py` (a client runs its user's own copy, with no version check). Its only interfaces are the manifest and the worker API models (the client poll's included); the whole backend targets py311 for it (ruff `target-version`, plus `check-backend` type-checks it as 3.11).
- `server/tests/fixtures/manifest_v1.json` is the worker ↔ server contract; both suites parse it.
- Path rules live in one table, `fixtures/path_cases.json`, run against both `validate.validate_path` and `worker.path_error`. Add cases there, not in either suite.
- Line limits: manifest `limits.max_line_bytes` = `validate.MAX_AGENT_LINE_BYTES` (256 KiB, agent stdout); the worker cuts every other stream at `min(SHELL_LINE_BYTES, max_line_bytes)` (8 KiB).
- The worker appends `JUNK_PATTERNS` (tool caches only, never `dist/`/`build/`) to `.git/info/exclude`; it never touches tracked files and the repo's `.gitignore` wins.
- `.agentos/` in the workspace belongs to AgentOS (a check's `verdict.json`): excluded, protected like the context files, never synced or committed.
- `AGENTOS_PR_LOOKUP=0` skips the end-of-run `gh pr view`.
- Client mode (`--client --server URL`, token only in `AGENTOS_CLIENT_TOKEN`, which `main` pops in every mode; conflicts with `--token`, `--workdir`, `--manifest`, `--outbox`): `run_client` long-polls `POST /api/worker/poll` while a slot is free (`--max-runs`, default 1); with every slot busy it sends a `take: false` heartbeat every `CLIENT_HEARTBEAT_S` (30 s) so it stays online. It starts this same file per run with `spawn_detached` (argv `--server URL --workdir <root>/runs/<run_id>`, log `<root>/runs/<run_id>.log`, env from `child_env`: ours minus `AGENTOS_*`, then the env file, then `AGENTOS_TOKEN`). Everything it writes is under its own `<root>/runs` (`CLIENT_RUNS_DIR`). `parse_env` is a stdlib subset of python-dotenv (comments, `export`, quotes, bare `KEY` = unset; no `${VAR}` expansion; anything else is an error). At startup it refuses a `--workdir-root` inside a git repo and a bad env file (exit 2) and deletes `<run_id>` / `<run_id>.log` entries in `<root>/runs` older than 7 days (`clean_workdir_root`; nothing else in the root is looked at). Network errors, 5xx and transient 4xx (`CLIENT_TRANSIENT_4XX`: 408, 425, 429) back off up to `CLIENT_BACKOFF_CAP_S` forever; any other 4xx (401/403, or a 404/422 from a server whose protocol moved on) exits 1. Started workers outlive the client.
- Every agent call's output is also echoed on the worker's stderr as it arrives (`Echo`: readable `[stage id] ...` lines from Claude stream-json and Copilot JSONL, anything else as plain text cut short), so a job console or the launcher's log shows the run live; the log events are unchanged. `AGENTOS_ECHO=0` turns it off. Every printed line carries the prefix (control characters are stripped), so agent output can't start a workflow command.

## Testing
- `just check-backend` runs `pytest -n auto` (pytest-xdist, one worker per CPU thread). A plain `uv run pytest [file]` stays serial. The suite runs in random order (pytest-randomly) on every run; the seed is printed at the end (`random order: --randomly-seed=N`, from `server/tests/conftest.py`). Reproduce a failing order with `--randomly-seed=<seed>` (or `=last`), serially or with the same `-n`; `-p no:randomly` runs in file order. For the inner loop, `-m 'not e2e'` skips the end-to-end tests. `gc.freeze()` and a raised GC threshold were measured under xdist and saved nothing, so the test session leaves the GC alone.
- One app per session (per xdist worker): the session fixture `session_server` creates the app (`run_loops=False`), enters one `TestClient` (base URL `127.0.0.1`, because dev auth needs a loopback host), so the real lifespan migrates and seeds once, and keeps that db as `template.db`. Each test's `ctx` copies the template to its own `settings.db_path`, builds an `AppContext` from that test's `settings` and `launcher` (`build_context`), binds its broker to the app's loop and sets `app.state.ctx`; teardown closes the broker and deletes `app.state.ctx`. `client` depends on `ctx` (not the other way round) and is the session client with its cookies cleared.
- Tests never modify the shared app (teardown asserts `dependency_overrides` is empty). A module overrides `settings` or `launcher` and the per-test `ctx` picks it up (`test_jira_login.py` swaps in Jira login that way). A test that needs other startup behaviour (startup checks, first-start seeding, loops, an https base URL, the SPA dir) builds its own app with `create_app` and its own `TestClient` on a fresh db, as `test_auth.py`'s `started()` does; never seed those from the template.
- Fixtures in `server/tests/conftest.py`: `settings` (tmp dirs, dev auth, `Settings(_env_file=None)` so a developer's `.env` never leaks in), `launcher` (a `FakeLauncher`), `app` (the session app), `client`, `ctx`, `authed`, `login(username, role=None)`, `set_role`, `create_ticket`, `add_run` (inserts run rows directly) and `runs` (a `RunDriver` that dispatches, ticks and acts as the worker). `test_fixtures.py` pins the isolation: every test starts signed out on an empty db.
- An autouse `_clean_env` drops every `AGENTOS_*` var from the shell, so tests see only the settings they pass. Session fixtures run before it, so `session_server` strips them itself.
- Mutating requests send `CSRF = {"X-AgentOS": "1"}`. Tests drive `dispatcher.tick` / `Reaper.tick` themselves; no loops run.
- Helpers: `run_support.py` (`FakeLauncher`, `RunDriver`, `Worker`, `event`, `log_event`, `finished`, `bearer`), `settings_support.py` (`set_space`, `set_repos`, `set_queries`). HTTP to Jira/GitHub is faked with `httpx.MockTransport`.
- `test_e2e.py` is marked `e2e` (real launcher, worker and git; deselect with `-m 'not e2e'`). So is `test_e2e_client.py`, the client e2e: a real uvicorn server with its loops, a recording fake launcher named `github`, and `worker.py --client` as a real process starting real run workers.
- Client mode elsewhere: `test_clients.py` (token lifecycle over HTTP), `test_client_poll.py` (poll filtering, atomicity, auth), and in the worker suite `test_worker_client.py` (`worker.main` in-process against the fake `server` via `server.hook`, with `spawn_detached` monkeypatched) and `test_worker_client_env.py` (tables for `parse_env` / `child_env`, the cleanup).
- Worker tests: helpers and fixtures in `worker_tests/worker_support.py` (re-exported by its `conftest.py`); autouse `fast` shrinks the timing constants and drops `GITHUB_ACTIONS`, `GH_TOKEN`, `GITHUB_REPOSITORY`, `GITHUB_API_URL` and `GITHUB_SERVER_URL` (so a suite run inside an Actions job never switches to API publication; the server's `_clean_env` drops the first two for the workers its e2e tests spawn), and `no_pr_lookup` sets `AGENTOS_PR_LOOKUP=0`. The GitHub publication helpers are tested as tables (`test_worker_github.py`); there is no fake GitHub API. The fake `server` polls for shutdown every 10 ms. `origin` is a per-test `copytree` of a bare repo the session fixture `origin_template` builds once (tests push to it and add hooks, so it is never shared).
- PR lookup: `worker.pr_lookup_result` (pure) turns the captured `gh` output into a URL or a reason; `test_worker_pr_lookup.py` tests it as a table. Whole runs are kept only for what a table can't show: a found PR on a failed run, a missing `gh`, the opt-out and no-repo mode.
- Live checks: anything that needs a real external service, a credential or the network goes in a `live/` folder inside the suite's test directory (`worker_tests/live/`, `server/tests/live/`). They are scripts run by hand (say after merging a change to that integration), never named `test_*.py`; `norecursedirs` keeps pytest out of `live/`, so CI never runs them. They target scratch resources only and clean up after themselves; the docstring says what they need. `worker_tests/live/github_publish.py` checks publication through the Git Data API.
- Test runtimes TOML is `fixtures/runtimes_test.toml`. Never name a test file `agentos.toml` (gitignored).

## Launchers and deploy
- `launchers/local.py` `LocalProcessLauncher.from_settings(settings, worker_path)` spawns detached workers (`CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`, tested against Ctrl+C) so a `--reload` doesn't kill runs.
- `launchers/github.py` dispatches `deploy/github/agentos-worker.yml`. Its copy in `.github/workflows/` must stay byte-identical (`test_launcher_github.py`).
- Mailbox mode (`AGENTOS_LAUNCHER=mailbox`, `design/mailbox.md` if present) is for runners that can't reach the server: `launchers/mailbox.py` puts `runs/<folder>/{manifest.json,worker.py}` in a `stores/` plugin (Artifactory) and dispatches `deploy/github/agentos-mailbox.yml` (inputs `run`, `worker_sha256`, `manifest_sha256`). Its jobs: fetch (Artifactory secret) → work (agent; no secret) → publish (uploads `outbox.jsonl`, bytes only). It has no installed copy in this repo; `test_workflow_mailbox.py` pins its security shape (no `${{` in `run:`, no secrets in work, credentials never in curl's argv).
- Client runs need no launcher: a user's `worker.py --client` pulls them, and spawns its workers like `launchers/local.py` (same flags and env rules, reimplemented in stdlib in `worker.py`; keep the two in step). `client` must never become a `LauncherName` / `AGENTOS_LAUNCHER` value.
- Container, runner and client setup: `deploy/README.md`.

## Safety rules
- Ask before anything calls the GitHub API for real or pushes a workflow file.
- Never write a token into a tracked file or a log. Source error messages never carry credentials.
- Catalog URLs for runner repos must be https (the job's token only works over https).
- The worker uses the job's `GITHUB_TOKEN` (as `GH_TOKEN`); on Actions (`GITHUB_ACTIONS=true` + `GH_TOKEN`) it publishes through the Git Data API instead of `git push`, so it writes only to the workflow's own repo (multi-repo setups supply a broader token themselves; see `deploy/README.md`). Don't switch to a GitHub App token without asking. `worker_tests/live/github_publish.py` checks publication against the real API by hand (see Testing › Live checks).
- Real dispatch tests use the scratch repo and test model the owner names; ask if you don't know them.

## Dev workflow
- To run `just dev-backend` from an agent shell, give it its own hidden console: `Start-Process pwsh -ArgumentList '-NoProfile','-Command','just dev-backend' -WorkingDirectory <repo root> -WindowStyle Hidden -RedirectStandardOutput <log> -RedirectStandardError <err-log>`. As a harness background task, uvicorn's reload Ctrl+C kills the wrapper and the harness reaps the tree.
- Keep files ruff-clean as you go (`uv run ruff format <files>`, `uv run ruff check <files>`): the pre-commit hook checks all of `backend/`.
