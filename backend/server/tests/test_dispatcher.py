"""Dispatcher (SPEC §8 "Dispatcher loop"): oldest first, concurrency, tokens, launch failures,
and the background loop wiring in the lifespan."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable

from fastapi.testclient import TestClient

from agentos.config import Settings
from agentos.context import AppContext
from agentos.main import _every, create_app
from agentos.models import TicketDetail
from tests.run_support import CSRF, FakeLauncher, RunDriver

CreateTicket = Callable[..., TicketDetail]


def queue_three(create_ticket: CreateTicket, runs: RunDriver) -> list[int]:
    return [runs.queue(create_ticket(f"T{i}", workspace_mode="none").key).id for i in range(3)]


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
    worker = runs.start(create_ticket("A", workspace_mode="none").key)
    b = runs.queue(create_ticket("B", workspace_mode="none").key).id
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
    run_id = runs.queue(create_ticket(workspace_mode="none").key).id
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
    run_id = runs.queue(create_ticket(workspace_mode="none").key).id
    runs.set_run(run_id, launcher="github")
    assert runs.tick() == []


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


def test_the_lifespan_runs_the_dispatcher(settings: Settings) -> None:
    launcher = FakeLauncher()
    app = create_app(settings, launcher=launcher)  # loops on
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        client.post("/api/auth/login", json={"username": "dev"}, headers=CSRF)
        r = client.post("/api/tickets", json={"title": "x", "workspace_mode": "none"}, headers=CSRF)
        key = r.json()["key"]
        run_id = client.post(f"/api/tickets/{key}/runs", json={}, headers=CSRF).json()["id"]
        deadline = time.monotonic() + 10
        while not launcher.launched and time.monotonic() < deadline:
            time.sleep(0.05)
        assert [r for r, _, _ in launcher.launched] == [run_id]
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "dispatched"
    # Leaving the block stopped the loops (the lifespan awaited them).
