# Deploying AgentOS as a container

The image (root `Containerfile`) runs **the server only**: the API plus the built web UI, on port
8000. It holds no agent CLIs and no git credentials; workers run on runners (GitHub Actions or
self-hosted) and dial back to the server. The image is stateless: the database and logs live on
the `/data` volume, the config is mounted read-only, and secrets come from an env file.

Docker and Podman both work. The `just` recipes default to Docker; add `engine=podman` (or set
`CONTAINER_ENGINE=podman`) for Podman.

## First run

1. `backend/agentos.toml`: copy `backend/agentos.toml.example` (the Space, the repo catalog and
   the runtimes). It is mounted read-only at `/config/agentos.toml`.
2. `backend/deploy/.env.container`: copy `container.env.example` and fill it in (gitignored).
   Dev auth can't run in the container (it needs a loopback host), so this uses Jira.
3. Build and start:

   ```
   just container-build
   just container-run        # http://127.0.0.1:8080 (the dev backend keeps :8000)
   just container-logs
   ```

`container-run` replaces any container named `agentos` and starts a new one with:
- `-p 8080:8000`, `--restart unless-stopped`
- `-v agentos-data:/data` (a named volume: `agentos.db`, `logs/`, `backups/`)
- `--mount type=bind,source=backend/agentos.toml,target=/config/agentos.toml,readonly`
- `--env-file backend/deploy/.env.container`

Every name, path and the port is a `just` variable: for example,
`just container_port=9000 container_env=path/to/other.env container-run`.

The healthcheck probes `GET /api/health` every 30 s (`docker ps` shows `healthy`). The process
runs as uid/gid 10001 and can't write to the application files.

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

## Not yet

- The GitHub Actions launcher (D5). Until it lands, the server can only use the local launcher,
  which would try to run workers inside this container (no agent CLIs). So dispatching from the
  container doesn't work yet.
- TLS and proxy details (W5): put a reverse proxy in front and set
  `AGENTOS_PUBLIC_URL=https://...`. `AGENTOS_TRUST_PROXY` exists but isn't used yet.
