"""Reaper (SPEC §8 "Reaper loop"): every rule, in order, with an injected clock."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentos.clock import to_db, utcnow
from agentos.context import AppContext
from agentos.models import TicketDetail
from agentos.runs.reaper import Reaper
from tests.run_support import CSRF, RunDriver, Worker

CreateTicket = Callable[..., TicketDetail]


@pytest.fixture
def reaper(ctx: AppContext) -> Reaper:
    return Reaper(ctx)


def armed(reaper: Reaper, now: datetime) -> Reaper:
    """A reaper whose previous tick was 15 s before `now` (so `now` is a normal tick)."""
    reaper.tick(now - timedelta(seconds=15))
    return reaper


def started(create_ticket: CreateTicket, runs: RunDriver, title: str = "T") -> Worker:
    return runs.start(create_ticket(title, repo=None).key)


# ------------------------------------------------------------------ rule 1: grace


def test_first_tick_is_a_grace_tick(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, reaper: Reaper
) -> None:
    worker = started(create_ticket, runs)
    runs.set_run(worker.run_id, last_seen_at=to_db(utcnow() - timedelta(hours=1)))
    now = utcnow()
    assert reaper.tick(now) == []
    row = runs.db_run(worker.run_id)
    assert (row["status"], row["last_seen_at"]) == ("running", to_db(now))
    assert reaper.tick(now + timedelta(seconds=15)) == []  # fresh: nothing to reap


def test_a_long_gap_is_a_grace_tick(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, reaper: Reaper
) -> None:
    worker = started(create_ticket, runs)
    now = utcnow()
    reaper.tick(now)
    later = now + timedelta(minutes=30)  # the laptop slept
    assert reaper.tick(later) == []
    assert runs.db_run(worker.run_id)["last_seen_at"] == to_db(later)
    assert reaper.tick(later + timedelta(seconds=45)) == []
    assert [r.reason for r in reaper.tick(later + timedelta(seconds=91))] == ["worker lost"]


# ------------------------------------------------------------------ rules 2-5


def test_never_connected(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, reaper: Reaper
) -> None:
    timeout = runs.ctx.settings.claim_timeout_s
    old = runs.queue(create_ticket("old", repo=None).key).id
    young = runs.queue(create_ticket("young", repo=None).key).id
    runs.tick()
    now = utcnow()
    runs.set_run(old, dispatched_at=to_db(now - timedelta(seconds=timeout + 1)))
    reaped = armed(reaper, now).tick(now)
    assert [(r.run_id, r.status, r.reason) for r in reaped] == [
        (old, "failed", "worker never connected")
    ]
    detail = runs.get(old)
    assert (detail.status, detail.error) == ("failed", "worker never connected")
    assert [s.status for s in detail.stage_runs] == ["cancelled"]
    assert runs.db_run(young)["status"] == "dispatched"


def test_worker_lost(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, reaper: Reaper
) -> None:
    lost = started(create_ticket, runs, "lost")
    alive = started(create_ticket, runs, "alive")
    now = utcnow()
    armed(reaper, now)  # the grace tick refreshed both; now backdate one heartbeat
    runs.set_run(lost.run_id, last_seen_at=to_db(now - timedelta(seconds=91)))
    runs.set_run(alive.run_id, last_seen_at=to_db(now - timedelta(seconds=89)))
    reaped = reaper.tick(now)
    assert [(r.run_id, r.reason) for r in reaped] == [(lost.run_id, "worker lost")]
    assert runs.get(lost.run_id).error == "worker lost"
    assert runs.db_run(alive.run_id)["status"] == "running"
    assert lost.events().status_code == 410  # the worker learns on its next post


def test_time_budget_exceeded(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, reaper: Reaper
) -> None:
    over = started(create_ticket, runs, "over")
    within = started(create_ticket, runs, "within")
    now = utcnow()
    armed(reaper, now)
    budget = timedelta(seconds=3600, minutes=15)  # time_budget_s (plan: 3600) + 15 min
    runs.set_run(over.run_id, started_at=to_db(now - budget - timedelta(seconds=1)))
    runs.set_run(within.run_id, started_at=to_db(now - budget + timedelta(seconds=1)))
    reaped = reaper.tick(now)
    assert [(r.run_id, r.reason) for r in reaped] == [(over.run_id, "time budget exceeded")]
    assert runs.db_run(within.run_id)["status"] == "running"


def test_unacknowledged_cancel(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, reaper: Reaper
) -> None:
    stuck = started(create_ticket, runs, "stuck")
    fresh = started(create_ticket, runs, "fresh")
    for w in (stuck, fresh):
        authed.post(f"/api/runs/{w.run_id}/cancel", headers=CSRF)
    now = utcnow()
    armed(reaper, now)
    runs.set_run(stuck.run_id, cancel_requested_at=to_db(now - timedelta(minutes=2, seconds=1)))
    reaped = reaper.tick(now)
    assert [(r.run_id, r.status) for r in reaped] == [(stuck.run_id, "cancelled")]
    detail = runs.get(stuck.run_id)
    assert (detail.status, detail.error) == ("cancelled", None)
    assert runs.db_run(fresh.run_id)["status"] == "running"


def test_rules_apply_in_order(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, reaper: Reaper
) -> None:
    """A run that is both silent and over budget is reaped once, as "worker lost"."""
    worker = started(create_ticket, runs)
    authed.post(f"/api/runs/{worker.run_id}/cancel", headers=CSRF)
    now = utcnow()
    armed(reaper, now)
    runs.set_run(
        worker.run_id,
        last_seen_at=to_db(now - timedelta(minutes=5)),
        started_at=to_db(now - timedelta(days=1)),
        cancel_requested_at=to_db(now - timedelta(minutes=5)),
    )
    reaped = reaper.tick(now)
    assert [(r.run_id, r.reason) for r in reaped] == [(worker.run_id, "worker lost")]
    assert reaper.tick(now + timedelta(seconds=15)) == []


def test_terminal_runs_are_never_touched(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, reaper: Reaper
) -> None:
    worker = started(create_ticket, runs)
    worker.complete()
    now = utcnow()
    runs.set_run(worker.run_id, last_seen_at=to_db(now - timedelta(days=1)))
    assert armed(reaper, now).tick(now) == []
    assert runs.db_run(worker.run_id)["status"] == "gated"


# ------------------------------------------------------------------ rule 6: workspaces


def _age(path: os.PathLike[str], when: datetime) -> None:
    ts = when.timestamp()
    os.utime(path, (ts, ts))


def test_old_workspaces_are_deleted(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, reaper: Reaper
) -> None:
    root = runs.ctx.settings.workspaces_dir
    worker = started(create_ticket, runs)
    now = utcnow()
    old = root / "9001"
    (old / ".git" / "objects").mkdir(parents=True)
    pack = old / ".git" / "objects" / "pack.idx"
    pack.write_text("x", encoding="utf-8")
    pack.chmod(stat.S_IREAD)  # read-only, like git packs on Windows
    active = root / str(worker.run_id)  # an active run's workspace is never deleted
    active.mkdir()
    young = root / "9002"
    young.mkdir()
    stray = root / "stray.txt"
    stray.write_text("x", encoding="utf-8")
    eight_days = now - timedelta(days=8)
    for p in (old, active, stray):
        _age(p, eight_days)
    _age(young, now - timedelta(days=6))
    armed(reaper, now).tick(now)
    assert sorted(p.name for p in root.iterdir()) == sorted([active.name, young.name])


def test_workspace_cleanup_is_local_only(
    authed: TestClient, runs: RunDriver, reaper: Reaper
) -> None:
    root = runs.ctx.settings.workspaces_dir
    old = root / "9001"
    old.mkdir(parents=True)
    now = utcnow()
    _age(old, now - timedelta(days=8))
    runs.launcher.name = "github"
    armed(reaper, now).tick(now)
    assert old.exists()
