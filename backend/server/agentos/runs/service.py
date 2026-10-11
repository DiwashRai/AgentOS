"""Runs: dispatch, reads, cancel and the run state machine (SPEC §8).

Every transition is a guarded `UPDATE … WHERE id = ? AND status IN (…)`; zero rows means the run
was elsewhere already (a no-op or a conflict, the caller decides). Terminal side effects
(`finish`): unfinished stage_runs → `cancelled`, `finished_at` set, the ticket's `updated_at`
bumped. The dispatcher and the reaper (loops) and the worker API call the primitives here.

Tokens are never stored in plaintext: `dispatch_token_hash` / `worker_token_hash` are SHA-256
hex digests, and they are never cleared (a token's validity follows the run's status).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import TypeAdapter

from agentos.clock import from_db, from_db_opt, to_db, utcnow
from agentos.db import Conn, Database, IntegrityError, Row
from agentos.errors import ApiError
from agentos.models import (
    Manifest,
    Me,
    Person,
    Pipeline,
    RunDetail,
    RunEvent,
    RunStatus,
    RunSummary,
    StageRunInfo,
)
from agentos.pipeline.manifest import render_stages
from agentos.pipeline.runtime import RuntimesConfig
from agentos.pipeline.segment import check_segment, segment
from agentos.pipeline.space import SpaceConfig
from agentos.tickets.board import RunFact, StageRunFact, next_stage
from agentos.tickets.service import (
    active_run_id,
    get_ticket_row,
    run_ticket_ref,
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

EVENT_ADAPTER: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)

_SELECT_RUN = (
    "SELECT r.*, t.key AS ticket_key, t.pipeline AS ticket_pipeline, "
    "u.display_name AS creator_name, u.avatar_url AS creator_avatar "
    "FROM runs r JOIN tickets t ON t.id = r.ticket_id LEFT JOIN users u ON u.id = r.created_by "
)
_SQL_RUN = _SELECT_RUN + "WHERE r.id = ?"
_SQL_TICKET_RUNS = _SELECT_RUN + "WHERE r.ticket_id = ? ORDER BY r.id DESC"
_SQL_STAGE_RUNS = (
    "SELECT idx, stage_id, status, exit_code, reason, started_at, finished_at "
    "FROM stage_runs WHERE run_id = ? ORDER BY idx"
)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ row → model helpers


def _person(id_: int | None, name: str | None, avatar: str | None) -> Person | None:
    if id_ is None or name is None:
        return None
    return Person(id=id_, display_name=name, avatar_url=avatar)


def run_row(conn: Conn, run_id: int) -> Row:
    """The `runs` row plus `ticket_key`, `ticket_pipeline` and the creator. 404 `run_not_found`."""
    row: Row | None = conn.execute(_SQL_RUN, (run_id,)).fetchone()
    if row is None:
        raise ApiError(404, "run_not_found", f"No run {run_id}")
    return row


def _summary(row: Row) -> RunSummary:
    return RunSummary(
        id=row["id"],
        ticket_key=row["ticket_key"],
        status=row["status"],
        from_stage=row["from_stage"],
        stage_ids=json.loads(row["stage_ids"]),
        created_by=_person(row["created_by"], row["creator_name"], row["creator_avatar"]),
        created_at=from_db(row["created_at"]),
        started_at=from_db_opt(row["started_at"]),
        finished_at=from_db_opt(row["finished_at"]),
        cancel_requested_at=from_db_opt(row["cancel_requested_at"]),
        error=row["error"],
        launch_ref=row["launch_ref"],
    )


def _detail(conn: Conn, row: Row) -> RunDetail:
    """Stage names come from the ticket's current pipeline, falling back to the stage id."""
    pipeline = Pipeline.model_validate_json(row["ticket_pipeline"])
    names = {s.id: s.name for s in pipeline.stages}
    stage_runs = [
        StageRunInfo(
            stage_id=sr["stage_id"],
            name=names.get(sr["stage_id"], sr["stage_id"]),
            idx=sr["idx"],
            status=sr["status"],
            exit_code=sr["exit_code"],
            reason=sr["reason"],
            started_at=from_db_opt(sr["started_at"]),
            finished_at=from_db_opt(sr["finished_at"]),
        )
        for sr in conn.execute(_SQL_STAGE_RUNS, (row["id"],)).fetchall()
    ]
    return RunDetail(**_summary(row).model_dump(), stage_runs=stage_runs)


def _latest_run_fact(conn: Conn, ticket_id: int) -> RunFact | None:
    run = conn.execute(
        "SELECT id, status, from_stage, stage_ids, created_at, started_at, finished_at "
        "FROM runs WHERE ticket_id = ? ORDER BY id DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    if run is None:
        return None
    stage_runs = tuple(
        StageRunFact(
            idx=sr["idx"],
            stage_id=sr["stage_id"],
            status=sr["status"],
            started_at=from_db_opt(sr["started_at"]),
            finished_at=from_db_opt(sr["finished_at"]),
        )
        for sr in conn.execute(_SQL_STAGE_RUNS, (run["id"],)).fetchall()
    )
    return RunFact(
        id=run["id"],
        status=run["status"],
        from_stage=run["from_stage"],
        stage_ids=tuple(json.loads(run["stage_ids"])),
        stage_runs=stage_runs,
        created_at=from_db(run["created_at"]),
        started_at=from_db_opt(run["started_at"]),
        finished_at=from_db_opt(run["finished_at"]),
    )


# ------------------------------------------------------------------ dispatch


def dispatch(
    db: Database,
    runtimes: RuntimesConfig,
    space: SpaceConfig,
    launcher_name: str,
    key: str,
    user: Me,
    from_stage: str | None,
) -> RunSummary:
    """Queue a run (SPEC §8 "Dispatch"), all in one transaction: a 422 leaves no run row.

    "Continue" and "Retry" are dispatches with the default `from_stage` (`board.next_stage`);
    "Run from…" passes one. The manifest is built later, at claim. A ticket whose repo is no
    longer in the Space's catalog → 422 `validation_error`.
    """
    now = to_db(utcnow())
    with db.tx() as conn:
        ticket = get_ticket_row(conn, key)
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
        stages = segment(pipeline, from_stage)
        check_segment(stages, ref.workspace_mode)
        try:
            cursor = conn.execute(
                "INSERT INTO runs(ticket_id, status, from_stage, stage_ids, time_budget_s, "
                "launcher, created_by, created_at) VALUES (?, 'queued', ?, '[]', 0, ?, ?, ?)",
                (ticket["id"], from_stage, launcher_name, user.id, now),
            )
        except IntegrityError as exc:  # runs_one_active
            raise ApiError(409, "run_active", f"{key} already has an active run") from exc
        run_id = cursor.lastrowid
        if run_id is None:  # pragma: no cover - sqlite always sets it after an INSERT
            raise RuntimeError("INSERT INTO runs returned no rowid")
        # Validate now (render errors → 422, rolling the run back); the manifest is built at claim.
        render_stages(run_id, ref, stages, runtimes)
        conn.execute(
            "UPDATE runs SET stage_ids = ?, time_budget_s = ? WHERE id = ?",
            (json.dumps([s.id for s in stages]), sum(s.timeout_s for s in stages), run_id),
        )
        conn.executemany(
            "INSERT INTO stage_runs(run_id, idx, stage_id, status) VALUES (?, ?, ?, 'pending')",
            [(run_id, i, s.id) for i, s in enumerate(stages)],
        )
        conn.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (now, ticket["id"]))
        # publish: run {key, id} (broker, Sunday)
        return _summary(run_row(conn, run_id))


# ------------------------------------------------------------------ reads


def list_runs(db: Database, key: str) -> list[RunSummary]:
    """The ticket's runs, newest first."""
    with db.read() as conn:
        ticket = get_ticket_row(conn, key)
        rows = conn.execute(_SQL_TICKET_RUNS, (ticket["id"],)).fetchall()
    return [_summary(r) for r in rows]


def get_run(db: Database, run_id: int) -> RunDetail:
    with db.read() as conn:
        return _detail(conn, run_row(conn, run_id))


def get_manifest(db: Database, run_id: int) -> Manifest:
    """The manifest exactly as sent to the worker. 404 `not_found` until the run is claimed."""
    with db.read() as conn:
        row = run_row(conn, run_id)
    if row["manifest"] is None:
        raise ApiError(
            404,
            "not_found",
            f"Run {run_id} has no manifest: it is built when a worker claims the run",
        )
    return Manifest.model_validate_json(row["manifest"])


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
    """queued/dispatched → `cancelled` at once (a late claim gets 410). running → set
    `cancel_requested_at` once; the worker sees `cancel: true` on its next event post and
    completes as cancelled (the reaper cancels it after CANCEL_GRACE otherwise). A terminal run
    is returned unchanged."""
    now = utcnow()
    with db.tx() as conn:
        row = run_row(conn, run_id)
        if row["status"] in ("queued", "dispatched"):
            finish(conn, run_id, "cancelled", ("queued", "dispatched"), now=now)
        elif row["status"] == "running":
            conn.execute(
                "UPDATE runs SET cancel_requested_at = ? "
                "WHERE id = ? AND status = 'running' AND cancel_requested_at IS NULL",
                (to_db(now), run_id),
            )
            # publish: run {key, id}
        return _summary(run_row(conn, run_id))


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
    # publish: run {key, id} + ticket {key}
    return True


# ------------------------------------------------------------------ dispatcher primitives


def mark_next_dispatched(
    db: Database, launcher_name: str, max_concurrency: int | None, dispatch_token_hash: str
) -> int | None:
    """Move the launcher's oldest `queued` run to `dispatched` (storing the token hash), unless
    its active (dispatched|running) runs are already at `max_concurrency`. Returns the run id."""
    with db.tx() as conn:
        if max_concurrency is not None:
            active = conn.execute(
                "SELECT COUNT(*) FROM runs "
                "WHERE launcher = ? AND status IN ('dispatched', 'running')",
                (launcher_name,),
            ).fetchone()[0]
            if active >= max_concurrency:
                return None
        row = conn.execute(
            "SELECT id FROM runs WHERE launcher = ? AND status = 'queued' ORDER BY id LIMIT 1",
            (launcher_name,),
        ).fetchone()
        if row is None:
            return None
        run_id: int = row["id"]
        cursor = conn.execute(
            "UPDATE runs SET status = 'dispatched', dispatch_token_hash = ?, dispatched_at = ? "
            "WHERE id = ? AND status = 'queued'",
            (dispatch_token_hash, to_db(utcnow()), run_id),
        )
        if cursor.rowcount == 0:  # pragma: no cover - BEGIN IMMEDIATE serialises writers
            return None
        # publish: run {key, id}
        return run_id


def set_launch_ref(db: Database, run_id: int, launch_ref: str) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE runs SET launch_ref = ? WHERE id = ?", (launch_ref, run_id))


def fail_launch(db: Database, run_id: int, error: str) -> None:
    with db.tx() as conn:
        finish(conn, run_id, "failed", ("dispatched",), now=utcnow(), error=error)


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


def reap(db: Database, now: datetime, claim_timeout_s: int) -> list[Reaped]:
    """Reaper rules 2-5 (SPEC §8), in order, in one transaction."""
    out: list[Reaped] = []
    with db.tx() as conn:

        def end(
            ids: Sequence[int], was: RunStatus, status: RunStatus, error: str | None, why: str
        ) -> None:
            for run_id in ids:
                if finish(conn, run_id, status, (was,), now=now, error=error):
                    out.append(Reaped(run_id, status, why))

        # 2. dispatched, never claimed
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
            "AND COALESCE(last_seen_at, started_at) < ?",
            (to_db(now - HEARTBEAT_TIMEOUT),),
        )
        end(lost, "running", "failed", WORKER_LOST, WORKER_LOST)
        # 4. over the time budget
        running = conn.execute(
            "SELECT id, started_at, time_budget_s FROM runs "
            "WHERE status = 'running' AND started_at IS NOT NULL"
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
            "SELECT id FROM runs WHERE status = 'running' AND cancel_requested_at < ?",
            (to_db(now - CANCEL_GRACE),),
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
