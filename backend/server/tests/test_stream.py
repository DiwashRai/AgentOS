"""GET /api/stream end to end, and every publish point: which actions notify which keys.

Events are collected by subscribing on the app's own loop (TestClient's portal), where the broker
is bound; a service publishes before its response returns, so the events are already queued when
the test reads them.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient

from agentos.clock import to_db, utcnow
from agentos.context import AppContext
from agentos.models import StreamEvent, TicketDetail
from agentos.runs.reaper import Reaper
from tests.run_support import CSRF, RunDriver, log_event

CreateTicket = Callable[..., TicketDetail]
# (type, key, id) of each event published since the last call
Drain = Callable[[], list[tuple[str, str, int | None]]]


def portal_of(client: TestClient) -> BlockingPortal:
    portal = client.portal
    assert portal is not None, "use the client inside `with TestClient(...)`"
    return portal


@pytest.fixture
def published(authed: TestClient, ctx: AppContext) -> Iterator[Drain]:
    portal = portal_of(authed)
    sub = portal.call(ctx.broker.subscribe)

    async def take() -> list[StreamEvent]:
        out: list[StreamEvent] = []
        while True:
            try:
                event = await asyncio.wait_for(sub.get(), 0.05)
            except TimeoutError:
                return out
            if event is None:
                return out
            out.append(event)

    def drain() -> list[tuple[str, str, int | None]]:
        return [(e.type, e.key, e.id) for e in portal.call(take)]

    yield drain
    portal.call(ctx.broker.unsubscribe, sub)


def ticket(key: str) -> tuple[str, str, None]:
    return ("ticket", key, None)


def run(key: str, run_id: int) -> list[tuple[str, str, int | None]]:
    return [("run", key, run_id), ticket(key)]


def test_ticket_chat_and_file_changes(
    authed: TestClient, create_ticket: CreateTicket, published: Drain
) -> None:
    t = create_ticket("Streamed")
    key = t.key
    assert published() == [ticket(key)]

    r = authed.post(f"/api/tickets/{key}/chat", json={"body": "hi"}, headers=CSRF)
    assert r.status_code == 201
    assert published() == [("chat", key, r.json()["id"]), ticket(key)]

    assert authed.patch(f"/api/tickets/{key}", json={"title": "Renamed"}, headers=CSRF).is_success
    assert published() == [ticket(key)]
    assert authed.patch(f"/api/tickets/{key}", json={"title": "Renamed"}, headers=CSRF).is_success
    assert published() == []  # nothing changed

    path = f".claude/skills/{t.skill_name}/notes.md"
    r = authed.put(
        f"/api/tickets/{key}/files/{path}",
        json={"content": "x", "mode": "replace", "base_version": 0},
        headers=CSRF,
    )
    assert r.status_code == 200, r.text
    assert published() == [ticket(key)]
    same = {"content": "x", "mode": "replace", "base_version": 1}
    assert authed.put(f"/api/tickets/{key}/files/{path}", json=same, headers=CSRF).is_success
    assert published() == []  # unchanged content appends nothing
    r = authed.delete(f"/api/tickets/{key}/files/{path}?base_version=1", headers=CSRF)
    assert r.status_code == 204, r.text
    assert published() == [ticket(key)]

    detail = authed.get(f"/api/tickets/{key}").json()
    body = {"pipeline": detail["pipeline"], "base_version": detail["pipeline_version"]}
    assert authed.put(f"/api/tickets/{key}/pipeline", json=body, headers=CSRF).is_success
    assert published() == [ticket(key)]

    r = authed.post(f"/api/tickets/{key}/resolve", json={"outcome": "abandoned"}, headers=CSRF)
    assert r.status_code == 200, r.text
    assert published() == [ticket(key)]


def test_a_run_from_dispatch_to_the_gate(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, published: Drain
) -> None:
    key = create_ticket("Run").key
    published()
    queued = runs.queue(key)
    assert published() == run(key, queued.id)
    worker = runs.launch_and_claim(queued.id)
    # dispatched, launch_ref stored, claimed (running)
    assert published() == run(key, queued.id) * 3

    assert worker.events(log_event(worker.next_seq(), "plan", "only a log line")).is_success
    assert worker.events().is_success  # heartbeat
    assert published() == []
    worker.run_stage("plan")
    assert published() == run(key, queued.id)

    skill = worker.manifest.sync_dirs[0]
    files = [{"path": f"{skill}/TASKS.md", "content": "- [ ] one\n", "mode": "replace"}]
    assert worker.sync("plan", files).status_code == 200
    assert published() == [ticket(key)]

    assert worker.complete().status_code == 204
    assert published() == run(key, queued.id)
    assert worker.complete().status_code == 204  # an idempotent retry changes nothing
    assert published() == []


def test_cancel_failed_launch_and_reaper(
    authed: TestClient,
    ctx: AppContext,
    create_ticket: CreateTicket,
    runs: RunDriver,
    published: Drain,
) -> None:
    a, b = create_ticket("A").key, create_ticket("B").key
    published()
    queued = runs.queue(a)
    published()
    assert authed.post(f"/api/runs/{queued.id}/cancel", headers=CSRF).is_success
    assert published() == run(a, queued.id)
    assert authed.post(f"/api/runs/{queued.id}/cancel", headers=CSRF).is_success
    assert published() == []  # already cancelled

    runs.launcher.fail = RuntimeError("no runner")
    failing = runs.queue(a)
    published()
    runs.tick()
    assert published() == run(a, failing.id) * 2  # dispatched, then failed
    runs.launcher.fail = None

    worker = runs.start(b)
    published()
    assert authed.post(f"/api/runs/{worker.run_id}/cancel", headers=CSRF).is_success
    assert published() == run(b, worker.run_id)  # cancel requested
    reaper = Reaper(ctx)
    now = utcnow()
    reaper.tick(now - timedelta(seconds=15))
    runs.set_run(worker.run_id, cancel_requested_at=to_db(now - timedelta(minutes=3)))
    runs.set_run(worker.run_id, last_seen_at=to_db(now))
    assert [r.run_id for r in reaper.tick(now)] == [worker.run_id]
    assert published() == run(b, worker.run_id)


def test_the_stream_route_sends_events_until_the_server_stops(
    authed: TestClient, ctx: AppContext, create_ticket: CreateTicket
) -> None:
    """TestClient returns a streamed body only once it ends: read it in a thread, act, then end
    every stream the way the lifespan does at shutdown."""
    got: dict[str, str] = {}

    def read() -> None:
        r = authed.get("/api/stream")
        got["status"] = str(r.status_code)
        got["type"] = r.headers["content-type"]
        got["body"] = r.text

    reader = threading.Thread(target=read)
    reader.start()
    deadline = time.monotonic() + 5
    while ctx.broker.subscribers == 0:
        assert time.monotonic() < deadline, "the stream never subscribed"
        time.sleep(0.01)
    key = create_ticket("Live").key
    portal_of(authed).call(ctx.broker.close)
    reader.join(5)
    assert not reader.is_alive()
    assert got["status"] == "200"
    assert got["type"].startswith("text/event-stream")
    chunks = [c for c in got["body"].split("\n\n") if c and c != ": ping"]
    assert chunks == [": connected", f'data: {{"type":"ticket","key":"{key}","id":null}}']


def test_the_stream_needs_a_session(client: TestClient) -> None:
    assert client.get("/api/stream").status_code == 401
