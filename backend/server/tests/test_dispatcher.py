"""Dispatcher: oldest first through each run's stored provider, tokens, start errors, runs held
by an unconfigured provider, and the background loops (dispatcher and reaper) in the lifespan."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from agentos import main
from agentos.clock import to_db, utcnow
from agentos.config import Settings
from agentos.context import AppContext
from agentos.launchers.base import Provider, ProviderError
from agentos.mailbox import dispatcher as mailbox_dispatcher
from agentos.main import _every, create_app
from agentos.models import Me, Route, RunDetail, TicketDetail
from agentos.runs import dispatcher
from tests.mailbox_support import FakeMailbox
from tests.run_support import CSRF, FakeProvider, RunDriver

CreateTicket = Callable[..., TicketDetail]
LoginFn = Callable[..., Me]


_: Provider = FakeProvider()


def queue_three(create_ticket: CreateTicket, runs: RunDriver) -> list[int]:
    return [runs.queue(create_ticket(f"T{i}", repo=None).key).id for i in range(3)]


def status(runs: RunDriver, run_id: int) -> str:
    s: str = runs.db_run(run_id)["status"]
    return s


def test_oldest_first(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, provider: FakeProvider
) -> None:
    ids = queue_three(create_ticket, runs)
    assert runs.tick() == ids  # no limit on our side: the provider queues
    assert provider.launched == ids
    assert [status(runs, r) for r in ids] == ["dispatched"] * 3
    assert runs.tick() == []


def test_job_token_and_launch_ref(
    authed: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    provider: FakeProvider,
    ctx: AppContext,
) -> None:
    run_id = runs.queue(create_ticket(repo=None).key).id
    runs.tick()
    [job] = provider.started
    assert (job.run_id, job.server_url) == (run_id, ctx.settings.effective_worker_url)
    assert len(job.job_token) >= 43
    with ctx.db.read() as conn:
        row = dict(conn.execute("SELECT * FROM jobs WHERE run_id = ?", (run_id,)).fetchone())
    assert row["id"] == job.job_id
    assert row["token_hash"] == hashlib.sha256(job.job_token.encode()).hexdigest()
    assert job.job_token not in repr(row)  # only the hash is stored
    assert runs.db_run(run_id)["dispatched_at"] is not None
    detail = runs.get(run_id)
    assert detail.launch_ref == detail.launch_url == FakeProvider.ref(run_id)


def test_each_dispatch_gets_its_own_token(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, provider: FakeProvider
) -> None:
    queue_three(create_ticket, runs)
    runs.tick()
    assert len({job.job_token for job in provider.started}) == 3


def test_a_start_error_fails_the_run_and_carries_on(
    authed: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    provider: FakeProvider,
    ctx: AppContext,
) -> None:
    provider.fail = ProviderError("GitHub answered 403: nope")
    a, b, c = queue_three(create_ticket, runs)
    assert runs.tick() == [a, b, c]
    detail = runs.get(a)
    assert (detail.status, detail.error) == ("failed", "launch failed: GitHub answered 403: nope")
    assert detail.finished_at is not None
    assert [s.status for s in detail.stage_runs] == ["cancelled"]
    with ctx.db.read() as conn:
        job = conn.execute("SELECT ended_at FROM jobs WHERE run_id = ?", (a,)).fetchone()
    assert job["ended_at"] is not None  # nothing started, so nothing can still publish
    provider.fail = None
    retry = runs.queue(detail.ticket_key)  # Retry works after a start error
    assert retry.from_stage == "plan"
    assert runs.tick() == [retry.id]


def test_a_queued_run_keeps_its_route_after_the_launcher_changes(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, provider: FakeProvider
) -> None:
    before = runs.queue(create_ticket("before", repo=None).key).id
    other = FakeProvider()
    runs.ctx.route = Route("push", "other", "live")
    runs.ctx.providers = {"other": other}
    after = runs.queue(create_ticket("after", repo=None).key).id

    assert runs.tick() == [after]
    assert other.launched == [after]
    held = runs.get(before)
    assert held.status == "queued"
    assert held.queue_reason is not None
    assert "github" in held.queue_reason  # its own provider, which is no longer configured
    assert runs.db_run(before)["provider"] == "github"

    runs.ctx.providers["github"] = provider
    assert runs.tick() == [before]
    assert provider.launched == [before]
    assert runs.get(before).queue_reason is None


def test_file_report_runs_wait_with_a_reason_until_a_mailbox_takes_them(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    """Queued for the mailbox, then AGENTOS_LAUNCHER switched to github with no store."""
    run_id = runs.queue(create_ticket(repo=None).key).id
    runs.set_run(run_id, report="file")
    assert runs.tick() == []
    reason = runs.get(run_id).queue_reason
    assert reason is not None
    assert "mailbox" in reason

    mailbox = FakeMailbox()
    runs.ctx.mailbox = mailbox
    assert runs.tick() == []  # the runs dispatcher never takes a file run
    assert mailbox_dispatcher.tick(runs.ctx, mailbox) == [run_id]
    assert runs.get(run_id).queue_reason is None


def test_client_runs_are_never_started_by_the_dispatcher(
    client: TestClient,
    login: LoginFn,
    create_ticket: CreateTicket,
    runs: RunDriver,
    provider: FakeProvider,
) -> None:
    me = login("dev")
    client.post("/api/me/clients", json={"name": "laptop"}, headers=CSRF)
    waiting = runs.queue(create_ticket("waiting", repo=None, owner_id=me.id).key, target="client")
    server = runs.queue(create_ticket("server", repo=None).key).id
    assert runs.tick() == [server]
    assert provider.launched == [server]
    assert status(runs, waiting.id) == "queued"


# ------------------------------------------------------------------ the loops


def test_a_failing_tick_does_not_stop_the_loop() -> None:
    calls: list[int] = []

    async def main() -> None:
        stop = asyncio.Event()

        def tick() -> None:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("boom")
            if len(calls) == 3:
                stop.set()

        await asyncio.wait_for(_every("test", 0.01, tick, stop), timeout=10)

    asyncio.run(main())
    assert len(calls) == 3


@contextmanager
def with_loops(
    settings: Settings, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, AppContext]]:
    """The app with its background loops on, ticking every 50 ms, signed in as dev."""
    monkeypatch.setattr(dispatcher, "INTERVAL_S", 0.05)
    monkeypatch.setattr(main, "REAPER_INTERVAL_S", 0.05)
    app = create_app(settings, providers={"github": provider})  # loops on
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.post("/api/auth/login", json={"username": "dev"}, headers=CSRF).is_success
        yield client, app.state.ctx
    # Leaving the block stopped the loops (the lifespan awaited them).


def dispatch(client: TestClient) -> int:
    r = client.post("/api/tickets", json={"title": "x", "repo": None}, headers=CSRF)
    run = client.post(f"/api/tickets/{r.json()['key']}/runs", json={}, headers=CSRF)
    run_id: int = run.json()["id"]
    return run_id


def wait_for(what: str, probe: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not probe():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.02)


def test_the_lifespan_runs_the_dispatcher(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider()
    with with_loops(settings, provider, monkeypatch) as (client, _):
        run_id = dispatch(client)
        wait_for("the start", lambda: bool(provider.launched))
        assert provider.launched == [run_id]
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "dispatched"


def test_the_lifespan_runs_the_reaper(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """A launched worker that never starts is failed by the reaper (rule 2), seen via the API."""
    provider = FakeProvider()
    with with_loops(settings, provider, monkeypatch) as (client, ctx):
        run_id = dispatch(client)
        wait_for("the start", lambda: bool(provider.launched))
        long_ago = to_db(utcnow() - timedelta(seconds=ctx.space.claim_timeout_s + 60))
        with ctx.db.tx() as conn:
            conn.execute("UPDATE runs SET dispatched_at = ? WHERE id = ?", (long_ago, run_id))
        wait_for(
            "the reaper",
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] != "dispatched",
        )
        run = RunDetail.model_validate(client.get(f"/api/runs/{run_id}").json())
        assert (run.status, run.error) == ("failed", "never started")
        assert [s.status for s in run.stage_runs] == ["cancelled"]
