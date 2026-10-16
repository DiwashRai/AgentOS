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
from dataclasses import dataclass
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

# Reaper thresholds (SPEC §8 "Reaper loop").
HEARTBEAT_TIMEOUT = timedelta(seconds=90)
BUDGET_GRACE = timedelta(minutes=15)
CANCEL_GRACE = timedelta(minutes=2)
NEVER_CONNECTED = "worker never connected"
WORKER_LOST = "worker lost"
OVER_BUDGET = "time budget exceeded"

# A client run: its owner's client leases it and it reports live.
CLIENT_ROUTE = Route("pull", None, "live")

# How a run ends when its stopped job reports `cancelled`: a user's cancel (or no stop) →
# cancelled; a stop the server made → failed with its reason.
STOPPED: dict[StopReason | None, tuple[RunStatus, str | None]] = {
    None: ("cancelled", None),
    "cancel": ("cancelled", None),
    "budget": ("failed", OVER_BUDGET),
    "never_started": ("failed", NEVER_CONNECTED),
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
# A user's oldest queued client run (param: the user id).
_SQL_QUEUED_CLIENT_RUN = (
    "SELECT id FROM runs WHERE start = 'pull' AND status = 'queued' AND created_by = ? "
    "ORDER BY id LIMIT 1"
)


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
    post and completes as cancelled (the reaper cancels it after CANCEL_GRACE otherwise). A
    terminal run is returned unchanged."""
    now = utcnow()
    with db.tx() as conn:
        row = run_row(conn, run_id)
        if row["status"] in ("queued", "dispatched"):
            finish(conn, run_id, "cancelled", ("queued", "dispatched"), now=now)
        elif row["status"] == "running":
            stop = (to_db(now), run_id)
            conn.execute(
                "UPDATE runs SET stop_requested_at = ?, stop_reason = 'cancel' "
                "WHERE id = ? AND status = 'running' AND stop_requested_at IS NULL",
                stop,
            )
            conn.execute(
                "UPDATE jobs SET stop_requested_at = ?, stop_reason = 'cancel' "
                "WHERE run_id = ? AND stop_requested_at IS NULL",
                stop,
            )
        return _summary(conn, run_row(conn, run_id))


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


def build_job(
    conn: Conn, run: Row, report: ManifestReport, now: datetime
) -> tuple[str, str] | None:
    """Build and store the job of a run just dispatched, in the caller's transaction, from the
    ticket, its bundle and the Space as they are now. Returns the job id and its token (in
    plaintext only here; never logged). A build failure (a repo gone from the catalog, a changed
    pipeline) fails the run through `finish()` and returns None, so the run is never launched."""
    snapshot = load_snapshot(conn)
    ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (run["ticket_id"],)).fetchone()
    budget_s: int = run["time_budget_s"]
    job = ManifestJob(
        id=f"{run['id']}-1-{secrets.token_hex(8)}",
        run_id=run["id"],
        segment=1,
        segments=1,
        budget_s=budget_s,
        not_after=now + timedelta(seconds=snapshot.space.claim_timeout_s + budget_s) + BUDGET_GRACE,
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
        "not_after, created_at) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)",
        (
            job.id,
            run["id"],
            hash_token(token),
            package.model_dump_json(),
            journal.first_seq,
            budget_s,
            to_db(job.not_after),
            to_db(now),
        ),
    )
    return job.id, token


def mark_next_dispatched(
    db: Database, providers: Collection[str], report: ManifestReport
) -> tuple[int, str, tuple[str, str] | None] | None:
    """Move the oldest `queued` push run reporting `report.mode` whose stored provider is one of
    `providers` to `dispatched` (clearing its `queue_reason`) and build its job. Queued push runs
    of that report mode whose provider isn't configured stay queued with a `queue_reason`.
    Returns the run id, its provider and `build_job`'s answer."""
    now = utcnow()
    params = (report.mode, json.dumps(list(providers)))
    with db.tx() as conn:
        conn.execute(
            "UPDATE runs SET queue_reason = 'its runner (' || provider || ') is not configured "
            "on this server' WHERE start = 'push' AND report = ? AND status = 'queued' "
            "AND provider NOT IN (SELECT value FROM json_each(?))",
            params,
        )
        row = conn.execute(
            "SELECT id, provider FROM runs WHERE start = 'push' AND report = ? "
            "AND status = 'queued' AND provider IN (SELECT value FROM json_each(?)) "
            "ORDER BY id LIMIT 1",
            params,
        ).fetchone()
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
    db: Database, user_id: int, launch_ref: str, server_url: str
) -> LeasedJob | None:
    """Hand the user's oldest `queued` client run to their leasing client: → `dispatched`,
    storing `launch_ref` (the client's name), and build its job (reporting to `server_url`).
    Returns it, or None if none is queued or the build failed. It reads first and takes the
    write lock only when a run is queued, so a waiting lease never holds it."""
    with db.read() as conn:
        if conn.execute(_SQL_QUEUED_CLIENT_RUN, (user_id,)).fetchone() is None:
            return None

    now = utcnow()
    with db.tx() as conn:
        row = conn.execute(_SQL_QUEUED_CLIENT_RUN, (user_id,)).fetchone()
        if row is None:
            return None
        run_id: int = row["id"]
        cursor = conn.execute(
            "UPDATE runs SET status = 'dispatched', dispatched_at = ?, launch_ref = ? "
            "WHERE id = ? AND status = 'queued'",
            (to_db(now), launch_ref, run_id),
        )
        if cursor.rowcount == 0:  # pragma: no cover - BEGIN IMMEDIATE serialises writers
            return None
        report = ManifestReport(mode="live", url=server_url)
        built = build_job(conn, run_row(conn, run_id), report, now)
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


@dataclass(frozen=True, slots=True)
class Reaped:
    run_id: int
    status: RunStatus
    reason: str


def touch_running(db: Database, now: datetime) -> int:
    """Startup / sleep grace: every running run counts as just seen. Returns how many."""
    with db.tx() as conn:
        cursor = conn.execute(
            "UPDATE runs SET last_seen_at = ? WHERE status = 'running'", (to_db(now),)
        )
        return cursor.rowcount


def reap(
    db: Database,
    now: datetime,
    claim_timeout_s: int,
    exempt_report: str | None = None,
) -> list[Reaped]:
    """Reaper rules 2-5 (SPEC §8), in order, in one transaction.

    Each rule keys on the run's own row: `queued` runs are never touched (a client run waits for
    its user's client), and a dispatched or running client run is reaped exactly like a local
    one. Runs whose `report` is `exempt_report` skip rules 3-5 (heartbeat, budget,
    unacknowledged cancel): the reaper passes "file" only while the mailbox reaper
    (`agentos.mailbox`) watches those runs, as they never send heartbeats.
    """
    out: list[Reaped] = []
    # Rules 3-5 end with `AND (? IS NULL OR report <> ?)`, bound to these two values.
    exempt = (exempt_report, exempt_report)
    with db.tx() as conn:

        def end(
            ids: Sequence[int], was: RunStatus, status: RunStatus, error: str | None, why: str
        ) -> None:
            for run_id in ids:
                if finish(conn, run_id, status, (was,), now=now, error=error):
                    out.append(Reaped(run_id, status, why))

        # 2. dispatched, never started
        stale = _ids(
            conn,
            "SELECT id FROM runs WHERE status = 'dispatched' AND dispatched_at < ?",
            (to_db(now - timedelta(seconds=claim_timeout_s)),),
        )
        end(stale, "dispatched", "failed", NEVER_CONNECTED, NEVER_CONNECTED)
        # 3. no heartbeat
        lost = _ids(
            conn,
            "SELECT id FROM runs WHERE status = 'running' "
            "AND COALESCE(last_seen_at, started_at) < ? AND (? IS NULL OR report <> ?)",
            (to_db(now - HEARTBEAT_TIMEOUT), *exempt),
        )
        end(lost, "running", "failed", WORKER_LOST, WORKER_LOST)
        # 4. over the time budget
        running = conn.execute(
            "SELECT id, started_at, time_budget_s FROM runs "
            "WHERE status = 'running' AND started_at IS NOT NULL "
            "AND (? IS NULL OR report <> ?)",
            exempt,
        ).fetchall()
        over = [
            int(r["id"])
            for r in running
            if from_db(r["started_at"]) + timedelta(seconds=r["time_budget_s"]) + BUDGET_GRACE < now
        ]
        end(over, "running", "failed", OVER_BUDGET, OVER_BUDGET)
        # 5. a cancel the worker never acknowledged
        unacked = _ids(
            conn,
            "SELECT id FROM runs WHERE status = 'running' AND stop_requested_at < ? "
            "AND (? IS NULL OR report <> ?)",
            (to_db(now - CANCEL_GRACE), *exempt),
        )
        end(unacked, "running", "cancelled", None, "cancel not acknowledged")
    return out


def active_run_ids(db: Database) -> set[int]:
    with db.read() as conn:
        return set(
            _ids(conn, "SELECT id FROM runs WHERE status IN ('queued', 'dispatched', 'running')")
        )


def _ids(conn: Conn, sql: str, params: Sequence[object] = ()) -> list[int]:
    return [int(r[0]) for r in conn.execute(sql, tuple(params)).fetchall()]
