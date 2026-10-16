"""Mailbox runs in the db: the reaper's queries, `end()` and `replay()` (`design/mailbox.md` §3).

Replay turns a run's recorded worker calls (the outbox) into journal records and applies them
through `worker_api.service.ingest`, so validation, the run state machine and the board work as
over HTTP. The outbox's seqs count events only, so replay numbers the records itself.

No launcher or store here (plugins stay out of services): the reaper fetches and deletes files.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Literal

from pydantic import TypeAdapter, ValidationError

from agentos.clock import from_db, from_db_opt, utcnow
from agentos.db import Database
from agentos.models import JournalRecord, RunStatus
from agentos.runs.service import STOPPED, finish
from agentos.worker_api import service as worker_api

log = logging.getLogger(__name__)

ReplayResult = Literal["completed", "ended", "gone"]

# A line over this is skipped (logged): 500 events x 256 KiB lines fit well below it.
MAX_LINE_BYTES = 160 * 1024 * 1024
NO_COMPLETE = "the worker stopped without completing (job killed or crashed); see the GitHub run"
_EVENTS = "/api/worker/events"
_SYNC = "/api/worker/sync"
_COMPLETE = "/api/worker/complete"
_DICTS: TypeAdapter[list[dict[str, Any]]] = TypeAdapter(list[dict[str, Any]])


@dataclass(frozen=True, slots=True)
class MailboxRun:
    """A running mailbox run, as the mailbox reaper sees it."""

    id: int
    job_hex: str  # the random part of its job id, which names its folder
    launch_ref: str | None
    started_at: datetime  # when the server started it
    not_after: datetime  # its job's: the worker publishes nothing after it
    stop_requested_at: datetime | None


# ------------------------------------------------------------------ queries


def running_runs(db: Database) -> list[MailboxRun]:
    """Every `running` mailbox (file-report) run, oldest first."""
    with db.read() as conn:
        rows = conn.execute(
            "SELECT r.id, substr(j.id, -16) AS job_hex, r.launch_ref, r.started_at, "
            "j.not_after, r.stop_requested_at FROM runs r JOIN jobs j ON j.run_id = r.id "
            "WHERE r.report = 'file' AND r.status = 'running' ORDER BY r.id"
        ).fetchall()
    return [
        MailboxRun(
            id=r["id"],
            job_hex=r["job_hex"],
            launch_ref=r["launch_ref"],
            started_at=from_db(r["started_at"]),
            not_after=from_db(r["not_after"]),
            stop_requested_at=from_db_opt(r["stop_requested_at"]),
        )
        for r in rows
    ]


def active_runs(db: Database) -> set[int]:
    """The ids of every dispatched or running run (any launcher: run ids are per server): the
    sweep keeps their folders."""
    with db.read() as conn:
        rows = conn.execute("SELECT id FROM runs WHERE status IN ('dispatched', 'running')")
        return {int(r["id"]) for r in rows.fetchall()}


def is_running(db: Database, run_id: int) -> bool:
    with db.read() as conn:
        row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
    return row is not None and row["status"] == "running"


# ------------------------------------------------------------------ end


def end(db: Database, run_id: int, status: RunStatus, error: str | None) -> bool:
    """running → `status` (failed|cancelled) through `finish()` (guarded). Returns whether it
    ended the run (False: it was terminal already)."""
    with db.tx() as conn:
        ended = finish(conn, run_id, status, ("running",), now=utcnow(), error=error)
    if ended:
        log.warning("mailbox: run %s %s%s", run_id, status, f": {error}" if error else "")
    return ended


# ------------------------------------------------------------------ replay


def replay(db: Database, run_id: int, outbox: Path) -> ReplayResult:
    """Replay the outbox at `outbox` into run `run_id` (design §3.4).

    - `gone`: the run isn't a running mailbox run (before or during the replay); nothing more
      to do.
    - `completed`: the recorded `complete` was applied.
    - `ended`: the recording had no `complete` (or isn't this run's): the run was ended here,
      by the stop table if it was asked to stop, else `failed`.

    Each line is one ingest transaction, its records numbered in line order from the job's
    `first_seq`, so a replay after a crash skips what was applied. Rejected records and refused
    sync paths are logged by ingest.
    """
    with db.read() as conn:
        job = conn.execute(
            "SELECT j.* FROM jobs j JOIN runs r ON r.id = j.run_id "
            "WHERE r.id = ? AND r.status = 'running' AND r.report = 'file'",
            (run_id,),
        ).fetchone()
    if job is None:
        return "gone"
    seq: int = job["first_seq"]

    with outbox.open("rb") as f:
        lines = _lines(f, run_id)
        header = _json(next(lines, None))
        if not isinstance(header, dict) or header.get("outbox") != 1:
            end(db, run_id, "failed", "the run's results are not an AgentOS outbox")
            return "ended"
        if header.get("run_id") != run_id:
            owner = header.get("run_id")
            end(db, run_id, "failed", f"the results belong to run {owner!r}")
            return "ended"
        for number, line in enumerate(lines, start=2):
            records = _records(run_id, number, job["id"], seq, _json(line))
            if not records:
                continue
            seq += len(records)

            with db.tx() as conn:
                run = conn.execute(
                    "SELECT * FROM runs WHERE id = ? AND status = 'running' AND report = 'file'",
                    (run_id,),
                ).fetchone()
                if run is None:
                    return "gone"
                rejected = worker_api.ingest(conn, run, job, records, utcnow()).rejected

            last = records[-1]
            if last.type == "result" and last.seq > run["journal_seq"] and last.seq not in rejected:
                return "completed"

    with db.read() as conn:  # a stop sets its reason with it
        stop = conn.execute("SELECT stop_reason FROM runs WHERE id = ?", (run_id,)).fetchone()
    reason = stop["stop_reason"] if stop is not None else None
    status, error = STOPPED[reason] if reason else ("failed", NO_COMPLETE)
    return "ended" if end(db, run_id, status, error) else "gone"


def _records(run_id: int, number: int, job_id: str, seq: int, entry: object) -> list[JournalRecord]:
    """One recorded call as the job's journal records, numbered from `seq`: an events batch → its
    display records, a sync → `sync`, a complete → `result`. A line that isn't a known, well-formed
    call is logged and gives none (ingest checks each record's data)."""
    if not isinstance(entry, dict):
        log.warning("mailbox: run %s: outbox line %s is not a call; skipped", run_id, number)
        return []

    path = entry.get("path")
    head: dict[str, Any] = {"seq": seq, "ts": utcnow(), "job": job_id}
    try:
        [body] = _DICTS.validate_python([entry.get("body")])
        if path == _EVENTS:
            events = _DICTS.validate_python(body.get("events"))
            raw = [{**e, "seq": seq + i, "job": job_id} for i, e in enumerate(events)]
        elif path == _SYNC:
            data = {"files": body.get("files")}
            raw = [head | {"type": "sync", "stage_id": body.get("stage_id"), "data": data}]
        elif path == _COMPLETE:
            raw = [head | {"type": "result", "data": body}]
        else:
            log.warning("mailbox: run %s: outbox line %s: unknown path; skipped", run_id, number)
            return []
        return [JournalRecord.model_validate(r) for r in raw]
    except ValidationError as exc:
        log.warning(
            "mailbox: run %s: outbox line %s (%s) is invalid; skipped: %s",
            run_id,
            number,
            path,
            exc.errors(include_url=False, include_input=False),
        )
        return []


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
