"""Mailbox runs in the db: the reaper's queries, `end()` and `replay()` (`design/mailbox.md` §3).

Replay feeds a run's recorded worker calls (the outbox) through the unchanged `worker_api.service`
functions, so validation, the run state machine, the board and the broker work as over HTTP.
It authenticates like a worker: a fresh worker token is minted and bound to the run (guarded on
`running` + `launcher = 'mailbox'`) and lives only in memory for that replay.

No launcher or store here (plugins stay out of services): the reaper fetches and deletes files.
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Literal

from pydantic import ValidationError

from agentos.broker import Broker
from agentos.clock import from_db_opt, utcnow
from agentos.db import Database
from agentos.errors import ApiError
from agentos.models import CompleteRequest, EventsRequest, SyncRequest
from agentos.runs.service import finish, run_ticket_key, token_hash
from agentos.worker_api import service as worker_api

log = logging.getLogger(__name__)

LAUNCHER = "mailbox"
ReplayResult = Literal["completed", "ended", "gone"]

# A line over this is skipped (logged): 500 events x 256 KiB lines fit well below it.
MAX_LINE_BYTES = 160 * 1024 * 1024
NO_COMPLETE = "the worker stopped without completing (job killed or crashed); see the GitHub run"
_EVENTS = "/api/worker/events"
_SYNC = "/api/worker/sync"
_COMPLETE = "/api/worker/complete"


@dataclass(frozen=True, slots=True)
class MailboxRun:
    """A running mailbox run, as the mailbox reaper sees it."""

    id: int
    dispatch_token_hash: str
    launch_ref: str | None
    started_at: datetime | None
    time_budget_s: int
    cancel_requested_at: datetime | None


# ------------------------------------------------------------------ queries


def running_runs(db: Database) -> list[MailboxRun]:
    """Every `running` mailbox run, oldest first."""
    with db.read() as conn:
        rows = conn.execute(
            "SELECT id, dispatch_token_hash, launch_ref, started_at, time_budget_s, "
            "cancel_requested_at FROM runs WHERE launcher = ? AND status = 'running' "
            "AND dispatch_token_hash IS NOT NULL ORDER BY id",
            (LAUNCHER,),
        ).fetchall()
    return [
        MailboxRun(
            id=r["id"],
            dispatch_token_hash=r["dispatch_token_hash"],
            launch_ref=r["launch_ref"],
            started_at=from_db_opt(r["started_at"]),
            time_budget_s=r["time_budget_s"],
            cancel_requested_at=from_db_opt(r["cancel_requested_at"]),
        )
        for r in rows
    ]


def active_runs(db: Database) -> dict[int, str | None]:
    """Every dispatched or running run (any launcher: run ids are per server) → its dispatch
    token hash. What the sweep must keep."""
    with db.read() as conn:
        rows = conn.execute(
            "SELECT id, dispatch_token_hash FROM runs WHERE status IN ('dispatched', 'running')"
        ).fetchall()
    return {int(r["id"]): r["dispatch_token_hash"] for r in rows}


def is_running(db: Database, run_id: int) -> bool:
    with db.read() as conn:
        row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
    return row is not None and row["status"] == "running"


# ------------------------------------------------------------------ end


def end(
    db: Database,
    broker: Broker,
    run_id: int,
    status: Literal["failed", "cancelled"],
    error: str | None,
) -> bool:
    """running → failed|cancelled through `finish()` (guarded). Returns whether it ended the run
    (False: it was terminal already). Publishes after the commit."""
    with db.tx() as conn:
        ended = finish(conn, run_id, status, ("running",), now=utcnow(), error=error)
        key = run_ticket_key(conn, run_id) if ended else None
    if key is not None:
        broker.run(key, run_id)
        log.warning("mailbox: run %s %s%s", run_id, status, f": {error}" if error else "")
    return ended


# ------------------------------------------------------------------ replay


def replay(db: Database, broker: Broker, run_id: int, outbox: Path) -> ReplayResult:
    """Replay the outbox at `outbox` into run `run_id` (design §3.4).

    - `gone`: the run was terminal already (before or during the replay); nothing more to do.
    - `completed`: the recorded `complete` was applied.
    - `ended`: the recording had no `complete` (or isn't this run's): the run was ended here,
      `cancelled` if a cancel was requested, else `failed`.

    Idempotent: events are stored on `(run_id, seq)`, stage updates only move pending/running
    rows, an unchanged sync appends nothing, and a repeated `complete` is a no-op.
    """
    token = _rebind(db, run_id)
    if token is None:
        return "gone"
    with outbox.open("rb") as f:
        lines = _lines(f, run_id)
        header = _json(next(lines, None))
        if not isinstance(header, dict) or header.get("outbox") != 1:
            end(db, broker, run_id, "failed", "the run's results are not an AgentOS outbox")
            return "ended"
        if header.get("run_id") != run_id:
            owner = header.get("run_id")
            end(db, broker, run_id, "failed", f"the results belong to run {owner!r}")
            return "ended"
        for number, line in enumerate(lines, start=2):
            outcome = _apply(db, broker, token, run_id, number, _json(line))
            if outcome is not None:
                return outcome
    if _cancel_requested(db, run_id):
        ended = end(db, broker, run_id, "cancelled", None)
    else:
        ended = end(db, broker, run_id, "failed", NO_COMPLETE)
    return "ended" if ended else "gone"


def _rebind(db: Database, run_id: int) -> str | None:
    """Bind a fresh worker token to the running mailbox run. None if it isn't one (any more)."""
    token = secrets.token_urlsafe(32)
    with db.tx() as conn:
        cursor = conn.execute(
            "UPDATE runs SET worker_token_hash = ? "
            "WHERE id = ? AND status = 'running' AND launcher = ?",
            (token_hash(token), run_id, LAUNCHER),
        )
    return token if cursor.rowcount else None


def _cancel_requested(db: Database, run_id: int) -> bool:
    with db.read() as conn:
        row = conn.execute(
            "SELECT cancel_requested_at FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    return row is not None and row["cancel_requested_at"] is not None


def _apply(
    db: Database, broker: Broker, token: str, run_id: int, number: int, entry: object
) -> ReplayResult | None:
    """One recorded call, as its route would make it. Mirrors the worker: a rejected events
    batch or sync is logged and skipped; 401/410 means the run went terminal (→ `gone`); a
    `complete` ends the replay (→ `completed`). None: carry on."""
    if not isinstance(entry, dict):
        log.warning("mailbox: run %s: outbox line %s is not a call; skipped", run_id, number)
        return None
    path, body = entry.get("path"), entry.get("body")
    try:
        if path == _EVENTS:
            events = EventsRequest.model_validate(body)
            worker_api.post_events(db, broker, token, events.events)
        elif path == _SYNC:
            req = SyncRequest.model_validate(body)
            worker_api.sync(db, broker, token, req.stage_id, req.files)
        elif path == _COMPLETE:
            worker_api.complete(db, broker, token, CompleteRequest.model_validate(body))
            return "completed"
        else:
            log.warning("mailbox: run %s: outbox line %s: unknown path; skipped", run_id, number)
    except ValidationError as exc:
        log.warning(
            "mailbox: run %s: outbox line %s (%s) is invalid; skipped: %s",
            run_id,
            number,
            path,
            exc.errors(include_url=False, include_input=False),
        )
    except ApiError as exc:
        if exc.status in (401, 410):
            return "gone"
        log.warning(
            "mailbox: run %s: outbox line %s (%s) rejected (%s); skipped: %s",
            run_id,
            number,
            path,
            exc.code,
            exc.message,
        )
    return None


def _lines(f: BinaryIO, run_id: int) -> Iterator[bytes | None]:
    """The file's lines, never more than MAX_LINE_BYTES in memory: a longer line is skipped
    (logged) and yields None."""
    while True:
        line = f.readline(MAX_LINE_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_LINE_BYTES:
            while line and not line.endswith(b"\n"):
                line = f.readline(1024 * 1024)
            log.warning(
                "mailbox: run %s: an outbox line over %s bytes; skipped", run_id, MAX_LINE_BYTES
            )
            yield None
            continue
        yield line


def _json(line: bytes | None) -> object:
    """A parsed line, or None. A truncated last line (the job was killed mid-write) never parses:
    a strict prefix of a JSON object isn't valid JSON."""
    if line is None or not line.strip():
        return None
    try:
        value: object = json.loads(line)
    except ValueError:
        return None
    return value
