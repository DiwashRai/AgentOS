# Deploying AgentOS as a container

The image (root `Containerfile`) runs **the server only**: the API plus the built web UI, on port
8000. It holds no agent CLIs and no git credentials; workers run on runners (GitHub Actions or
self-hosted) and dial back to the server. The image is stateless: the database (which also holds
every setting an admin edits in the app) and logs live on the `/data` volume, and the few
deployment facts and secrets come from an env file. There is no config file to mount.

Docker and Podman both work. The `just` recipes default to Docker; add `engine=podman` (or set
`CONTAINER_ENGINE=podman`) for Podman.

## First run

1. `backend/deploy/.env.container`: copy `container.env.example` and fill it in (gitignored).
   Dev auth can't run in the container (it needs a loopback host), so this uses Jira: people
   sign in with their Jira email + API token, need access to one of the Space's Jira projects
   (Settings › Space), and wait for an admin's approval after their first sign-in
   (Settings › Users). List yourself in `AGENTOS_ADMINS` (account id or email): admins only need
   a valid Jira token, so the first sign-in is an admin who can then set up the Space. Importing
   Jira issues as tickets needs a service account: `AGENTOS_JIRA_SERVICE_EMAIL` +
   `AGENTOS_JIRA_SERVICE_TOKEN` (leave both empty to turn import off).
2. Build and start:

   ```
   just container-build
   just container-run        # http://127.0.0.1:8080 (the dev backend keeps :8000)
   just container-logs
   ```
3. Open the app and sign in as one of the `AGENTOS_ADMINS` people, then set the Space up in
   **Settings**: **Space** (name, key prefix, Jira projects, the repo catalog), **Intake** (the
   Jira queries) and **Runners** (the GitHub repo/workflow/ref runs are dispatched to, and the
   agent runtimes). Until Runners has a repo, every run fails at launch saying so.

`container-run` replaces any container named `agentos` and starts a new one with:
- `-p 8080:8000`, `--restart unless-stopped`
- `-v agentos-data:/data` (a named volume: `agentos.db`, `logs/`, `backups/`)
- `--env-file backend/deploy/.env.container`

Every name, path and the port is a `just` variable: for example,
`just container_port=9000 container_env=path/to/other.env container-run`.

The healthcheck probes `GET /api/health` every 30 s (`docker ps` shows `healthy`). The process
runs as uid/gid 10001 and can't write to the application files.

## Settings in the app

What an admin may change lives in the database and is edited in **Settings**; changes apply at
once, without a restart:
- **Space:** the Space's name, the default repo, the key prefix for new native tickets, the Jira
  projects (signing in needs browse access to one of them; `AGENTOS_ADMINS` people excepted, so a
  bad save can't lock the admins out) and the claim timeout. Plus the **repo catalog** (separate
  Save): tickets reference a repo by name, so changing a URL is safe; removing a repo that open
  tickets or the default repo use is refused.
- **Intake:** the Jira queries the Intake tab lists, with a "Test query" preview.
- **Runners:** the GitHub repo (`owner/name`), workflow file and ref that each run's workflow is
  dispatched to (read at every launch), plus the **agent runtimes** (separate Save): the argv
  templates stages run (`claude` and `copilot` built in). The first runtime is the server
  default. Removing a runtime the template's defaults or stages use is refused. Which launcher
  runs use (`AGENTOS_LAUNCHER`) and whether the GitHub token is set are shown read-only.

The env keeps what the server needs to start, who may sign in, and secrets. The whole
`.env.container`: `AGENTOS_PUBLIC_URL`, `AGENTOS_WORKER_URL`, `AGENTOS_JIRA_URL`,
`AGENTOS_ADMINS`, `AGENTOS_JIRA_SERVICE_EMAIL`, `AGENTOS_JIRA_SERVICE_TOKEN`, `AGENTOS_LAUNCHER`
and `AGENTOS_GITHUB_TOKEN`.

**Upgrading from an older version:** each settings area with nothing in the database yet is
seeded once, on the first start that knows it, from what configured it before: an
`agentos.toml` if one is found (`AGENTOS_CONFIG`, default `./agentos.toml`: `[space]` name and
default_repo, `[[repos]]`, `[[intake.queries]]`, `[runtimes.*]` with `default_runtime` first)
and the env vars that moved into the app (`AGENTOS_KEY_PREFIX`, `AGENTOS_JIRA_PROJECTS`,
`AGENTOS_CLAIM_TIMEOUT_S`; `AGENTOS_GITHUB_REPO`, `_WORKFLOW`, `_REF`), else from built-in
defaults. After that they are ignored: the server logs a warning at startup for each one still
set (naming the Settings tab that replaced it) and for an `agentos.toml` still present. Delete
them once the warnings appear. The image no longer reads `/config/agentos.toml`; an instance
whose runtimes were customised there gets the built-in `claude` + `copilot` instead, so check
Settings › Runners after the upgrade.

## Upgrade

Rebuild (or pull) the image, then start a new container on the same volume:

```
just container-build
just container-run
```

On startup the server migrates the database. If an existing database has pending migrations, it
first copies it with SQLite's online backup API to
`/data/backups/agentos-<UTC timestamp>-v<old version>.db` and keeps the 5 newest. A new
database, or one that's already current, isn't backed up. To roll back, stop the container,
copy a backup over `/data/agentos.db` and run the matching older image (a server refuses a
database that is newer than it knows).

`just container-stop` stops and removes the container. The volume stays; remove it with
`docker volume rm agentos-data` only if you mean to delete the data.

## Podman notes

- `just engine=podman container-build` adds `--format docker`. Podman's default OCI format
  drops `HEALTHCHECK`.
- Base images are fully qualified (`docker.io/library/...`, `ghcr.io/...`), so Podman never has
  to resolve a short name.
- The ignore file is `.dockerignore`, which Podman reads too (it only prefers a
  `.containerignore` if one exists, so don't add one).
- Rootless Podman has no daemon: `--restart unless-stopped` covers crashes, but nothing restarts
  the container after a reboot. Use a Quadlet/systemd unit for that.
- With rootless Podman the volume is owned by uid 10001 inside the user namespace. Named volumes
  work as is. A bind-mounted host directory for `/data` must be writable by that uid (for
  example `--userns=keep-id:uid=10001,gid=10001` or `podman unshare chown 10001:10001 <dir>`).
- On SELinux hosts, bind mounts need `:Z`/`:z` or the `relabel=shared` mount option.

## GitHub Actions runner

From the container, runs go to a GitHub Actions runner: the local launcher would try to run
workers inside the container, which has no agent CLIs. For each run, the server dispatches the
workflow `github/agentos-worker.yml`. The runner downloads `GET /api/worker/worker.py` from the
server and runs it, and the worker dials back to `AGENTOS_WORKER_URL`.

**Env** (in `.env.container`):

```
AGENTOS_LAUNCHER=github
AGENTOS_GITHUB_TOKEN=github_pat_...          # secret
# AGENTOS_GITHUB_API_URL=https://ghe.example.com/api/v3   # GHES only; default api.github.com
AGENTOS_WORKER_URL=http://<reachable from the runner>:8080
```

(`--env-file` takes no inline comments; they're only here to explain each line.)

**In the app** (Settings › Runners): the repo whose Actions run the workflow (`owner/name`, e.g.
`DiwashRai/AgentOS`), the workflow file (default `agentos-worker.yml`) and the ref the workflow
file is on (default `main`). They're read at every launch, so a change applies to the next run.

**Token:** use a fine-grained PAT that covers only the one repository, with the repository
permission **Actions: Read and write** (dispatching needs write; Metadata: read is added
automatically). It can't push code or read secrets. A classic PAT would need the whole `repo`
scope, so avoid it.

**Install the workflow:** copy `github/agentos-worker.yml` to `.github/workflows/agentos-worker.yml`
on the repo's **default branch** and push it. GitHub only dispatches workflows it knows from the
default branch. In this repo the copy already exists, and a backend test keeps it byte-identical
to the template.

**Runner checklist** (for the user the runner service runs as):
- [ ] Self-hosted runner registered on the Runners repo with the label `agentos`
      (the job uses `runs-on: [self-hosted, agentos]`), running as a service.
- [ ] `python3` >= 3.11, `curl`, `git` and `gh` installed. The job checks for these first.
- [ ] The agent CLIs your runtimes use (e.g. `claude`), logged in.
- [ ] **No** repo credentials of its own: `gh` not logged in, no SSH key, no stored git
      credentials. Agents run with a shell, so anything the runner holds is theirs too.
- [ ] The ticket repos' URLs (Settings › Space, repos) are **https** (the job's token works over https).
- [ ] `curl $AGENTOS_WORKER_URL/api/health` works **from the runner**.

**Repo credentials = the job's `GITHUB_TOKEN`.** The workflow grants it `contents: write` and
`pull-requests: write` on this repo only, and it expires with the job. The worker step gets it as
`GH_TOKEN` (for `gh`: the open-pr stage, the PR lookup), and git uses it through gh's credential
helper, set for that step only via `GIT_CONFIG_*` (nothing is written to `~/.gitconfig`). Caveat:
PRs opened with `GITHUB_TOKEN` don't trigger other workflows (e.g. CI). When that matters, use a
GitHub App token or a fine-grained token (one repo; contents + pull requests) from a repo secret
instead. Registering the runner with `--ephemeral` gives every run a fresh runner.

No `actions/checkout`: the worker clones the ticket's repo itself. Dispatch inputs show in the Actions UI to anyone who can read the repo. The dispatch
token is single-use: the worker's claim binds its own token. The job masks the token before
anything else and never interpolates inputs into its scripts. Its `timeout-minutes: 720` is
only a backstop. The real limit is the run's time budget (the sum of its stages' `timeout_s`
plus 15 min). Past it, the reaper fails the run and the worker stops on its next call.

**WSL2 runner and `AGENTOS_WORKER_URL`.** The container publishes host port 8080 on Windows.
Inside WSL2, `127.0.0.1` means the WSL VM, not Windows, unless WSL uses mirrored networking:
- **Mirrored networking** (simplest; Windows 11 22H2+): put `networkingMode=mirrored` under
  `[wsl2]` in `%UserProfile%\.wslconfig`, then run `wsl --shutdown`. After that,
  `AGENTOS_WORKER_URL=http://127.0.0.1:8080` works from the runner. The browser keeps using
  `AGENTOS_PUBLIC_URL`.
- **NAT (the default):** use the Windows host's IP as WSL sees it:
  `ip route show default | awk '{print $3}'` inside WSL. That IP can change after a reboot. The
  Windows firewall must allow inbound TCP 8080 from the WSL network. Set it in
  `AGENTOS_WORKER_URL=http://<that-ip>:8080`.
- If the runner has an HTTP proxy configured, add that host to `NO_PROXY`. The worker only
  bypasses proxies for loopback hosts.

## Not yet

- TLS and proxy details (W5): put a reverse proxy in front and set
  `AGENTOS_PUBLIC_URL=https://...`. `AGENTOS_TRUST_PROXY=1` makes the login rate limit use the
  proxy's client-IP headers.
