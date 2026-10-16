"""The fence: a ticket's next run stays queued, with a reason, until the previous job can no longer
publish: its accepted result says so; for a worker that sent none, push: its provider says ended;
pull: its client stops listing it or goes offline after the run ended; any route: past
`not_after`. Plus tables for `job_ended` and `not_after`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentos.clients import service as clients
from agentos.clock import to_db, utcnow
from agentos.context import AppContext
from agentos.db import Conn
from agentos.launchers.base import JobStatus
from agentos.models import LeasedJob, LeaseResponse, Me, Route, TicketDetail
from agentos.runs.reaper import WORKER_LOST, JobFacts, Reaper, job_ended
from agentos.runs.service import finish, not_after
from tests.run_support import CSRF, FakeProvider, RunDriver, bearer

CreateTicket = Callable[..., TicketDetail]
LoginFn = Callable[..., Me]
T0 = datetime(2026, 10, 16, 12, tzinfo=UTC)
S = timedelta(seconds=1)


# ------------------------------------------------------------------ pure tables


@pytest.mark.parametrize(
    ("route", "allowance"),
    [
        pytest.param(Route("push", "github", "live"), timedelta(minutes=10 + 15), id="push-live"),
        pytest.param(Route("push", "github", "file"), timedelta(minutes=10 + 45), id="push-file"),
        pytest.param(Route("pull", None, "live"), timedelta(minutes=2 + 15), id="pull-live"),
    ],
)
def test_not_after_is_dispatch_plus_start_budget_and_overhead(
    route: Route, allowance: timedelta
) -> None:
    assert not_after(route, T0, 3600, 600) == T0 + timedelta(hours=1) + allowance


# A pull job: dispatched at T0, its result at +10 s, last listed by its client's lease at +20 s.
PULL = JobFacts(
    run_id=1,
    job_id="1-1-0123456789abcdef",
    status="succeeded",
    start="pull",
    provider=None,
    report="live",
    ref="laptop",
    dispatched_at=T0,
    started_at=T0,
    bound=True,
    seen_at=T0,
    budget_s=3600,
    stop_requested_at=None,
    stop_reason=None,
    start_timeout=timedelta(minutes=2),
    not_after=T0 + timedelta(hours=2),
    finished_at=T0 + 10 * S,
    alive_at=T0 + 20 * S,
    client_seen_at=T0 + 20 * S,
)
PUSH = replace(PULL, start="push", provider="github", ref="r", alive_at=None, client_seen_at=None)
LATE = T0 + timedelta(hours=2) + S


@pytest.mark.parametrize(
    ("job", "now", "status", "ended"),
    [
        pytest.param(PUSH, T0 + 60 * S, None, False, id="push-no-status"),
        pytest.param(PUSH, T0 + 60 * S, JobStatus("running"), False, id="push-running"),
        pytest.param(PUSH, T0 + 60 * S, JobStatus("ended"), True, id="push-ended"),
        pytest.param(PUSH, LATE, None, True, id="push-past-not-after"),
        pytest.param(PULL, T0 + 60 * S, None, False, id="pull-listed-by-the-last-lease"),
        pytest.param(
            replace(PULL, client_seen_at=T0 + 21 * S), T0 + 30 * S, None, True, id="pull-unlisted"
        ),
        pytest.param(
            replace(PULL, alive_at=None, client_seen_at=T0 - S),
            T0 + 30 * S,
            None,
            False,
            id="pull-no-lease-since-the-take",
        ),
        pytest.param(PULL, T0 + 110 * S, None, False, id="pull-offline-90-s"),
        pytest.param(PULL, T0 + 111 * S, None, True, id="pull-offline-over-90-s"),
        pytest.param(
            replace(PULL, alive_at=None, client_seen_at=None),
            T0 + 101 * S,
            None,
            True,
            id="pull-client-revoked-90-s-after-the-result",
        ),
        pytest.param(
            replace(PULL, client_seen_at=LATE), LATE, None, True, id="pull-past-not-after"
        ),
    ],
)
def test_job_ended(job: JobFacts, now: datetime, status: JobStatus | None, ended: bool) -> None:
    assert job_ended(job, now, status) is ended


# ------------------------------------------------------------------ through the API


@pytest.fixture
def me(login: LoginFn) -> Me:
    return login("dev")


@pytest.fixture
def key(create_ticket: CreateTicket, me: Me) -> str:
    return create_ticket("T", repo=None, owner_id=me.id).key


@pytest.fixture
def token(client: TestClient, me: Me) -> str:
    r = client.post("/api/me/clients", json={"name": "laptop"}, headers=CSRF)
    assert r.status_code == 201, r.text
    return str(r.json()["token"])


def lease(
    client: TestClient, token: str, alive: tuple[str, ...] = (), take: bool = True
) -> LeasedJob | None:
    body = {"wait_s": 0, "take": take, "alive": list(alive)}
    r = client.post("/api/worker/lease", json=body, headers=bearer(token))
    assert r.status_code == 200, r.text
    return LeaseResponse.model_validate(r.json()).job


def reap(ctx: AppContext, now: datetime) -> None:
    """One normal reaper tick at `now` (after its grace tick)."""
    reaper = Reaper(ctx)
    reaper.tick(now - 15 * S)
    reaper.tick(now)


def lose(ctx: AppContext, run_id: int) -> None:
    """End a running run as the reaper ends a lost worker's: no result, so its job may still
    publish."""
    with ctx.db.tx() as conn:
        assert finish(conn, run_id, "failed", ("running",), now=utcnow(), error=WORKER_LOST)


def lost_client_run(
    runs: RunDriver, ctx: AppContext, client: TestClient, key: str, token: str
) -> LeasedJob:
    """A client run leased, started, listed as alive and lost (no result)."""
    runs.queue(key, target="client")
    job = lease(client, token)
    assert job is not None
    runs.bind(job.run_id, job.job_token)
    assert lease(client, token, (job.job_id,), take=False) is None
    lose(ctx, job.run_id)
    return job


def job_ended_at(ctx: AppContext, run_id: int) -> str | None:
    with ctx.db.read() as conn:
        ended: str | None = conn.execute(
            "SELECT ended_at FROM jobs WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
    return ended


def waits_for(runs: RunDriver, run_id: int, earlier: int) -> bool:
    run = runs.get(run_id)
    return run.status == "queued" and f"run #{earlier}'s" in (run.queue_reason or "")


def test_the_fence_lifts_on_the_accepted_result(
    key: str, client: TestClient, token: str, runs: RunDriver
) -> None:
    first = runs.start(key)
    assert first.complete(status="failed").status_code == 200
    second = runs.queue(key).id
    assert runs.tick() == [second]  # no reaper tick, no provider status
    worker = runs.bind(second, runs.provider.token(second))
    assert worker.complete(status="failed").status_code == 200

    # A client run, while its client still lists the finished worker as alive.
    runs.queue(key, target="client")
    job = lease(client, token)
    assert job is not None
    assert runs.bind(job.run_id, job.job_token).complete(status="failed").status_code == 200
    third = runs.queue(key, target="client").id
    taken = lease(client, token, (job.job_id,))
    assert taken is not None
    assert taken.run_id == third


def test_a_lease_between_its_writes_never_ends_a_job_it_lists(
    key: str,
    client: TestClient,
    token: str,
    runs: RunDriver,
    ctx: AppContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = lost_client_run(runs, ctx, client, key, token)
    reaper = Reaper(ctx)
    reaper.tick(utcnow() - 15 * S)  # its grace tick

    # The reaper ticks right after the lease records that the client was seen.
    touch = clients.touch

    def touch_then_reap(conn: Conn, client_id: int, now: datetime) -> None:
        touch(conn, client_id, now)
        reaper.tick(utcnow())

    monkeypatch.setattr(clients, "touch", touch_then_reap)
    assert lease(client, token, (first.job_id,), take=False) is None
    assert job_ended_at(ctx, first.run_id) is None


def test_push_fence_holds_until_the_provider_says_ended(
    key: str, runs: RunDriver, ctx: AppContext, provider: FakeProvider
) -> None:
    first = runs.start(key)
    lose(ctx, first.run_id)
    second = runs.queue(key).id

    assert runs.tick() == []
    assert waits_for(runs, second, first.run_id)
    reap(ctx, utcnow())  # the provider doesn't know yet
    assert runs.tick() == []

    provider.statuses[FakeProvider.ref(first.run_id)] = JobStatus("ended")
    reap(ctx, utcnow())
    assert runs.tick() == [second]
    assert runs.get(second).queue_reason is None


def test_pull_fence_holds_until_the_client_stops_listing_the_job(
    key: str, client: TestClient, token: str, runs: RunDriver, ctx: AppContext
) -> None:
    first = lost_client_run(runs, ctx, client, key, token)
    second = runs.queue(key, target="client").id

    assert lease(client, token, (first.job_id,)) is None
    assert waits_for(runs, second, first.run_id)
    reap(ctx, utcnow())
    assert lease(client, token, (first.job_id,)) is None  # still listed

    assert lease(client, token, take=False) is None  # its worker exited
    reap(ctx, utcnow())
    taken = lease(client, token)
    assert taken is not None
    assert taken.run_id == second


def test_pull_fence_lifts_when_the_client_is_offline_90_s_after_the_result(
    key: str, client: TestClient, token: str, runs: RunDriver, ctx: AppContext
) -> None:
    first = lost_client_run(runs, ctx, client, key, token)
    second = runs.queue(key, target="client").id
    now = utcnow()

    reap(ctx, now + 60 * S)
    assert lease(client, token, (first.job_id,)) is None
    assert waits_for(runs, second, first.run_id)

    reap(ctx, now + 120 * S)  # nothing heard from the client since `now`
    assert runs.get(second).queue_reason is None  # gone before the client is back
    taken = lease(client, token, (first.job_id,))
    assert taken is not None
    assert taken.run_id == second


@pytest.mark.parametrize("target", ["server", "client"])
def test_the_fence_lifts_once_not_after_has_passed(
    target: str, key: str, client: TestClient, token: str, runs: RunDriver, ctx: AppContext
) -> None:
    if target == "client":
        first = lost_client_run(runs, ctx, client, key, token).run_id
    else:
        first = runs.start(key).run_id
        lose(ctx, first)
    second = runs.queue(key, target=target).id
    take = (
        runs.tick if target == "server" else lambda: [j.run_id for j in [lease(client, token)] if j]
    )

    assert take() == []
    assert waits_for(runs, second, first)

    with ctx.db.tx() as conn:  # no reaper tick: the fence reads not_after itself
        conn.execute("UPDATE jobs SET not_after = ? WHERE run_id = ?", (to_db(utcnow()), first))
    assert take() == [second]


def test_a_push_run_cancelled_while_dispatched_is_cancelled_at_its_provider_once(
    key: str, client: TestClient, runs: RunDriver, ctx: AppContext, provider: FakeProvider
) -> None:
    run_id = runs.queue(key).id
    assert runs.tick() == [run_id]
    r = client.post(f"/api/runs/{run_id}/cancel", headers=CSRF)
    assert r.json()["status"] == "cancelled"

    reaper = Reaper(ctx)
    now = utcnow()
    for tick in range(4):
        reaper.tick(now + tick * 15 * S)
    assert provider.cancelled == [FakeProvider.ref(run_id)]
