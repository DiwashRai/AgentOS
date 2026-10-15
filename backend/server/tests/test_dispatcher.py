"""Dispatcher (SPEC §8 "Dispatcher loop"): oldest first, concurrency, tokens, launch failures,
and the background loops (dispatcher and reaper) wired into the lifespan."""

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
from agentos.main import _every, create_app
from agentos.models import Me, RunDetail, TicketDetail
from agentos.runs import dispatcher
from tests.run_support import CSRF, FakeLauncher, RunDriver

CreateTicket = Callable[..., TicketDetail]
LoginFn = Callable[..., Me]


def queue_three(create_ticket: CreateTicket, runs: RunDriver) -> list[int]:
    return [runs.queue(create_ticket(f"T{i}", repo=None).key).id for i in range(3)]


def status(runs: RunDriver, run_id: int) -> str:
    s: str = runs.db_run(run_id)["status"]
    return s


def test_oldest_first_within_max_concurrency(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, launcher: FakeLauncher
) -> None:
    a, b, c = queue_three(create_ticket, runs)
    assert runs.tick() == [a, b]  # max_concurrency = 2
    assert [r for r, _, _ in launcher.launched] == [a, b]
    assert [status(runs, r) for r in (a, b, c)] == ["dispatched", "dispatched", "queued"]
    assert runs.tick() == []  # still full
    authed.post(f"/api/runs/{a}/cancel", headers=CSRF)
    assert runs.tick() == [c]


def test_running_runs_count_towards_the_limit(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, launcher: FakeLauncher
) -> None:
    launcher.max_concurrency = 1
    worker = runs.start(create_ticket("A", repo=None).key)
    b = runs.queue(create_ticket("B", repo=None).key).id
    assert runs.tick() == []
    worker.complete()
    assert runs.tick() == [b]


def test_unlimited(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, launcher: FakeLauncher
) -> None:
    launcher.max_concurrency = None
    ids = queue_three(create_ticket, runs)
    assert runs.tick() == ids


def test_tokens_and_launch_ref(
    authed: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    launcher: FakeLauncher,
    ctx: AppContext,
) -> None:
    run_id = runs.queue(create_ticket(repo=None).key).id
    runs.tick()
    [(launched_id, server_url, token)] = launcher.launched
    assert launched_id == run_id
    assert server_url == ctx.settings.effective_worker_url
    assert len(token) >= 43
    row = runs.db_run(run_id)
    assert row["dispatch_token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert row["dispatched_at"] is not None
    assert row["launch_ref"] == f"fake-{run_id}"
    assert token not in repr(row)  # only the hash is stored
    assert runs.get(run_id).launch_ref == f"fake-{run_id}"


def test_each_dispatch_gets_its_own_token(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, launcher: FakeLauncher
) -> None:
    queue_three(create_ticket, runs)
    launcher.max_concurrency = 3
    runs.tick()
    assert len({t for _, _, t in launcher.launched}) == 3


def test_launch_failure_fails_the_run_and_carries_on(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, launcher: FakeLauncher
) -> None:
    launcher.fail = OSError("no python here")
    a, b, c = queue_three(create_ticket, runs)
    assert runs.tick() == [a, b, c]  # failed launches don't occupy a slot
    detail = runs.get(a)
    assert detail.status == "failed"
    assert detail.error == "launch failed: no python here"
    assert detail.finished_at is not None
    assert [s.status for s in detail.stage_runs] == ["cancelled"]
    launcher.fail = None
    retry = runs.queue(detail.ticket_key)  # Retry works after a launch failure
    assert retry.from_stage == "plan"
    assert runs.tick() == [retry.id]


def test_other_launchers_runs_are_left_alone(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, ctx: AppContext
) -> None:
    run_id = runs.queue(create_ticket(repo=None).key).id
    runs.set_run(run_id, launcher="github")
    assert runs.tick() == []


def test_client_runs_are_neither_launched_nor_counted(
    client: TestClient,
    login: LoginFn,
    create_ticket: CreateTicket,
    runs: RunDriver,
    launcher: FakeLauncher,
) -> None:
    me = login("dev")
    launcher.max_concurrency = 1
    busy, waiting = (
        runs.queue(create_ticket(title, repo=None, owner_id=me.id).key, target="client").id
        for title in ("busy", "waiting")
    )
    runs.set_run(busy, status="running")  # a client is working on it
    server = runs.queue(create_ticket("server", repo=None).key).id
    assert runs.tick() == [server]
    assert [r for r, _, _ in launcher.launched] == [server]
    assert status(runs, waiting) == "queued"


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
    settings: Settings, launcher: FakeLauncher, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, AppContext]]:
    """The app with its background loops on, ticking every 50 ms, signed in as dev."""
    monkeypatch.setattr(dispatcher, "INTERVAL_S", 0.05)
    monkeypatch.setattr(main, "REAPER_INTERVAL_S", 0.05)
    app = create_app(settings, launcher=launcher)  # loops on
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
    launcher = FakeLauncher()
    with with_loops(settings, launcher, monkeypatch) as (client, _):
        run_id = dispatch(client)
        wait_for("the launch", lambda: bool(launcher.launched))
        assert [r for r, _, _ in launcher.launched] == [run_id]
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "dispatched"


def test_the_lifespan_runs_the_reaper(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """A launched worker that never claims is failed by the reaper (rule 2), seen via the API."""
    launcher = FakeLauncher()
    with with_loops(settings, launcher, monkeypatch) as (client, ctx):
        run_id = dispatch(client)
        wait_for("the launch", lambda: bool(launcher.launched))
        long_ago = to_db(utcnow() - timedelta(seconds=ctx.space.claim_timeout_s + 60))
        with ctx.db.tx() as conn:
            conn.execute("UPDATE runs SET dispatched_at = ? WHERE id = ?", (long_ago, run_id))
        wait_for(
            "the reaper",
            lambda: client.get(f"/api/runs/{run_id}").json()["status"] != "dispatched",
        )
        run = RunDetail.model_validate(client.get(f"/api/runs/{run_id}").json())
        assert (run.status, run.error) == ("failed", "worker never connected")
        assert [s.status for s in run.stage_runs] == ["cancelled"]
