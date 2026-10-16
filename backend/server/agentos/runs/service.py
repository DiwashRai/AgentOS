"""Runs: dispatch, reads, cancel and the run state machine (SPEC §8).

Every transition is a guarded `UPDATE … WHERE id = ? AND status IN (…)`; zero rows means the run
was elsewhere already (a no-op or a conflict, the caller decides). Terminal side effects
(`finish`): unfinished stage_runs → `cancelled`, `finished_at` set, the ticket's `updated_at`
bumped. The dispatcher and the reaper (loops) and the worker API (the client lease included) call
the primitives here.

A run's job is built when it is dispatched (`build_job`). Job tokens are stored only as SHA-256
digests, never cleared (a token's validity follows the run's status).
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Collection, Sequence
from datetime import datetime, timedelta

from pydantic import TypeAdapter

from agentos.auth.service import hash_token
from agentos.clients.service import has_client, online_user_ids
from agentos.clock import from_db, from_db_opt, to_db, utcnow
from agentos.db import Conn, Database, IntegrityError, Row
from agentos.errors import ApiError, field_error
from agentos.files.service import current_bundle
from agentos.models import (
    DispatchTarget,
    JobPackage,
    LeasedJob,
    ManifestJob,
    ManifestJournal,
    ManifestReport,
    Me,
    Pipeline,
    Route,
    RunDetail,
    RunEvent,
    RunStatus,
    RunSummary,
    StageRunInfo,
    StopReason,
    SyncBackInfo,
)
from agentos.pipeline.manifest import build_package, render_stages
from agentos.pipeline.runtime import RuntimesConfig
from agentos.pipeline.segment import check_segment, plan_run
from agentos.pipeline.space import SpaceConfig
from agentos.space_store import load_snapshot
from agentos.templates.service import current_defaults
from agentos.tickets.board import RunFact, next_stage
from agentos.tickets.service import (
    active_run_id,
    get_ticket_row,
    run_fact,
    run_summary,
    run_ticket_ref,
    stage_run_fact,
    ticket_pipeline,
)

ACTIVE: tuple[RunStatus, ...] = ("queued", "dispatched", "running")
TERMINAL: tuple[RunStatus, ...] = ("succeeded", "gated", "failed", "cancelled")

# A job's allowance past its time budget (clone, publish), plus the publish job's for a file
# report; a pull job's allowance to start after the lease.
BUDGET_GRACE = timedelta(minutes=15)
FILE_GRACE = timedelta(minutes=30)
PULL_START_TIMEOUT = timedelta(minutes=2)
NEVER_STARTED = "never started"
OVER_BUDGET = "time budget exceeded"

# A client run: its owner's client leases it and it reports live.
CLIENT_ROUTE = Route("pull", None, "live")

# How a run ends when its stopped job reports `cancelled`: a user's cancel (or no stop) →
# cancelled; a stop the server made → failed with its reason.
STOPPED: dict[StopReason | None, tuple[RunStatus, str | None]] = {
    None: ("cancelled", None),
    "cancel": ("cancelled", None),
    "budget": ("failed", OVER_BUDGET),
    "never_started": ("failed", NEVER_STARTED),
}

EVENT_ADAPTER: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)

_SELECT_RUN = (
    "SELECT r.*, t.key AS ticket_key, t.pipeline AS ticket_pipeline, "
    "u.display_name AS creator_name, u.avatar_url AS creator_avatar "
    "FROM runs r JOIN tickets t ON t.id = r.ticket_id LEFT JOIN users u ON u.id = r.created_by "
)
_SQL_RUN = _SELECT_RUN + "WHERE r.id = ?"
_SQL_TICKET_RUNS = _SELECT_RUN + "WHERE r.ticket_id = ? ORDER BY r.id DESC"
_SQL_STAGE_RUNS = (
    "SELECT idx, stage_id, round, status, verdict, exit_code, reason, started_at, finished_at "
    "FROM stage_runs WHERE run_id = ? ORDER BY idx"
)
# Queued runs, oldest first, each with `fence`: the latest earlier run of its ticket whose job may
# still publish (params: now, then those of the WHERE part appended).
_SQL_QUEUED = (
    "SELECT r.id, r.provider, r.queue_reason, (SELECT MAX(p.id) FROM runs p JOIN jobs j "
    "ON j.run_id = p.id WHERE p.ticket_id = r.ticket_id AND p.id < r.id AND j.ended_at IS NULL "
    "AND j.not_after > ?) AS fence FROM runs r WHERE r.status = 'queued' AND "
)
_SQL_QUEUED_PUSH = _SQL_QUEUED + "r.start = 'push' AND r.report = ? ORDER BY r.id"
_SQL_QUEUED_PULL = _SQL_QUEUED + "r.start = 'pull' AND r.created_by = ? ORDER BY r.id"


# ------------------------------------------------------------------ row → model helpers


def run_row(conn: Conn, run_id: int) -> Row:
    """The `runs` row plus `ticket_key`, `ticket_pipeline` and the creator. 404 `run_not_found`."""
    row: Row | None = conn.execute(_SQL_RUN, (run_id,)).fetchone()
    if row is None:
        raise ApiError(404, "run_not_found", f"No run {run_id}")
    return row


def _summaries(conn: Conn, rows: Sequence[Row]) -> list[RunSummary]:
    """`run_row`-shaped rows → summaries, with one query for whose clients are online."""
    creators = [r["created_by"] for r in rows if r["start"] == "pull" and r["created_by"]]
    online = online_user_ids(conn, creators, utcnow()) if creators else set()
    return [run_summary(r, r["ticket_key"], online) for r in rows]


def _summary(conn: Conn, row: Row) -> RunSummary:
    return _summaries(conn, [row])[0]


def _detail(conn: Conn, row: Row) -> RunDetail:
    """Stage names come from the ticket's current pipeline, falling back to the stage id."""
    pipeline = Pipeline.model_validate_json(row["ticket_pipeline"])
    names = {s.id: s.name for s in pipeline.stages}
    stage_runs = [
        StageRunInfo(
            stage_id=sr["stage_id"],
            name=names.get(sr["stage_id"], sr["stage_id"]),
            idx=sr["idx"],
            round=sr["round"],
            status=sr["status"],
            verdict=sr["verdict"],
            exit_code=sr["exit_code"],
            reason=sr["reason"],
            started_at=from_db_opt(sr["started_at"]),
            finished_at=from_db_opt(sr["finished_at"]),
        )
        for sr in conn.execute(_SQL_STAGE_RUNS, (row["id"],)).fetchall()
    ]
    archive = conn.execute(
        "SELECT files, bytes FROM run_sync_back WHERE run_id = ?", (row["id"],)
    ).fetchone()
    sync_back = SyncBackInfo(files=archive["files"], bytes=archive["bytes"]) if archive else None
    return RunDetail(**_summary(conn, row).model_dump(), stage_runs=stage_runs, sync_back=sync_back)


def _latest_run_fact(conn: Conn, ticket_id: int) -> RunFact | None:
    run = conn.execute(_SQL_TICKET_RUNS + " LIMIT 1", (ticket_id,)).fetchone()
    if run is None:
        return None
    stage_runs = conn.execute(_SQL_STAGE_RUNS, (run["id"],)).fetchall()
    return run_fact(run, [stage_run_fact(sr) for sr in stage_runs])


# ------------------------------------------------------------------ dispatch


def dispatch(
    db: Database,
    runtimes: RuntimesConfig,
    space: SpaceConfig,
    server_route: Route | None,
    key: str,
    user: Me,
    from_stage: str | None,
    target: DispatchTarget = "server",
) -> RunSummary:
    """Queue a run (SPEC §8 "Dispatch"), all in one transaction: a 422 leaves no run row.

    "Continue" and "Retry" are dispatches with the default `from_stage` (`board.next_stage`);
    "Run from…" passes one. The package is built later, at dispatch. A ticket whose repo is no
    longer in the Space's catalog → 422 `validation_error`; a repo ticket without a base branch
    → 409 `base_branch_missing` (`run_ticket_ref`; the board's `dispatch_block`).

    The run covers `plan_run`'s stages (`stage_ids`): from `from_stage` on, plus the start of the
    loop block it is in (run from round 2 on), with a time budget for every round.

    The run stores its route. `target` "server" takes `server_route` (None: 422
    `validation_error`, this server starts no workers); "client" queues it for `user`'s own
    client (`CLIENT_ROUTE`, taken by `take_client_run`), which only the ticket's owner may do
    (403 `forbidden`), and only with a client token (422 `validation_error`).
    """
    now = to_db(utcnow())
    route = CLIENT_ROUTE if target == "client" else server_route
    if route is None:
        raise field_error(["target"], "No runner is configured on this server; use your client.")

    with db.tx() as conn:
        ticket = get_ticket_row(conn, key)
        if target == "client" and ticket["owner_id"] != user.id:
            raise ApiError(403, "forbidden", f"Only {key}'s owner can run it on their client")
        if target == "client" and not has_client(conn, user.id):
            raise field_error(["target"], "Create a client in Settings > My client first.")
        if ticket["state"] != "open":
            raise ApiError(409, "invalid_stage", f"{key} is {ticket['state']}; reopen it first")
        ref = run_ticket_ref(ticket, space)
        if active_run_id(conn, ticket["id"]) is not None:
            raise ApiError(409, "run_active", f"{key} already has an active run")
        pipeline = ticket_pipeline(ticket)
        if from_stage is None:
            from_stage = next_stage(pipeline, _latest_run_fact(conn, ticket["id"]))
            if from_stage is None:
                raise ApiError(422, "invalid_stage", "nothing to run; pick a stage")
        plan = plan_run(pipeline, from_stage)
        check_segment(plan.stages, ref.workspace_mode)
        try:
            cursor = conn.execute(
                "INSERT INTO runs(ticket_id, status, from_stage, stage_ids, time_budget_s, start, "
                "provider, report, created_by, created_at) "
                "VALUES (?, 'queued', ?, '[]', 0, ?, ?, ?, ?, ?)",
                (ticket["id"], from_stage, *route, user.id, now),
            )
        except IntegrityError as exc:  # runs_one_active
            raise ApiError(409, "run_active", f"{key} already has an active run") from exc
        run_id = cursor.lastrowid
        if run_id is None:  # pragma: no cover - sqlite always sets it after an INSERT
            raise RuntimeError("INSERT INTO runs returned no rowid")
        # Validate now (render errors → 422, rolling the run back); the job is built at dispatch.
        render_stages(
            run_id, ref, plan.stages, runtimes, defaults=current_defaults(conn), loops=plan.loops
        )
        ids = [s.id for s in plan.stages]
        conn.execute(
            "UPDATE runs SET stage_ids = ?, time_budget_s = ? WHERE id = ?",
            (json.dumps(ids), plan.time_budget_s(), run_id),
        )
        # Round 1's stage_runs; later rounds of a loop get theirs when they start (ingest).
        conn.executemany(
            "INSERT INTO stage_runs(run_id, idx, stage_id, status) VALUES (?, ?, ?, 'pending')",
            [(run_id, i, stage_id) for i, stage_id in enumerate(ids[ids.index(from_stage) :])],
        )
        conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, ticket["id"]))
        return _summary(conn, run_row(conn, run_id))


# ------------------------------------------------------------------ reads


def list_runs(db: Database, key: str) -> list[RunSummary]:
    """The ticket's runs, newest first."""
    with db.read() as conn:
        ticket = get_ticket_row(conn, key)
        rows = conn.execute(_SQL_TICKET_RUNS, (ticket["id"],)).fetchall()
        return _summaries(conn, rows)


def get_run(db: Database, run_id: int) -> RunDetail:
    with db.read() as conn:
        return _detail(conn, run_row(conn, run_id))


def get_manifest(db: Database, run_id: int) -> JobPackage:
    """The run's job package exactly as sent to the worker. 404 `not_found` until the run is
    dispatched."""
    with db.read() as conn:
        run_row(conn, run_id)
        row = conn.execute("SELECT package FROM jobs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", f"Run {run_id} has no package until it is dispatched")
    return JobPackage.model_validate_json(row["package"])


def get_sync_back(db: Database, run_id: int) -> bytes:
    """The zip the run's worker returned from sync_back. 404 `not_found` when there is none."""
    with db.read() as conn:
        run_row(conn, run_id)
        row = conn.execute("SELECT data FROM run_sync_back WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise ApiError(404, "not_found", f"Run {run_id} returned no sync_back")
    return bytes(row["data"])


def run_events(
    db: Database, run_id: int, after_seq: int | None, before_seq: int | None, limit: int
) -> list[RunEvent]:
    """Events in ascending `seq`: the first `limit` after `after_seq`, else the last `limit`
    (before `before_seq` when given)."""
    with db.read() as conn:
        run_row(conn, run_id)
        if after_seq is not None:
            rows = conn.execute(
                "SELECT seq, ts, stage_id, type, data FROM run_events "
                "WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (run_id, after_seq, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT seq, ts, stage_id, type, data FROM run_events "
                "WHERE run_id = ? AND (? IS NULL OR seq < ?) ORDER BY seq DESC LIMIT ?",
                (run_id, before_seq, before_seq, limit),
            ).fetchall()[::-1]
    return [
        EVENT_ADAPTER.validate_python(
            {
                "seq": r["seq"],
                "ts": from_db(r["ts"]),
                "stage_id": r["stage_id"],
                "type": r["type"],
                "data": json.loads(r["data"]),
            }
        )
        for r in rows
    ]


# ------------------------------------------------------------------ cancel


def cancel(db: Database, run_id: int) -> RunSummary:
    """queued/dispatched → `cancelled` at once (a late `started` gets 410). running → stop the
    run and its job once (reason `cancel`); the worker sees `cancel: true` on its next journal
    post and completes as cancelled (the reaper ends it after its stop grace otherwise). A
    terminal run is returned unchanged."""
    now = utcnow()
    with db.tx() as conn:
        row = run_row(conn, run_id)
        if row["status"] in ("queued", "dispatched"):
            finish(conn, run_id, "cancelled", ("queued", "dispatched"), now=now)
        elif row["status"] == "running":
            stop(conn, run_id, "cancel", now)
        return _summary(conn, run_row(conn, run_id))


def stop(conn: Conn, run_id: int, reason: StopReason, now: datetime) -> None:
    """Stop an active run and its job once, with `reason`, in the caller's transaction: its
    worker sees `cancel: true` in its next journal reply."""
    params = (to_db(now), reason, run_id)
    cursor = conn.execute(
        "UPDATE runs SET stop_requested_at = ?, stop_reason = ? "
        "WHERE id = ? AND status IN ('dispatched', 'running') AND stop_requested_at IS NULL",
        params,
    )
    if cursor.rowcount:
        conn.execute(
            "UPDATE jobs SET stop_requested_at = ?, stop_reason = ? "
            "WHERE run_id = ? AND stop_requested_at IS NULL",
            params,
        )


# ------------------------------------------------------------------ state machine primitives


def finish(
    conn: Conn,
    run_id: int,
    status: RunStatus,
    from_statuses: Sequence[RunStatus],
    *,
    now: datetime,
    error: str | None = None,
) -> bool:
    """Guarded terminal transition + side effects, in the caller's transaction.

    Returns False (and changes nothing) if the run's status isn't one of `from_statuses`.
    """
    if status not in TERMINAL:
        raise ValueError(f"{status} is not a terminal status")
    stamp = to_db(now)
    cursor = conn.execute(
        "UPDATE runs SET status = ?, error = COALESCE(?, error), finished_at = ? "
        "WHERE id = ? AND status IN (SELECT value FROM json_each(?))",
        (status, error, stamp, run_id, json.dumps(list(from_statuses))),
    )
    if cursor.rowcount == 0:
        return False
    conn.execute(
        "UPDATE stage_runs SET status = 'cancelled', finished_at = ? "
        "WHERE run_id = ? AND status IN ('pending', 'running')",
        (stamp, run_id),
    )
    conn.execute(
        "UPDATE tickets SET updated_at = ? WHERE id = (SELECT ticket_id FROM runs WHERE id = ?)",
        (stamp, run_id),
    )
    return True


# ------------------------------------------------------------------ dispatcher primitives


def not_after(route: Route, dispatched: datetime, budget_s: int, start_timeout: int) -> datetime:
    """When a job's worker may no longer publish: its dispatch + the start allowance (push: the
    start timeout; pull: PULL_START_TIMEOUT) + its budget + BUDGET_GRACE (+ FILE_GRACE)."""
    start = timedelta(seconds=start_timeout) if route.start == "push" else PULL_START_TIMEOUT
    overhead = BUDGET_GRACE + (FILE_GRACE if route.report == "file" else timedelta())
    return dispatched + start + timedelta(seconds=budget_s) + overhead


def build_job(
    conn: Conn, run: Row, report: ManifestReport, now: datetime, client_id: int | None = None
) -> tuple[str, str] | None:
    """Build and store the job of a run just dispatched (by `client_id`, for pull), in the
    caller's transaction, from the ticket, its bundle and the Space as they are now. Returns the
    job id and its token (in plaintext only here; never logged). A build failure (a repo gone
    from the catalog, a changed pipeline) fails the run through `finish()` and returns None."""
    snapshot = load_snapshot(conn)
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (run["ticket_id"],)).fetchone()
    budget_s: int = run["time_budget_s"]
    route = Route(run["start"], run["provider"], run["report"])
    job = ManifestJob(
        id=f"{run['id']}-1-{secrets.token_hex(8)}",
        run_id=run["id"],
        segment=1,
        segments=1,
        budget_s=budget_s,
        not_after=not_after(route, now, budget_s, snapshot.space.claim_timeout_s),
    )
    journal = ManifestJournal(first_seq=run["journal_seq"] + 1)

    try:
        plan = plan_run(ticket_pipeline(ticket), run["from_stage"])
        if [s.id for s in plan.stages] != json.loads(run["stage_ids"]):
            raise ApiError(
                422, "invalid_stage", "the pipeline changed since the run was dispatched"
            )
        package = build_package(
            run["id"],
            run_ticket_ref(ticket, snapshot.space),
            plan,
            current_bundle(conn, ticket["id"]),
            snapshot.runtimes,
            defaults=current_defaults(conn),
            job=job,
            journal=journal,
            report=report,
        )
    except ApiError as exc:
        error = f"cannot build the manifest: {exc.message}"
        finish(conn, run["id"], "failed", ("dispatched",), now=now, error=error)
        return None

    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO jobs(id, run_id, segment, token_hash, package, first_seq, budget_s, "
        "not_after, created_at, client_id) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
        (
            job.id,
            run["id"],
            hash_token(token),
            package.model_dump_json(),
            journal.first_seq,
            budget_s,
            to_db(job.not_after),
            to_db(now),
            client_id,
        ),
    )
    return job.id, token


def _next_queued(
    conn: Conn, sql: str, param: str | int, now: datetime, providers: Collection[str] | None
) -> tuple[Row | None, list[tuple[str, int]]]:
    """The oldest queued run `sql` selects that may start now, and `(queue_reason, id)` for each
    older one that waits for a new reason: the fence (its ticket's earlier job may still
    publish) or, for push (`providers` given), a provider that isn't configured."""
    held: list[tuple[str, int]] = []
    for row in conn.execute(sql, (to_db(now), param)).fetchall():
        if row["fence"] is not None:
            reason = f"waiting for run #{row['fence']}'s worker to stop"
        elif providers is not None and row["provider"] not in providers:
            reason = f"its runner ({row['provider']}) is not configured on this server"
        else:
            return row, held
        if reason != row["queue_reason"]:
            held.append((reason, row["id"]))
    return None, held


def mark_next_dispatched(
    db: Database, providers: Collection[str], report: ManifestReport
) -> tuple[int, str, tuple[str, str] | None] | None:
    """Move the oldest `queued` push run reporting `report.mode` that may start (`_next_queued`)
    to `dispatched` (clearing its `queue_reason`) and build its job. Returns the run id, its
    provider and `build_job`'s answer."""
    now = utcnow()
    with db.tx() as conn:
        row, held = _next_queued(conn, _SQL_QUEUED_PUSH, report.mode, now, providers)
        conn.executemany("UPDATE runs SET queue_reason = ? WHERE id = ?", held)
        if row is None:
            return None
        run_id: int = row["id"]
        cursor = conn.execute(
            "UPDATE runs SET status = 'dispatched', dispatched_at = ?, queue_reason = NULL "
            "WHERE id = ? AND status = 'queued'",
            (to_db(now), run_id),
        )
        if cursor.rowcount == 0:  # pragma: no cover - BEGIN IMMEDIATE serialises writers
            return None
        return run_id, row["provider"], build_job(conn, run_row(conn, run_id), report, now)


def take_client_run(
    db: Database, user_id: int, client_id: int, launch_ref: str, server_url: str
) -> LeasedJob | None:
    """Hand the user's oldest client run that may start (`_next_queued`) to their leasing client
    `client_id`: → `dispatched`, storing `launch_ref` (its name), and build its job (reporting to
    `server_url`). Returns it, or None. It takes the write lock only when a run may start or a
    `queue_reason` changes, so a waiting lease never holds it."""
    now = utcnow()
    with db.read() as conn:
        row, held = _next_queued(conn, _SQL_QUEUED_PULL, user_id, now, None)
    if row is None and not held:
        return None

    with db.tx() as conn:
        row, held = _next_queued(conn, _SQL_QUEUED_PULL, user_id, now, None)
        conn.executemany("UPDATE runs SET queue_reason = ? WHERE id = ?", held)
        if row is None:
            return None
        run_id: int = row["id"]
        cursor = conn.execute(
            "UPDATE runs SET status = 'dispatched', dispatched_at = ?, launch_ref = ?, "
            "queue_reason = NULL WHERE id = ? AND status = 'queued'",
            (to_db(now), launch_ref, run_id),
        )
        if cursor.rowcount == 0:  # pragma: no cover - BEGIN IMMEDIATE serialises writers
            return None
        report = ManifestReport(mode="live", url=server_url)
        built = build_job(conn, run_row(conn, run_id), report, now, client_id)
    if built is None:
        return None
    return LeasedJob(job_id=built[0], run_id=run_id, server_url=server_url, job_token=built[1])


def start(db: Database, run_id: int) -> str | None:
    """dispatched → running, for a run the server starts itself (no worker binds to it). Returns
    its stored package, or None if the run isn't dispatched (any more)."""
    stamp = to_db(utcnow())
    with db.tx() as conn:
        cursor = conn.execute(
            "UPDATE runs SET status = 'running', started_at = ?, last_seen_at = ? "
            "WHERE id = ? AND status = 'dispatched'",
            (stamp, stamp, run_id),
        )
        if cursor.rowcount == 0:
            return None
        row = conn.execute("SELECT package FROM jobs WHERE run_id = ?", (run_id,)).fetchone()
    return str(row["package"])


def set_launch_ref(db: Database, run_id: int, launch_ref: str) -> None:
    """Store the provider's ref of the started worker (the run's `launch_url` derives from it)."""
    with db.tx() as conn:
        conn.execute("UPDATE runs SET launch_ref = ? WHERE id = ?", (launch_ref, run_id))


def fail_start(db: Database, run_id: int, job_id: str, error: str) -> None:
    """The provider didn't start the job: the run fails and the job has ended (nothing ran)."""
    now = utcnow()
    with db.tx() as conn:
        finish(conn, run_id, "failed", ("dispatched",), now=now, error=error)
        conn.execute("UPDATE jobs SET ended_at = ? WHERE id = ?", (to_db(now), job_id))


# ------------------------------------------------------------------ reaper primitives


def touch_running(db: Database, now: datetime) -> int:
    """Startup / sleep grace: every running run counts as just seen. Returns how many."""
    with db.tx() as conn:
        cursor = conn.execute(
            "UPDATE runs SET last_seen_at = ? WHERE status = 'running'", (to_db(now),)
        )
        return cursor.rowcount


def active_jobs(db: Database) -> list[Row]:
    """Every job that hasn't ended (those of dispatched and running runs, and of ended runs whose
    worker may still publish), with its run's route and clocks and its client's last lease."""
    with db.read() as conn:
        rows: list[Row] = conn.execute(
            "SELECT r.id AS run_id, r.status, r.start, r.provider, r.report, r.launch_ref, "
            "r.dispatched_at, r.started_at, r.last_seen_at, r.finished_at, r.stop_requested_at, "
            "r.stop_reason, j.id AS job_id, j.budget_s, j.not_after, j.alive_at, "
            "c.last_seen_at AS client_seen_at FROM runs r JOIN jobs j ON j.run_id = r.id "
            "LEFT JOIN client_tokens c ON c.id = j.client_id WHERE j.ended_at IS NULL ORDER BY r.id"
        ).fetchall()
    return rows


def end_job(db: Database, job_id: str, now: datetime) -> None:
    """The job's worker can no longer publish: the fence lifts for its ticket's next run."""
    with db.tx() as conn:
        conn.execute("UPDATE jobs SET ended_at = ? WHERE id = ?", (to_db(now), job_id))


def reap(
    db: Database,
    run_id: int,
    was: RunStatus,
    now: datetime,
    stop_reason: StopReason | None,
    status: RunStatus | None,
    error: str | None,
) -> bool:
    """One reaper verdict, in one transaction: stop the run (`stop_reason`), then end it
    (`status`, only if it is still `was`). Returns whether it ended."""
    with db.tx() as conn:
        if stop_reason is not None:
            stop(conn, run_id, stop_reason, now)
        return status is not None and finish(conn, run_id, status, (was,), now=now, error=error)
