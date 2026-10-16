"""The fence: a ticket's next run stays queued, with a reason, until the previous job can no longer
publish: its accepted result says so; for a worker that sent none, push: its provider says ended;
pull: the client instance that last listed it leaves it out of a lease (never another instance,
never because a client went quiet); any route: past `not_after`. Plus tables for `job_ended` and
`not_after`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from agentos.clock import from_db, to_db, utcnow
from agentos.context import AppContext
from agentos.launchers.base import JobStatus
from agentos.models import LeasedJob, LeaseResponse, Me, Route, TicketDetail
from agentos.runs.reaper import WORKER_LOST, JobFacts, Reaper, job_ended
from agentos.runs.service import NEVER_STARTED, WORKER_EXITED, Snapshot, finish, not_after
from tests.run_support import CSRF, INSTANCE, FakeProvider, RunDriver, bearer

OTHER = "test-client-instance-b"  # another client process with the same token
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


# A pull job of a run that ended without a result, dispatched at T0.
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
    read=Snapshot("succeeded", "laptop", to_db(T0), to_db(T0)),
)
PUSH = replace(PULL, start="push", provider="github", ref="r")
LATE = T0 + timedelta(hours=2) + S


@pytest.mark.parametrize(
    ("job", "now", "status", "ended"),
    [
        pytest.param(PUSH, T0 + 60 * S, None, False, id="push-no-status"),
        pytest.param(PUSH, T0 + 60 * S, JobStatus("running"), False, id="push-running"),
        pytest.param(PUSH, T0 + 60 * S, JobStatus("ended"), True, id="push-ended"),
        pytest.param(PUSH, LATE, None, True, id="push-past-not-after"),
        # Only its lister's lease ends a pull job early (`lease_client`), never the reaper.
        pytest.param(PULL, T0 + timedelta(hours=1), None, False, id="pull-before-not-after"),
        pytest.param(PULL, LATE, None, True, id="pull-past-not-after"),
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
    client: TestClient,
    token: str,
    alive: tuple[str, ...] = (),
    take: bool = True,
    instance: str = INSTANCE,
) -> LeasedJob | None:
    body = {"instance": instance, "wait_s": 0, "take": take, "alive": list(alive)}
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


def listed_client_run(runs: RunDriver, client: TestClient, key: str, token: str) -> LeasedJob:
    """A client run leased, started and listed as alive by its client's next lease."""
    runs.queue(key, target="client")
    job = lease(client, token)
    assert job is not None
    runs.bind(job.run_id, job.job_token)
    assert lease(client, token, (job.job_id,), take=False) is None
    return job


def lost_client_run(
    runs: RunDriver, ctx: AppContext, client: TestClient, key: str, token: str
) -> LeasedJob:
    """A client run leased, started, listed as alive and lost (no result)."""
    job = listed_client_run(runs, client, key, token)
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


def test_a_lease_that_leaves_out_a_running_job_ends_its_run_and_job(
    key: str, client: TestClient, token: str, runs: RunDriver, ctx: AppContext
) -> None:
    first = listed_client_run(runs, client, key, token)

    assert lease(client, token, take=False) is None  # its worker exited without a result
    run = runs.get(first.run_id)
    assert (run.status, run.error) == ("failed", WORKER_EXITED)
    assert job_ended_at(ctx, first.run_id) is not None

    second = runs.queue(key, target="client").id  # no fence: no reaper tick needed
    taken = lease(client, token)
    assert taken is not None
    assert taken.run_id == second


def test_two_instances_of_one_token_each_end_only_their_own_jobs(
    key: str,
    create_ticket: CreateTicket,
    me: Me,
    client: TestClient,
    token: str,
    runs: RunDriver,
) -> None:
    a = listed_client_run(runs, client, key, token)
    runs.queue(create_ticket("B's", repo=None, owner_id=me.id).key, target="client")
    b = lease(client, token, instance=OTHER)  # leaves A's job out
    assert b is not None
    runs.bind(b.run_id, b.job_token)
    assert lease(client, token, (b.job_id,), take=False, instance=OTHER) is None
    assert lease(client, token, (a.job_id,), take=False) is None  # leaves B's job out
    assert [runs.get(j.run_id).status for j in (a, b)] == ["running", "running"]

    assert lease(client, token, (a.job_id,), take=False, instance=OTHER) is None  # B's exited
    assert [runs.get(j.run_id).status for j in (a, b)] == ["running", "failed"]


def test_an_unbound_job_its_lister_leaves_out_ends_its_run_and_refuses_a_late_bind(
    key: str, client: TestClient, token: str, runs: RunDriver, ctx: AppContext
) -> None:
    run_id = runs.queue(key, target="client").id
    job = lease(client, token)
    assert job is not None
    assert lease(client, token, (job.job_id,), take=False) is None  # its worker started...
    assert lease(client, token, take=False) is None  # ...and exited before it bound

    run = runs.get(run_id)
    assert (run.status, run.error) == ("failed", NEVER_STARTED)
    assert job_ended_at(ctx, run_id) is not None
    late = {**bearer(job.job_token), "X-AgentOS-Instance": "late-worker"}
    assert client.get("/api/worker/job", headers=late).status_code == 410
    r = client.post("/api/worker/journal", json={"records": []}, headers=late)
    assert (r.status_code, r.json()["error"]["code"]) == (410, "run_gone")


def test_an_ended_job_is_never_bound_whatever_its_run_says(
    key: str, client: TestClient, token: str, runs: RunDriver, ctx: AppContext
) -> None:
    run_id = runs.queue(key, target="client").id
    job = lease(client, token)
    assert job is not None
    with ctx.db.tx() as conn:  # its job ended while the run still reads dispatched
        conn.execute("UPDATE jobs SET ended_at = ? WHERE run_id = ?", (to_db(utcnow()), run_id))

    late = {**bearer(job.job_token), "X-AgentOS-Instance": "late-worker"}
    assert client.get("/api/worker/job", headers=late).status_code == 410
    r = client.post("/api/worker/journal", json={"records": []}, headers=late)
    assert (r.status_code, r.json()["error"]["code"]) == (410, "run_gone")


def test_a_restarted_client_takes_over_the_survivors_it_lists(
    key: str, client: TestClient, token: str, runs: RunDriver, ctx: AppContext
) -> None:
    survivor = listed_client_run(runs, client, key, token)
    # The new instance lists it (its lock is held under the same root); the old one is gone.
    assert lease(client, token, (survivor.job_id,), take=False, instance=OTHER) is None
    assert lease(client, token, take=False) is None  # the old instance no longer judges it
    assert runs.get(survivor.run_id).status == "running"

    assert lease(client, token, take=False, instance=OTHER) is None  # the survivor exited
    run = runs.get(survivor.run_id)
    assert (run.status, run.error) == ("failed", WORKER_EXITED)
    assert job_ended_at(ctx, survivor.run_id) is not None


def test_a_job_its_instance_never_listed_is_left_alone(
    key: str,
    create_ticket: CreateTicket,
    me: Me,
    client: TestClient,
    token: str,
    runs: RunDriver,
) -> None:
    listed = listed_client_run(runs, client, key, token)
    unlisted = create_ticket("U", repo=None, owner_id=me.id).key
    runs.queue(unlisted, target="client")
    taken = lease(client, token, (listed.job_id,))  # leased, but not started or listed yet
    assert taken is not None
    assert lease(client, token, (listed.job_id,), take=False) is None
    runs.bind(taken.run_id, taken.job_token)  # started, never listed yet
    assert lease(client, token, (listed.job_id,), take=False) is None

    assert runs.get(listed.run_id).status == "running"
    assert runs.get(taken.run_id).status == "running"


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


def test_pull_fence_holds_until_its_lister_leaves_the_job_out(
    key: str, client: TestClient, token: str, runs: RunDriver, ctx: AppContext
) -> None:
    first = lost_client_run(runs, ctx, client, key, token)
    second = runs.queue(key, target="client").id

    assert lease(client, token, (first.job_id,)) is None
    assert waits_for(runs, second, first.run_id)
    assert lease(client, token, take=False, instance=OTHER) is None  # not its lister
    reap(ctx, utcnow())  # nor does the reaper end a pull job before its not_after
    assert waits_for(runs, second, first.run_id)

    assert lease(client, token, take=False) is None  # its lister: the worker exited
    assert runs.get(second).queue_reason is None
    taken = lease(client, token)
    assert taken is not None
    assert taken.run_id == second


def test_pull_fence_holds_while_the_client_is_offline(
    key: str, client: TestClient, token: str, runs: RunDriver, ctx: AppContext
) -> None:
    """Its worker outlives the client and may still publish: only a lease or not_after tells."""
    first = lost_client_run(runs, ctx, client, key, token)
    second = runs.queue(key, target="client").id
    assert lease(client, token, (first.job_id,)) is None

    with ctx.db.read() as conn:
        row = conn.execute("SELECT not_after FROM jobs WHERE id = ?", (first.job_id,)).fetchone()
    reap(ctx, from_db(row["not_after"]))  # nothing heard from the client until then
    assert waits_for(runs, second, first.run_id)
    assert lease(client, token, (first.job_id,)) is None  # back, and the worker still runs


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
    # Client: a new instance on another root, which can't see the old worker, takes.
    take = (
        runs.tick
        if target == "server"
        else lambda: [j.run_id for j in [lease(client, token, instance=OTHER)] if j]
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
