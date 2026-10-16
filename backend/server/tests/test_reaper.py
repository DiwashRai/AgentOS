"""Reaper: every rule per route (push + live, pull + live), in order, with an injected clock."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentos.clock import from_db, to_db, utcnow
from agentos.context import AppContext
from agentos.launchers.base import JobStatus
from agentos.models import LeaseResponse, Me, TicketDetail
from agentos.runs.reaper import UNWATCHED, Reaper
from agentos.runs.service import NEVER_STARTED, OVER_BUDGET
from tests.mailbox_support import FakeMailbox
from tests.run_support import CSRF, FakeProvider, RunDriver, Worker, bearer
from tests.settings_support import set_space

CreateTicket = Callable[..., TicketDetail]
LoginFn = Callable[..., Me]
BUDGET = timedelta(seconds=3600, minutes=15)  # time_budget_s (plan: 3600) + 15 min


@dataclass
class Side:
    """User `me` running tickets on one route: push (the server's FakeProvider) or pull (their
    client, with `token`)."""

    client: TestClient
    create_ticket: CreateTicket
    runs: RunDriver
    me: Me
    token: str | None

    @property
    def push(self) -> bool:
        return self.token is None

    @property
    def start_timeout(self) -> timedelta:
        pull = timedelta(minutes=2)
        return timedelta(seconds=self.runs.ctx.space.claim_timeout_s) if self.push else pull

    def queue(self, title: str = "T") -> int:
        key = self.create_ticket(title, repo=None, owner_id=self.me.id).key
        return self.runs.queue(key, target=None if self.push else "client").id

    def dispatch(self, title: str = "T") -> tuple[int, str]:
        """Queue and start or lease: the run is `dispatched`. Returns it and its job token."""
        run_id = self.queue(title)
        if self.token is None:
            assert run_id in self.runs.tick()
            return run_id, self.runs.provider.token(run_id)
        r = self.client.post("/api/worker/lease", json={"wait_s": 0}, headers=bearer(self.token))
        job = LeaseResponse.model_validate(r.json()).job
        assert job is not None, r.text
        assert job.run_id == run_id
        return run_id, job.job_token

    def start(self, title: str = "T") -> Worker:
        """Dispatch and bind: the run is `running`."""
        return self.runs.bind(*self.dispatch(title))

    def cancelled(self) -> list[str]:
        """What the provider was asked to cancel."""
        return self.runs.provider.cancelled


@pytest.fixture(params=["push", "pull"])
def side(
    request: pytest.FixtureRequest,
    client: TestClient,
    login: LoginFn,
    create_ticket: CreateTicket,
    runs: RunDriver,
) -> Side:
    me = login("dev")
    token = None
    if request.param == "pull":
        r = client.post("/api/me/clients", json={"name": "laptop"}, headers=CSRF)
        assert r.status_code == 201, r.text
        token = r.json()["token"]
    return Side(client, create_ticket, runs, me, token)


@pytest.fixture
def reaper(ctx: AppContext) -> Reaper:
    return Reaper(ctx)


def armed(reaper: Reaper, now: datetime) -> Reaper:
    """A reaper whose previous tick was 15 s before `now` (so `now` is a normal tick)."""
    reaper.tick(now - timedelta(seconds=15))
    return reaper


def ref(run_id: int) -> str:
    return FakeProvider.ref(run_id)


def reaped(reaper: Reaper, now: datetime) -> list[tuple[int, str, str]]:
    return [(r.run_id, r.status, r.reason) for r in reaper.tick(now)]


# ------------------------------------------------------------------ rule 0: sleep grace


def test_first_tick_is_a_grace_tick(side: Side, runs: RunDriver, reaper: Reaper) -> None:
    worker = side.start()
    runs.set_run(worker.run_id, last_seen_at=to_db(utcnow() - timedelta(hours=1)))
    now = utcnow()
    assert reaper.tick(now) == []
    row = runs.db_run(worker.run_id)
    assert (row["status"], row["last_seen_at"]) == ("running", to_db(now))
    assert reaper.tick(now + timedelta(seconds=15)) == []  # fresh: nothing to reap


def test_a_long_gap_is_a_grace_tick(side: Side, runs: RunDriver, reaper: Reaper) -> None:
    worker = side.start()
    now = utcnow()
    reaper.tick(now)
    later = now + timedelta(minutes=30)  # the laptop slept
    assert reaper.tick(later) == []
    assert runs.db_run(worker.run_id)["last_seen_at"] == to_db(later)
    assert reaper.tick(later + timedelta(seconds=45)) == []
    lost = reaped(reaper, later + timedelta(seconds=91))
    assert lost == [(worker.run_id, "failed", "worker lost")]


# ------------------------------------------------------------------ rule 1: start interrupted


def test_a_start_without_a_ref_fails_after_15_minutes(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, reaper: Reaper
) -> None:
    """The server died between the dispatch and storing the provider's ref."""
    old = runs.queue(create_ticket("old", repo=None).key).id
    young = runs.queue(create_ticket("young", repo=None).key).id
    runs.tick()
    now = utcnow()
    runs.set_run(old, launch_ref=None, dispatched_at=to_db(now - timedelta(minutes=15, seconds=1)))
    runs.set_run(young, launch_ref=None, dispatched_at=to_db(now - timedelta(minutes=14)))
    assert reaped(armed(reaper, now), now) == [(old, "failed", "start interrupted")]
    assert runs.db_run(young)["status"] == "dispatched"


# ------------------------------------------------------------------ rule 2: never started


def test_never_started(side: Side, runs: RunDriver, reaper: Reaper) -> None:
    old, _ = side.dispatch("old")
    young, _ = side.dispatch("young")
    now = utcnow()
    runs.set_run(old, dispatched_at=to_db(now - side.start_timeout - timedelta(seconds=1)))
    runs.set_run(young, dispatched_at=to_db(now - side.start_timeout + timedelta(seconds=1)))
    assert reaped(armed(reaper, now), now) == [(old, "failed", NEVER_STARTED)]
    detail = runs.get(old)
    assert [s.status for s in detail.stage_runs] == ["cancelled"]
    assert detail.stop_reason == ("never_started" if side.push else None)
    assert side.cancelled() == ([ref(old)] if side.push else [])
    assert runs.db_run(young)["status"] == "dispatched"


def test_the_start_timeout_is_the_saved_space_setting(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, reaper: Reaper
) -> None:
    set_space(runs.ctx, claim_timeout_s=60)  # Settings > Space: the next tick uses it
    old = runs.queue(create_ticket("old", repo=None).key).id
    young = runs.queue(create_ticket("young", repo=None).key).id
    runs.tick()
    now = utcnow()
    runs.set_run(old, dispatched_at=to_db(now - timedelta(seconds=61)))
    runs.set_run(young, dispatched_at=to_db(now - timedelta(seconds=59)))
    assert [r.run_id for r in armed(reaper, now).tick(now)] == [old]


def test_a_push_job_the_provider_runs_is_not_failed_and_its_status_is_polled_every_minute(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, reaper: Reaper
) -> None:
    run_id = runs.queue(create_ticket(repo=None).key).id
    runs.tick()
    now = utcnow()
    runs.set_run(run_id, dispatched_at=to_db(now - timedelta(days=1)))
    runs.provider.statuses[ref(run_id)] = JobStatus("running")
    assert armed(reaper, now).tick(now) == []

    runs.provider.statuses[ref(run_id)] = JobStatus("pending")
    assert reaper.tick(now + timedelta(seconds=45)) == []  # the last answer is a minute fresh
    assert reaped(reaper, now + timedelta(seconds=60)) == [(run_id, "failed", NEVER_STARTED)]


def test_a_push_job_the_provider_runs_fails_past_its_not_after(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, reaper: Reaper
) -> None:
    """A workflow stuck before `started` would otherwise hold the run until GitHub's timeout."""
    run_id = runs.queue(create_ticket(repo=None).key).id
    runs.tick()
    runs.provider.statuses[ref(run_id)] = JobStatus("running")
    with runs.ctx.db.read() as conn:
        row = conn.execute("SELECT not_after FROM jobs WHERE run_id = ?", (run_id,)).fetchone()
    not_after = from_db(row["not_after"])

    assert armed(reaper, not_after).tick(not_after) == []
    later = not_after + timedelta(seconds=15)
    assert reaped(reaper, later) == [(run_id, "failed", NEVER_STARTED)]
    assert runs.provider.cancelled == [ref(run_id)]


def test_a_failing_check_does_not_stop_the_next_job(
    authed: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    reaper: Reaper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = runs.queue(create_ticket("broken", repo=None).key).id
    stale = runs.queue(create_ticket("stale", repo=None).key).id
    runs.tick()
    now = utcnow()
    for run_id in (broken, stale):
        runs.set_run(run_id, dispatched_at=to_db(now - timedelta(days=1)))

    def status(job_ref: str) -> JobStatus:
        if job_ref == ref(broken):
            raise RuntimeError("boom")
        return JobStatus("unknown")

    monkeypatch.setattr(runs.provider, "status", status)
    assert [r.run_id for r in armed(reaper, now).tick(now)] == [stale]
    assert runs.db_run(broken)["status"] == "dispatched"


@pytest.mark.parametrize("rule", ["never_started", "lost", "stop_grace"])
def test_a_run_that_moves_while_the_provider_is_asked_keeps_its_worker(
    rule: str,
    authed: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    reaper: Reaper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reaper reads, asks the provider, then writes: what the worker posts in between
    (`started`, a heartbeat) drops the verdict, and nothing is cancelled."""
    run_id = runs.queue(create_ticket(repo=None).key).id
    assert runs.tick() == [run_id]
    now = utcnow()
    armed(reaper, now)
    worker: list[Worker] = []
    if rule == "never_started":
        runs.set_run(run_id, dispatched_at=to_db(now - timedelta(days=1)))
    else:
        worker.append(runs.bind(run_id, runs.provider.token(run_id)))
        runs.set_run(run_id, last_seen_at=to_db(now - timedelta(minutes=5)))
        if rule == "stop_grace":
            stopped = to_db(now - timedelta(minutes=3))
            runs.set_run(run_id, last_seen_at=to_db(now), stop_requested_at=stopped)
            runs.set_run(run_id, stop_reason="cancel")

    def status(job_ref: str) -> JobStatus:
        if rule == "never_started":
            worker.append(runs.bind(run_id, runs.provider.token(run_id)))
        else:
            assert worker[0].events().status_code == 200  # a post: last_seen_at moves
        return JobStatus("unknown")

    monkeypatch.setattr(runs.provider, "status", status)
    assert reaper.tick(now) == []
    row = runs.db_run(run_id)
    assert (row["status"], row["error"]) == ("running", None)
    assert row["stop_reason"] == ("cancel" if rule == "stop_grace" else None)
    assert runs.provider.cancelled == []


# ------------------------------------------------------------------ rule 3: lost


def test_worker_lost(side: Side, runs: RunDriver, reaper: Reaper) -> None:
    lost = side.start("lost")
    alive = side.start("alive")
    now = utcnow()
    armed(reaper, now)  # the grace tick refreshed both; now backdate one heartbeat
    runs.set_run(lost.run_id, last_seen_at=to_db(now - timedelta(seconds=91)))
    runs.set_run(alive.run_id, last_seen_at=to_db(now - timedelta(seconds=89)))
    assert reaped(reaper, now) == [(lost.run_id, "failed", "worker lost")]
    assert runs.db_run(alive.run_id)["status"] == "running"
    assert side.cancelled() == ([ref(lost.run_id)] if side.push else [])
    assert lost.events().status_code == 410  # the worker learns on its next post


# ------------------------------------------------------------------ rule 4: over budget


def test_over_budget_stops_the_job(side: Side, runs: RunDriver, reaper: Reaper) -> None:
    over = side.start("over")
    within = side.start("within")
    now = utcnow()
    armed(reaper, now)
    runs.set_run(over.run_id, started_at=to_db(now - BUDGET - timedelta(seconds=1)))
    runs.set_run(within.run_id, started_at=to_db(now - BUDGET + timedelta(seconds=1)))
    assert reaper.tick(now) == []

    detail = runs.get(over.run_id)
    assert (detail.status, detail.stop_reason) == ("running", "budget")
    assert over.journal().json()["cancel"] is True  # it publishes, then reports cancelled
    assert runs.get(within.run_id).stop_reason is None
    assert side.cancelled() == []


# ------------------------------------------------------------------ rule 5: stop grace


@pytest.mark.parametrize(
    ("reason", "status", "error"),
    [
        ("cancel", "cancelled", None),
        ("budget", "failed", OVER_BUDGET),
        ("never_started", "failed", NEVER_STARTED),
    ],
)
def test_a_stop_without_a_result_ends_the_run_after_2_minutes(
    side: Side, runs: RunDriver, reaper: Reaper, reason: str, status: str, error: str | None
) -> None:
    stuck = side.start("stuck")
    fresh = side.start("fresh")
    now = utcnow()
    armed(reaper, now)
    runs.set_run(
        stuck.run_id,
        stop_requested_at=to_db(now - timedelta(minutes=2, seconds=1)),
        stop_reason=reason,
    )
    runs.set_run(fresh.run_id, stop_requested_at=to_db(now), stop_reason=reason)

    assert [(r.run_id, r.status) for r in reaper.tick(now)] == [(stuck.run_id, status)]
    detail = runs.get(stuck.run_id)
    assert (detail.status, detail.error) == (status, error)
    assert runs.db_run(fresh.run_id)["status"] == "running"
    assert (ref(stuck.run_id) in side.cancelled()) == side.push


def test_a_user_cancel_reaches_the_provider_only_after_the_stop_grace(
    side: Side, runs: RunDriver, reaper: Reaper
) -> None:
    """Inside the grace the worker sees `cancel: true` and still publishes: nothing kills it."""
    worker = side.start()
    assert side.client.post(f"/api/runs/{worker.run_id}/cancel", headers=CSRF).status_code == 200
    now = utcnow()
    armed(reaper, now)
    for seconds in range(0, 120, 15):
        tick = now + timedelta(seconds=seconds)
        runs.set_run(worker.run_id, last_seen_at=to_db(tick))  # its heartbeats
        assert reaper.tick(tick) == []
    assert side.cancelled() == []

    later = now + timedelta(seconds=135)
    runs.set_run(worker.run_id, last_seen_at=to_db(later))
    assert [(r.run_id, r.status) for r in reaper.tick(later)] == [(worker.run_id, "cancelled")]
    reaper.tick(later + timedelta(seconds=15))
    assert side.cancelled() == ([ref(worker.run_id)] if side.push else [])


def test_a_worker_that_reports_cancelled_is_never_cancelled_at_its_provider(
    side: Side, runs: RunDriver, reaper: Reaper
) -> None:
    worker = side.start()
    assert side.client.post(f"/api/runs/{worker.run_id}/cancel", headers=CSRF).status_code == 200
    now = utcnow()
    armed(reaper, now)
    assert worker.complete("cancelled").status_code == 200  # inside the grace
    for seconds in (15, 30, 45):
        reaper.tick(now + timedelta(seconds=seconds))
    assert runs.get(worker.run_id).status == "cancelled"
    assert side.cancelled() == []


def test_rules_apply_in_order(side: Side, runs: RunDriver, reaper: Reaper) -> None:
    """A run that is silent, over budget and past its stop grace is reaped once, as lost."""
    worker = side.start()
    side.client.post(f"/api/runs/{worker.run_id}/cancel", headers=CSRF)
    now = utcnow()
    armed(reaper, now)
    runs.set_run(
        worker.run_id,
        last_seen_at=to_db(now - timedelta(minutes=5)),
        started_at=to_db(now - timedelta(days=1)),
        stop_requested_at=to_db(now - timedelta(minutes=5)),
    )
    assert reaped(reaper, now) == [(worker.run_id, "failed", "worker lost")]
    assert reaper.tick(now + timedelta(seconds=15)) == []


# ------------------------------------------------------------------ what is never reaped


@pytest.mark.parametrize("configured", ["github", "mailbox", None])
def test_a_running_file_run_is_left_to_the_mailbox_whatever_the_settings(
    authed: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    reaper: Reaper,
    configured: str | None,
) -> None:
    """Switching the server away from the mailbox doesn't fail its running runs."""
    worker = runs.start(create_ticket(repo=None).key)
    runs.ctx.providers = {} if configured is None else {"github": runs.provider}
    runs.ctx.mailbox = FakeMailbox() if configured == "mailbox" else None
    now = utcnow()
    armed(reaper, now)
    old = to_db(now - timedelta(days=1))
    runs.set_run(
        worker.run_id, report="file", last_seen_at=old, started_at=old, stop_requested_at=old
    )
    assert reaper.tick(now) == []
    assert runs.db_run(worker.run_id)["status"] == "running"


@pytest.mark.parametrize("mailbox", [False, True])
def test_with_no_mailbox_a_running_file_run_fails_past_its_not_after(
    authed: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    reaper: Reaper,
    mailbox: bool,
) -> None:
    worker = runs.start(create_ticket(repo=None).key)
    runs.ctx.mailbox = FakeMailbox() if mailbox else None
    runs.set_run(worker.run_id, report="file")
    with runs.ctx.db.read() as conn:
        row = conn.execute("SELECT not_after FROM jobs WHERE run_id = ?", (worker.run_id,))
        not_after = from_db(row.fetchone()["not_after"])

    assert armed(reaper, not_after).tick(not_after) == []
    late = not_after + timedelta(seconds=15)
    assert reaped(reaper, late) == ([] if mailbox else [(worker.run_id, "failed", UNWATCHED)])


def test_terminal_runs_are_never_touched(side: Side, runs: RunDriver, reaper: Reaper) -> None:
    worker = side.start()
    worker.complete()
    now = utcnow()
    runs.set_run(worker.run_id, last_seen_at=to_db(now - timedelta(days=1)))
    assert armed(reaper, now).tick(now) == []
    assert runs.db_run(worker.run_id)["status"] == "gated"


def test_a_queued_run_waits_through_every_rule(side: Side, runs: RunDriver, reaper: Reaper) -> None:
    """A client run waits for its user's client however long it is offline."""
    run_id = side.queue()
    runs.ctx.providers = {}  # a push run held: its provider is not configured
    now = utcnow()
    runs.set_run(run_id, created_at=to_db(now - timedelta(days=3)))
    armed(reaper, now)
    for minutes in (0, 0.25, 0.5):
        assert reaper.tick(now + timedelta(minutes=minutes)) == []
    assert runs.db_run(run_id)["status"] == "queued"
