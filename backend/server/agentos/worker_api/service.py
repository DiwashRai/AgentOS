"""The worker protocol (SPEC §8 claim table, §10 worker API): claim, events, sync, complete.

Claim authenticates with the dispatch token, every other route with the worker token the worker
generated and bound at claim. Tokens are looked up by their SHA-256 hash. Worker-token runs are
`running` or terminal; a terminal run answers 410 `run_gone` (the worker aborts and never pushes),
except `complete` repeating the run's final status (idempotent retry).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from agentos.clock import to_db, utcnow
from agentos.db import Conn, Database, IntegrityError, Row
from agentos.errors import ApiError
from agentos.files.service import current_bundle, write_version
from agentos.models import (
    CompleteRequest,
    Manifest,
    ManifestFile,
    RunEvent,
    RunStatus,
    Stage,
    SyncFile,
    WorkerFinalStatus,
)
from agentos.pipeline.manifest import build_manifest
from agentos.pipeline.runtime import RuntimesConfig
from agentos.pipeline.segment import completion_status
from agentos.runs.service import TERMINAL, finish, token_hash
from agentos.tickets.service import ticket_pipeline, ticket_ref
from agentos.validate import path_key, validate_path, validate_pr_url

log = logging.getLogger(__name__)

MAX_EVENTS_PER_RUN = 100_000  # SPEC §6; beyond it `log` events are dropped

# The run statuses a repeated `complete` may find (idempotent retry → 204).
_FINAL: dict[WorkerFinalStatus, frozenset[RunStatus]] = {
    "completed": frozenset({"gated", "succeeded"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
}


def _gone(run_id: int, status: str) -> ApiError:
    return ApiError(410, "run_gone", f"Run {run_id} is {status}")


def _unauthorized(what: str) -> ApiError:
    return ApiError(401, "unauthorized", f"Unknown {what} token")


# ------------------------------------------------------------------ claim


@dataclass(frozen=True, slots=True)
class _Claim:
    manifest: Manifest | None = None
    error: ApiError | None = None


def claim(
    db: Database, runtimes: RuntimesConfig, dispatch_token: str, worker_token: str
) -> Manifest:
    """dispatched → running, binding the worker token and building the manifest from the bundle
    as it is now (the bundle locks from here). A safe retry with the same worker token returns
    the stored manifest; another worker token → 409 `already_claimed`; terminal → 410."""
    outcome = _claim(db, runtimes, token_hash(dispatch_token), token_hash(worker_token))
    if outcome.error is not None:
        raise outcome.error
    if outcome.manifest is None:  # pragma: no cover - _claim sets one or the other
        raise RuntimeError("claim produced no manifest")
    return outcome.manifest


def _claim(db: Database, runtimes: RuntimesConfig, dispatch_hash: str, worker_hash: str) -> _Claim:
    """Errors are returned rather than raised where the transaction must still commit (a run
    whose manifest can't be built is failed)."""
    now = utcnow()
    with db.tx() as conn:
        run = conn.execute(
            "SELECT * FROM runs WHERE dispatch_token_hash = ?", (dispatch_hash,)
        ).fetchone()
        if run is None:
            raise _unauthorized("dispatch")
        if run["status"] == "running":
            if run["worker_token_hash"] != worker_hash:
                raise ApiError(409, "already_claimed", f"Run {run['id']} is already claimed")
            return _Claim(manifest=Manifest.model_validate_json(run["manifest"]))
        if run["status"] != "dispatched":
            raise _gone(run["id"], run["status"])
        try:
            manifest = _build(conn, run, runtimes)
        except ApiError as exc:
            error = f"cannot build the manifest: {exc.message}"
            finish(conn, run["id"], "failed", ("dispatched",), now=now, error=error)
            return _Claim(error=_gone(run["id"], f"failed: {error}"))
        stamp = to_db(now)
        try:
            cursor = conn.execute(
                "UPDATE runs SET status = 'running', worker_token_hash = ?, manifest = ?, "
                "started_at = ?, last_seen_at = ? WHERE id = ? AND status = 'dispatched'",
                (worker_hash, manifest.model_dump_json(), stamp, stamp, run["id"]),
            )
        except IntegrityError as exc:  # worker_token_hash is UNIQUE: a token reused across runs
            raise ApiError(409, "already_claimed", "That worker token is already in use") from exc
        if cursor.rowcount == 0:  # pragma: no cover - BEGIN IMMEDIATE serialises writers
            raise ApiError(409, "already_claimed", f"Run {run['id']} is already claimed")
        # publish: run {key, id}
        return _Claim(manifest=manifest)


def _build(conn: Conn, run: Row, runtimes: RuntimesConfig) -> Manifest:
    """The run's stages come from the ticket's current pipeline: it is locked while the run is
    active, so it is the one validated at dispatch."""
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (run["ticket_id"],)).fetchone()
    pipeline = ticket_pipeline(ticket)
    by_id = {s.id: s for s in pipeline.stages}
    stages: list[Stage] = []
    for stage_id in json.loads(run["stage_ids"]):
        if stage_id not in by_id:
            raise ApiError(422, "invalid_stage", f"stage {stage_id}: no longer in the pipeline")
        stages.append(by_id[stage_id])
    return build_manifest(
        run["id"], ticket_ref(ticket), stages, current_bundle(conn, ticket["id"]), runtimes
    )


# ------------------------------------------------------------------ worker-token routes


def _worker_run(conn: Conn, worker_token: str) -> Row:
    """The run bound to this worker token (any status). 401 if none."""
    run: Row | None = conn.execute(
        "SELECT * FROM runs WHERE worker_token_hash = ?", (token_hash(worker_token),)
    ).fetchone()
    if run is None:
        raise _unauthorized("worker")
    return run


def _live_run(conn: Conn, worker_token: str) -> Row:
    run = _worker_run(conn, worker_token)
    if run["status"] in TERMINAL:
        raise _gone(run["id"], run["status"])
    return run


def post_events(db: Database, worker_token: str, events: Sequence[RunEvent]) -> bool:
    """Store events idempotently on `(run_id, seq)`, move stage_runs along, refresh
    `last_seen_at` (an empty batch is the heartbeat). Returns whether a cancel was requested.

    Beyond MAX_EVENTS_PER_RUN stored events, `log` events are dropped (the worker already
    replaces its own overflow with one `warning`; this is the server-side backstop, logged here
    rather than stored, since a server-chosen seq could collide with the worker's).

    Stage times are the events' own `ts` (a batch spans up to a second), capped at the server's
    clock so a skewed runner can't write future times.
    """
    now_dt = utcnow()
    now = to_db(now_dt)
    with db.tx() as conn:
        run = _live_run(conn, worker_token)
        run_id = run["id"]
        stored: int = conn.execute(
            "SELECT COUNT(*) FROM run_events WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        dropped = 0
        for ev in events:
            if ev.type == "log" and stored >= MAX_EVENTS_PER_RUN:
                dropped += 1
                continue
            cursor = conn.execute(
                "INSERT INTO run_events(run_id, seq, ts, stage_id, type, data) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(run_id, seq) DO NOTHING",
                (run_id, ev.seq, to_db(ev.ts), ev.stage_id, ev.type, ev.data.model_dump_json()),
            )
            stored += cursor.rowcount
            if ev.stage_id is None:
                continue
            at = to_db(min(ev.ts, now_dt))
            if ev.type == "stage_started":
                conn.execute(
                    "UPDATE stage_runs SET status = 'running', started_at = ? "
                    "WHERE run_id = ? AND idx = (SELECT MIN(idx) FROM stage_runs "
                    "  WHERE run_id = ? AND stage_id = ? AND status = 'pending')",
                    (at, run_id, run_id, ev.stage_id),
                )
            elif ev.type == "stage_finished":
                # The running stage_run of that stage (else a pending one: a missed start).
                conn.execute(
                    "UPDATE stage_runs SET status = ?, exit_code = ?, reason = ?, finished_at = ? "
                    "WHERE run_id = ? AND idx = (SELECT idx FROM stage_runs "
                    "  WHERE run_id = ? AND stage_id = ? AND status IN ('running', 'pending') "
                    "  ORDER BY status = 'running' DESC, idx LIMIT 1)",
                    (
                        ev.data.status,
                        ev.data.exit_code,
                        ev.data.reason,
                        at,
                        run_id,
                        run_id,
                        ev.stage_id,
                    ),
                )
        if dropped:
            log.warning("run %s: over %s events, dropped %s log events", run_id, stored, dropped)
        conn.execute("UPDATE runs SET last_seen_at = ? WHERE id = ?", (now, run_id))
        # publish: run {key, id} when a stage started or finished
        return run["cancel_requested_at"] is not None


def sync(
    db: Database, worker_token: str, stage_id: str, files: Sequence[SyncFile]
) -> dict[str, int]:
    """Write the worker's context files back as new versions authored by the run (SPEC §9.4.1).

    Each path must be a `sync=true` manifest file (keeping its mode) or lie under a manifest
    `sync_dirs` entry, and pass `validate_path`. Any invalid entry → 422 for the whole request,
    nothing written. Returns each path's latest version (0 = absent: deleting a missing file).
    """
    with db.tx() as conn:
        run = _live_run(conn, worker_token)
        if stage_id not in json.loads(run["stage_ids"]):
            raise ApiError(
                422, "validation_error", f"stage_id: {stage_id!r} is not a stage of this run"
            )
        manifest = Manifest.model_validate_json(run["manifest"])
        entries = [_check_sync_entry(manifest, f) for f in files]
        versions: dict[str, int] = {}
        for path, f in entries:
            version = write_version(
                conn,
                run["ticket_id"],
                path,
                f.content,
                f.mode,
                author_run_id=run["id"],
                author_stage=stage_id,
            )
            versions[path] = version or 0
        # publish: ticket {key} (files changed)
        return versions


def _check_sync_entry(manifest: Manifest, f: SyncFile) -> tuple[str, SyncFile]:
    path = validate_path(f.path, "files.path")
    key = path_key(path)
    known: ManifestFile | None = next((m for m in manifest.files if path_key(m.path) == key), None)
    if known is not None:
        if not known.sync:
            raise ApiError(422, "validation_error", f"files.path: {path!r} is read-only (library)")
        if f.content is not None and f.mode != known.mode:
            raise ApiError(
                422,
                "validation_error",
                f"files.mode: {path!r} is a {known.mode}-mode file; its mode can't change",
            )
        return known.path, f.model_copy(update={"mode": known.mode})
    if not any(key.startswith(path_key(d) + "/") for d in manifest.sync_dirs):
        raise ApiError(
            422, "validation_error", f"files.path: {path!r} is not a context file of this run"
        )
    return path, f


def complete(db: Database, worker_token: str, body: CompleteRequest) -> None:
    """running → gated|succeeded (completed, per `completion_status`), failed or cancelled.
    Repeating the final status of a terminal run is a no-op; anything else on a terminal run →
    410."""
    now = utcnow()
    with db.tx() as conn:
        run = _worker_run(conn, worker_token)
        if run["status"] in TERMINAL:
            if run["status"] in _FINAL[body.status]:
                return
            raise _gone(run["id"], run["status"])
        status: RunStatus
        error = body.error
        if body.status == "completed":
            ticket = conn.execute(
                "SELECT pipeline FROM tickets WHERE id = ?", (run["ticket_id"],)
            ).fetchone()
            status = completion_status(ticket_pipeline(ticket), json.loads(run["stage_ids"]))
        elif body.status == "failed":
            status = "failed"
            if not error:
                error = (
                    f"stage {body.failed_stage} failed" if body.failed_stage else "the run failed"
                )
        else:
            status = "cancelled"
        if finish(conn, run["id"], status, ("running",), now=now, error=error):
            _store_pr_url(conn, run, body.pr_url)


def _store_pr_url(conn: Conn, run: Row, pr_url: str | None) -> None:
    """Remember the PR the worker found for the ticket's branch. No URL leaves the stored one;
    an invalid one is logged and ignored (it never fails the completion)."""
    if pr_url is None:
        return
    try:
        url = validate_pr_url(pr_url)
    except ApiError as exc:
        log.warning("run %s: ignoring the reported PR URL (%s)", run["id"], exc.message)
        return
    conn.execute("UPDATE tickets SET pr_url = ? WHERE id = ?", (url, run["ticket_id"]))
