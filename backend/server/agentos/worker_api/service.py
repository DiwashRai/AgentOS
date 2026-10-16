"""The worker protocol (SPEC §8 claim table, §10 worker API): the job and its journal, the
sync_back upload, the client poll, and the older claim, events, sync and complete routes.

A job token (one per job, built at dispatch) fetches the job's package (`job_package`) and binds
the job to the worker instance whose `started` record comes first (`post_journal`); every later
post carries that instance id. `ingest` applies the journal's records to the run in strict seq
order. Tokens and instance ids are looked up by their SHA-256 hash. A terminal run answers 410
`run_gone` (the worker aborts and never pushes), except a repeat of its final result.

The older routes take the worker token bound at claim and apply the same effects as ingest.

The poll authenticates with a client token (`agentos.clients`) and hands the client a run its user
dispatched to "My client", with its job token.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Sequence
from datetime import datetime

from pydantic import ValidationError

from agentos.clients import service as clients
from agentos.clients.service import Client
from agentos.clock import to_db, utcnow
from agentos.db import Conn, Database, IntegrityError, Row
from agentos.errors import ApiError, field_error, validation_error
from agentos.files.service import write_version
from agentos.launchers.local import WORKER_PATH
from agentos.models import (
    ClientRunAssignment,
    JobPackage,
    JournalRecord,
    JournalResponse,
    Mode,
    PushedData,
    ResultData,
    RunEvent,
    RunStatus,
    StartedData,
    SyncBackData,
    SyncData,
    SyncFile,
    SyncResponse,
)
from agentos.pipeline.segment import completion_status
from agentos.runs import service as runs
from agentos.runs.service import EVENT_ADAPTER, TERMINAL, finish, token_hash
from agentos.tickets.service import ticket_pipeline
from agentos.validate import MAX_SYNC_BACK_BYTES as MAX_SYNC_BACK_BYTES  # the route caps reads
from agentos.validate import parsed, path_key, pydantic_issues, validate_pr_url

log = logging.getLogger(__name__)

MAX_EVENTS_PER_RUN = 100_000  # SPEC §6; beyond it `log` events are dropped

# The run statuses a repeated final result may find (by `ResultData.status`).
_FINAL: dict[str, frozenset[RunStatus]] = {
    "completed": frozenset({"gated", "succeeded"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
}

# The journal record types stored as `run_events` rows (validated as `RunEvent`).
_DISPLAY = frozenset(
    {"stage_started", "stage_finished", "log", "warning", "verdict", "verdict_repair"}
)


def _gone(run_id: int, status: str) -> ApiError:
    return ApiError(410, "run_gone", f"Run {run_id} is {status}")


def _unauthorized(what: str) -> ApiError:
    return ApiError(401, "unauthorized", f"Unknown {what} token")


# ------------------------------------------------------------------ the job and its journal


def _job_run(conn: Conn, job_token: str, instance: str) -> tuple[Row, Row]:
    """The job of this job token and its run (any status). 401 for an unknown token; 409
    `already_claimed` when the job is bound to another instance, or its run was started without
    one (by the server, or through the claim)."""
    job: Row | None = conn.execute(
        "SELECT * FROM jobs WHERE token_hash = ?", (token_hash(job_token),)
    ).fetchone()
    if job is None:
        raise _unauthorized("job")

    run: Row = conn.execute("SELECT * FROM runs WHERE id = ?", (job["run_id"],)).fetchone()
    bound = job["instance_hash"]
    if (bound is None and run["status"] == "running") or bound not in (None, token_hash(instance)):
        raise ApiError(409, "already_claimed", f"Job {job['id']} is bound to another worker")
    return job, run


def job_package(db: Database, job_token: str, instance: str) -> str:
    """The job's stored package (JSON), while its run is dispatched, or running and bound to this
    instance. 401 / 409 as `_job_run`; 410 once the run has ended."""
    with db.read() as conn:
        job, run = _job_run(conn, job_token, instance)
    if run["status"] in TERMINAL:
        raise _gone(run["id"], run["status"])
    package: str = job["package"]
    return package


def post_journal(
    db: Database, job_token: str, instance: str, records: Sequence[JournalRecord]
) -> JournalResponse:
    """Apply a batch of the job's journal (`ingest`) and refresh `last_seen_at`; an empty batch
    is the heartbeat.

    An unbound job binds to `instance` when the batch's first new record is a valid `started`
    (else 422 `validation_error`, nothing bound; another protocol is refused the same way). A
    terminal run answers 410, except a batch whose only new record repeats the final `result`
    (or that holds only records applied already): 200, and only `journal_seq` may move.
    """
    now = utcnow()
    with db.tx() as conn:
        job, run = _job_run(conn, job_token, instance)
        new = [r for r in records if r.seq > run["journal_seq"]]
        if run["status"] in TERMINAL:
            repeats = [
                r
                for r in new
                if r.type == "result" and run["status"] in _FINAL.get(str(r.data.get("status")), ())
            ]
            if not records or len(new) > 1 or repeats != new:
                raise _gone(run["id"], run["status"])

        elif job["instance_hash"] is None:
            if not instance:
                raise ApiError(401, "unauthorized", "Missing X-AgentOS-Instance")
            first = new[0] if new else None
            if first is None or first.type != "started" or first.seq != run["journal_seq"] + 1:
                raise field_error(["records"], "An unbound job's first record must be `started`.")
            _, issues = parsed(StartedData, first.data, ["records", records.index(first), "data"])
            if issues:
                raise validation_error(issues)
            conn.execute(
                "UPDATE jobs SET instance_hash = ? WHERE id = ? AND instance_hash IS NULL",
                (token_hash(instance), job["id"]),
            )

        response = ingest(conn, run, job, records, now)
        conn.execute(
            "UPDATE runs SET last_seen_at = ? WHERE id = ? AND status = 'running'",
            (to_db(now), run["id"]),
        )
    return response


def ingest(
    conn: Conn, run: Row, job: Row, records: Sequence[JournalRecord], now: datetime
) -> JournalResponse:
    """Apply a job's journal records to its run in strict seq order, in the caller's
    transaction. A seq at or below `runs.journal_seq` was applied already and is skipped; the
    next one is applied; a gap stops the batch (`ack` tells the worker where to resend from).
    `journal_seq` is written with the effects, so a batch commits whole or not at all, and
    nothing after a `result` applies.

    Display records become `run_events` rows at their own seq: the server never numbers a row.
    A record that can't apply (bad data, another job, a refused sync path, an unverified
    sync_back) is consumed: a `warning` row at its seq says why, it is listed in `rejected`, and
    it is logged.
    """
    seq: int = run["journal_seq"]
    rejected: list[int] = []
    events: list[RunEvent] = []  # display records, stored before the next other effect
    for record in records:
        if record.seq <= seq:
            continue
        if record.seq > seq + 1:
            break

        seq = record.seq
        note: str | None = None  # a warning for the record's seq slot
        try:
            if record.job != job["id"]:
                raise field_error(["job"], "Not this run's job.")
            if record.type in _DISPLAY:
                events.append(EVENT_ADAPTER.validate_python(record.model_dump(exclude={"job"})))
                continue
            _store_events(conn, run, events, now)
            events = []
            note = _apply(conn, run, record, now)
        except (ValidationError, ApiError) as exc:
            if isinstance(exc, ValidationError):
                exc = validation_error(pydantic_issues(exc.errors()))
            rejected.append(seq)
            note = f"The {record.type} record was rejected: {exc.message}"

        if note is not None:
            log.warning("run %s: record %s: %s", run["id"], seq, note)
            warning = record.model_dump(include={"seq", "ts", "stage_id"})
            warning |= {"type": "warning", "data": {"message": note}}
            events.append(EVENT_ADAPTER.validate_python(warning))
        if record.type == "result":
            break

    _store_events(conn, run, events, now)
    conn.execute("UPDATE runs SET journal_seq = ? WHERE id = ?", (seq, run["id"]))
    cancel = run["cancel_requested_at"] is not None
    return JournalResponse(ack=seq, cancel=cancel, rejected=rejected)


def _apply(conn: Conn, run: Row, record: JournalRecord, now: datetime) -> str | None:
    """The effect of one record that isn't a display record; returns a warning for its seq slot.
    Raises ApiError or ValidationError when it can't apply; what it wrote before (a sync's
    declared paths) stays."""
    if record.type == "started":
        started = StartedData.model_validate(record.data)
        conn.execute(
            "UPDATE runs SET status = 'running', started_at = ? "
            "WHERE id = ? AND status = 'dispatched'",
            (to_db(now), run["id"]),
        )
        if started.worker_sha256 != hashlib.sha256(WORKER_PATH.read_bytes()).hexdigest():
            return "This worker.py differs from the server's: update it."

    elif record.type == "sync":
        files = SyncData.model_validate(record.data).files
        refused = _sync(conn, run, record.stage_id, files).refused
        if refused:
            raise field_error(["files"], f"Not declared in the package: {', '.join(refused)}.")

    elif record.type == "sync_back":
        sent = SyncBackData.model_validate(record.data)
        stored = conn.execute(
            "SELECT sha256, bytes, files FROM run_sync_back WHERE run_id = ?", (run["id"],)
        ).fetchone()
        if stored is None or tuple(stored) != (sent.sha256.lower(), sent.bytes, sent.files):
            conn.execute("DELETE FROM run_sync_back WHERE run_id = ?", (run["id"],))
            raise field_error([], "It doesn't match the uploaded zip.")

    elif record.type == "pushed":
        head = PushedData.model_validate(record.data).head
        conn.execute("UPDATE runs SET head_commit = ? WHERE id = ?", (head, run["id"]))

    elif record.type == "result":
        _finish(conn, run, ResultData.model_validate(record.data), now)

    else:
        raise field_error(["type"], f"Unknown record type {record.type!r}.")
    return None


# ------------------------------------------------------------------ claim


def claim(db: Database, job_token: str, worker_token: str) -> JobPackage:
    """dispatched → running, binding the worker token, and returns the job's stored package. A
    safe retry with the same worker token returns it again; another worker token → 409
    `already_claimed`; terminal → 410."""
    worker_hash = token_hash(worker_token)
    stamp = to_db(utcnow())
    with db.tx() as conn:
        run = conn.execute(
            "SELECT r.*, j.package FROM jobs j JOIN runs r ON r.id = j.run_id "
            "WHERE j.token_hash = ?",
            (token_hash(job_token),),
        ).fetchone()
        if run is None:
            raise _unauthorized("job")
        if run["status"] == "running":
            if run["worker_token_hash"] != worker_hash:
                raise ApiError(409, "already_claimed", f"Run {run['id']} is already claimed")
            return JobPackage.model_validate_json(run["package"])
        if run["status"] != "dispatched":
            raise _gone(run["id"], run["status"])

        try:
            conn.execute(
                "UPDATE runs SET status = 'running', worker_token_hash = ?, started_at = ?, "
                "last_seen_at = ? WHERE id = ? AND status = 'dispatched'",
                (worker_hash, stamp, stamp, run["id"]),
            )
        except IntegrityError as exc:  # worker_token_hash is UNIQUE: a token reused across runs
            raise ApiError(409, "already_claimed", "That worker token is already in use") from exc
    return JobPackage.model_validate_json(run["package"])


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
    """Store events (`_store_events`) and refresh `last_seen_at` (an empty batch is the
    heartbeat). Returns whether a cancel was requested."""
    now = utcnow()
    with db.tx() as conn:
        run = _live_run(conn, worker_token)
        _store_events(conn, run, events, now)
        conn.execute("UPDATE runs SET last_seen_at = ? WHERE id = ?", (to_db(now), run["id"]))
    return run["cancel_requested_at"] is not None


def _store_events(conn: Conn, run: Row, events: Sequence[RunEvent], now: datetime) -> None:
    """Store events idempotently on `(run_id, seq)` and move stage_runs along.

    Beyond MAX_EVENTS_PER_RUN stored events, `log` events are dropped (the worker already
    replaces its own overflow with one `warning`; this is the server-side backstop, logged here
    rather than stored, since a server-chosen seq could collide with the worker's).

    Stage times are the events' own `ts` (a batch spans up to a second), capped at the server's
    clock so a skewed runner can't write future times. Only a newly stored event moves stage_runs
    (a resent batch changes nothing). A `verdict` sets the running stage_run's verdict.
    """
    if not events:
        return

    run_id = run["id"]
    stored: int = conn.execute(
        "SELECT COUNT(*) FROM run_events WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    dropped = 0
    started = False  # a new `stage_started` was stored
    stage_ids = set(json.loads(run["stage_ids"]))
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
        if cursor.rowcount == 0 or ev.stage_id is None or ev.stage_id not in stage_ids:
            continue  # a resent event, or none of the run's stages: stored only
        at = to_db(min(ev.ts, now))
        if ev.type == "stage_started":
            started = True
            _start_stage(conn, run_id, ev.stage_id, ev.data.round, at)
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
        elif ev.type == "verdict":
            conn.execute(
                "UPDATE stage_runs SET verdict = ? WHERE run_id = ? AND stage_id = ? "
                "AND status = 'running'",
                (ev.data.verdict, run_id, ev.stage_id),
            )
    if dropped:
        log.warning("run %s: over %s events, dropped %s log events", run_id, stored, dropped)
    if started:
        _lock_base_branch(conn, run)


def _start_stage(conn: Conn, run_id: int, stage_id: str, round_: int, at: str) -> None:
    """Mark the stage_run a `stage_started` begins: the stage's planned (pending) row after the
    last one that started. With none (a later round of a loop block, whose rows aren't planned at
    dispatch), insert one right after the last started row, moving the pending rows after it one
    place down, so `idx` stays the execution order."""
    last: int = conn.execute(
        "SELECT COALESCE(MAX(idx), -1) FROM stage_runs WHERE run_id = ? AND status != 'pending'",
        (run_id,),
    ).fetchone()[0]
    planned = conn.execute(
        "SELECT MIN(idx) FROM stage_runs "
        "WHERE run_id = ? AND stage_id = ? AND status = 'pending' AND idx > ?",
        (run_id, stage_id, last),
    ).fetchone()[0]
    if planned is not None:
        conn.execute(
            "UPDATE stage_runs SET status = 'running', started_at = ?, round = ? "
            "WHERE run_id = ? AND idx = ?",
            (at, round_, run_id, planned),
        )
        return
    # Two steps, as idx is part of the key: k → -k-1 → k+1.
    conn.execute(
        "UPDATE stage_runs SET idx = -idx - 1 WHERE run_id = ? AND idx > ?", (run_id, last)
    )
    conn.execute("UPDATE stage_runs SET idx = -idx WHERE run_id = ? AND idx < 0", (run_id,))
    conn.execute(
        "INSERT INTO stage_runs(run_id, idx, stage_id, round, status, started_at) "
        "VALUES (?, ?, ?, ?, 'running', ?)",
        (run_id, last + 1, stage_id, round_, at),
    )


def _lock_base_branch(conn: Conn, run: Row) -> None:
    """Lock the base branch of the run's ticket on the run's first stored `stage_started`, if no
    run locked it before (request 22A).

    Why then: the worker emits `stage_started` only after `_setup_workspace` succeeded, i.e. the
    ticket branch was checked out from `origin/{base_branch}` (or from an existing
    `origin/agent/{KEY}`). From then on work exists on top of that base, so changing it (or the
    repo) would strand it. A run that fails at checkout (a wrong base branch) never sends one, so
    the owner can still fix the base. Mailbox runs replay their events through `post_events`, so
    the same moment applies. A no-repo ticket has no base branch: nothing to lock. The ticket's
    `repo` can't change while the run is active (409 `run_active`), so it is the run's.

    A ticket that followed the repo default is pinned to the branch the run started from (its
    job's package), so a later change to the repo's `default_branch` doesn't move locked work.
    """
    conn.execute(
        "UPDATE tickets SET base_branch_locked_by_run = ?, base_branch = COALESCE(base_branch, "
        "(SELECT json_extract(package, '$.workspace.base_branch') FROM jobs WHERE run_id = ?)) "
        "WHERE id = ? AND repo IS NOT NULL AND base_branch_locked_by_run IS NULL",
        (run["id"], run["id"], run["ticket_id"]),
    )


def sync(db: Database, worker_token: str, stage_id: str, files: Sequence[SyncFile]) -> SyncResponse:
    """Write the worker's context files back (`_sync`); refused paths are logged."""
    with db.tx() as conn:
        run = _live_run(conn, worker_token)
        response = _sync(conn, run, stage_id, files)
    if response.refused:
        log.warning("run %s: sync refused %s", run["id"], response.refused)
    return response


def _sync(conn: Conn, run: Row, stage_id: str | None, files: Sequence[SyncFile]) -> SyncResponse:
    """Write the worker's context files back as new versions authored by the run (SPEC §9.4.1).

    Only the run package's write context entries and notes are written (matched by `path_key`, in
    the package's mode). Any other path, or a declared one sent in another mode, is refused:
    listed in `refused`, while the rest of the batch applies. `versions` holds each written
    path's latest version (0 = absent: deleting a missing file). A stage outside the run → 422.
    """
    if stage_id not in json.loads(run["stage_ids"]):
        raise ApiError(
            422, "validation_error", f"stage_id: {stage_id!r} is not a stage of this run"
        )
    package = JobPackage.model_validate_json(
        conn.execute("SELECT package FROM jobs WHERE run_id = ?", (run["id"],)).fetchone()[0]
    )
    declared: dict[str, tuple[str, Mode]] = {
        path_key(e.path): (e.path, e.mode) for e in package.context if e.access == "write"
    }
    declared |= {path_key(n.path): (n.path, "replace") for n in package.notes}

    versions: dict[str, int] = {}
    refused: list[str] = []
    for f in files:
        path, mode = declared.get(path_key(f.path), (None, f.mode))
        if path is None or (f.content is not None and f.mode != mode):
            refused.append(f.path)
            continue
        version = write_version(
            conn,
            run["ticket_id"],
            path,
            f.content,
            mode,
            author_run_id=run["id"],
            author_stage=stage_id,
        )
        versions[path] = version or 0
    return SyncResponse(versions=versions, refused=refused)


def check_live_run(db: Database, worker_token: str) -> int:
    """The run of a worker token: 401 for an unknown token, 410 when the run has ended. The
    upload route calls it before it reads a body."""
    with db.read() as conn:
        run_id: int = _live_run(conn, worker_token)["id"]
    return run_id


def check_upload(db: Database, job_token: str, instance: str, job_id: str) -> int:
    """The run a job's sync_back upload goes to: as `_job_run`, plus 404 for another job's id and
    410 unless the run is running. The upload route calls it before it reads a body."""
    with db.read() as conn:
        job, run = _job_run(conn, job_token, instance)
    if job["id"] != job_id:
        raise ApiError(404, "not_found", f"No job {job_id} for this token")
    if run["status"] != "running":
        raise _gone(run["id"], run["status"])
    run_id: int = run["id"]
    return run_id


def store_sync_back(db: Database, run_id: int, data: bytes, sha256: str, files: int) -> None:
    """Store the run's sync_back zip while the run is running. A repeat upload replaces it, so a
    retry is safe. 413 `too_large` over MAX_SYNC_BACK_BYTES; 422 when empty or the sha256 (hex)
    doesn't match the bytes. The caller has checked the credentials (`check_live_run`,
    `check_upload`) before reading the body."""
    if len(data) > MAX_SYNC_BACK_BYTES:
        raise ApiError(413, "too_large", f"sync_back: over {MAX_SYNC_BACK_BYTES} bytes")
    if not data:
        raise field_error([], "The zip is empty.")
    if hashlib.sha256(data).hexdigest() != sha256.lower():
        raise field_error(["sha256"], "Doesn't match the upload.")

    with db.tx() as conn:
        status = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()[0]
        if status != "running":
            raise _gone(run_id, status)
        conn.execute(
            "INSERT OR REPLACE INTO run_sync_back(run_id, sha256, bytes, files, data, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, sha256.lower(), len(data), files, data, to_db(utcnow())),
        )


def complete(db: Database, worker_token: str, body: ResultData) -> None:
    """End the run (`_finish`). Repeating the final status of a terminal run is a no-op; anything
    else on a terminal run → 410."""
    with db.tx() as conn:
        run = _worker_run(conn, worker_token)
        if run["status"] in TERMINAL:
            if run["status"] in _FINAL[body.status]:
                return
            raise _gone(run["id"], run["status"])
        _finish(conn, run, body, utcnow())


def _finish(conn: Conn, run: Row, body: ResultData, now: datetime) -> None:
    """running → gated|succeeded (completed, per `completion_status`), failed or cancelled, and
    the reported PR stored. A run that isn't running is left as it is."""
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
            error = f"stage {body.failed_stage} failed" if body.failed_stage else "the run failed"
    else:
        status = "cancelled"
    if finish(conn, run["id"], status, ("running",), now=now, error=error):
        _store_pr_url(conn, run, body.pr_url)


# ------------------------------------------------------------------ client poll


def poll_client(db: Database, client_token: str) -> Client:
    """Authenticate a polling client (401/403: `clients.service.authenticate`) and record that it
    was seen. Once per poll."""
    client = clients.authenticate(db, client_token)
    clients.touch(db, client.id, utcnow())
    return client


def take_client_run(db: Database, client_token: str, server_url: str) -> ClientRunAssignment | None:
    """One take of a poll: the oldest client run queued by the token's user, whose job is built
    now, with the job token for its worker (in plaintext only in the answer; never logged). The
    token is checked again on every take, so a revoke or a disabled user also stops a poll that
    is waiting. Only reads run until a run is queued; the write transaction opens only to take
    it. A run whose job can't be built is failed, and the poll goes on waiting."""
    client = clients.authenticate(db, client_token)
    if not runs.client_run_queued(db, client.user.id):
        return None

    taken = runs.take_client_run(db, client.user.id, client.name, server_url)
    if taken is None:
        return None
    run_id, job_token = taken
    if job_token is None:
        return None
    log.info("run %s: handed to client %s of user %s", run_id, client.id, client.user.id)
    return ClientRunAssignment(run_id=run_id, server_url=server_url, dispatch_token=job_token)


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
