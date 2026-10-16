"""The client poll (POST /api/worker/poll): a client token takes its user's client runs, oldest
first, one client per run; the handed-out dispatch token then drives the unchanged worker API."""

from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from agentos.context import AppContext
from agentos.models import ClientPollResponse, ClientToken, Me, Role, TicketDetail
from agentos.worker_api import service
from tests.run_support import CSRF, RunDriver, bearer

CreateTicket = Callable[..., TicketDetail]
LoginFn = Callable[..., Me]


def new_token(client: TestClient, name: str = "laptop") -> str:
    r = client.post("/api/me/clients", json={"name": name}, headers=CSRF)
    assert r.status_code == 201, r.text
    token: str = r.json()["token"]
    return token


def poll_raw(client: TestClient, token: str, wait_s: int = 0, *, take: bool = True) -> Response:
    body = {"wait_s": wait_s, "take": take}
    r: Response = client.post("/api/worker/poll", json=body, headers=bearer(token))
    return r


def poll(client: TestClient, token: str, wait_s: int = 0) -> int | None:
    """The run id the poll handed out, if any."""
    r = poll_raw(client, token, wait_s)
    assert r.status_code == 200, r.text
    run = ClientPollResponse.model_validate(r.json()).run
    return None if run is None else run.run_id


def client_run(create_ticket: CreateTicket, runs: RunDriver, owner: Me, title: str = "T") -> int:
    return runs.queue(create_ticket(title, repo=None, owner_id=owner.id).key, target="client").id


def error(r: Any) -> tuple[int, str]:
    return r.status_code, r.json()["error"]["code"]


def test_a_client_gets_its_users_runs_oldest_first(
    client: TestClient,
    login: LoginFn,
    create_ticket: CreateTicket,
    runs: RunDriver,
    ctx: AppContext,
) -> None:
    bob = login("bob")
    bobs = new_token(client)
    ann = login("ann")
    anns = new_token(client, "ann's laptop")
    first = client_run(create_ticket, runs, ann, "first")
    second = client_run(create_ticket, runs, ann, "second")
    server = runs.queue(create_ticket("server", repo=None, owner_id=ann.id).key).id
    login("bob")
    bobs_own = client_run(create_ticket, runs, bob, "bob's")

    assert poll(client, bobs) == bobs_own
    assert poll(client, bobs) is None

    r = poll_raw(client, anns)
    assert r.status_code == 200, r.text
    run = ClientPollResponse.model_validate(r.json()).run
    assert run is not None
    assert (run.run_id, run.server_url) == (first, ctx.settings.public_url)
    row = runs.db_run(first)
    assert (row["status"], row["launch_ref"]) == ("dispatched", "ann's laptop")
    assert row["dispatched_at"] is not None
    assert run.dispatch_token not in repr(row)

    assert poll(client, anns) == second
    assert poll(client, anns) is None
    assert runs.db_run(server)["status"] == "queued"  # the server launcher's run


def test_racing_polls_hand_each_run_to_one_client(
    client: TestClient, login: LoginFn, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    ann = login("ann")
    tokens = [new_token(client, f"machine {i}") for i in range(4)]
    queued = {client_run(create_ticket, runs, ann, f"T{i}") for i in range(3)}
    start = threading.Barrier(len(tokens))
    taken: list[int | None] = []

    def race(token: str) -> None:
        start.wait()
        taken.append(poll(client, token))

    threads = [threading.Thread(target=race, args=(t,)) for t in tokens]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    handed = [run_id for run_id in taken if run_id is not None]
    assert len(taken) == len(tokens)
    assert sorted(handed) == sorted(queued)  # each run exactly once


def test_a_waiting_poll_gets_a_run_queued_meanwhile(
    client: TestClient, login: LoginFn, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    ann = login("ann")
    token = new_token(client)
    key = create_ticket(repo=None, owner_id=ann.id).key
    queued: list[int] = []
    later = threading.Timer(0.3, lambda: queued.append(runs.queue(key, target="client").id))

    began = time.monotonic()
    later.start()
    taken = poll(client, token, wait_s=10)
    later.join()

    assert taken == queued[0]
    assert time.monotonic() - began < 5


def test_the_dispatch_token_drives_the_worker_api(
    client: TestClient, login: LoginFn, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    ann = login("ann")
    token = new_token(client)
    run_id = client_run(create_ticket, runs, ann)
    r = poll_raw(client, token)
    run = ClientPollResponse.model_validate(r.json()).run
    assert run is not None

    worker = runs.bind(run_id, run.dispatch_token)
    worker.run_stage("plan")
    synced = worker.manifest.context[0]
    files = [{"path": synced.path, "content": "edited", "mode": synced.mode}]
    assert worker.sync("plan", files).status_code == 200
    assert worker.complete().status_code == 200
    assert runs.get(run_id).status == "gated"


def test_polling_marks_the_client_online(client: TestClient, login: LoginFn) -> None:
    login("ann")
    token = new_token(client)
    [before] = client.get("/api/me/clients").json()
    assert (before["online"], before["last_seen_at"]) == (False, None)
    assert poll(client, token) is None
    after = ClientToken.model_validate(client.get("/api/me/clients").json()[0])
    assert after.online
    assert after.last_seen_at is not None


def test_a_busy_clients_heartbeat_keeps_it_online_and_takes_nothing(
    client: TestClient, login: LoginFn, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    ann = login("ann")
    token = new_token(client)
    run_id = client_run(create_ticket, runs, ann)

    began = time.monotonic()
    r = poll_raw(client, token, wait_s=25, take=False)
    assert r.status_code == 200, r.text
    assert ClientPollResponse.model_validate(r.json()).run is None
    assert time.monotonic() - began < 5  # answered at once

    assert runs.db_run(run_id)["status"] == "queued"
    assert ClientToken.model_validate(client.get("/api/me/clients").json()[0]).online


def test_a_poll_with_nothing_queued_takes_no_write_lock(
    client: TestClient, login: LoginFn, ctx: AppContext
) -> None:
    login("ann")
    token = new_token(client)
    with ctx.db.tx():  # another writer holds the lock (a take would wait for its busy timeout)
        began = time.monotonic()
        assert service.take_client_run(ctx.db, token, "http://x") is None
        assert time.monotonic() - began < 1


def test_a_queued_client_run_says_whether_its_client_is_online(
    client: TestClient, login: LoginFn, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    ann = login("ann")
    token = new_token(client)
    key = create_ticket(repo=None, owner_id=ann.id).key
    run_id = runs.queue(key, target="client").id
    assert runs.get(run_id).client_online is False
    client.post(f"/api/runs/{run_id}/cancel", headers=CSRF)
    poll(client, token)  # nothing queued now, but the client was seen
    run_id = runs.queue(key, target="client").id
    assert runs.get(run_id).client_online is True
    latest = client.get(f"/api/tickets/{key}").json()["latest_run"]
    assert latest["client_online"] is True


# ------------------------------------------------------------------ authentication


Spoil = Callable[[TestClient, AppContext], None]


def _revoke(client: TestClient, ctx: AppContext) -> None:
    for t in client.get("/api/me/clients").json():
        client.delete(f"/api/me/clients/{t['id']}", headers=CSRF)


def _disable(client: TestClient, ctx: AppContext) -> None:
    with ctx.db.tx() as conn:
        conn.execute("UPDATE users SET disabled = 1")


def _role(role: Role) -> Spoil:
    def change(client: TestClient, ctx: AppContext) -> None:
        with ctx.db.tx() as conn:
            conn.execute("UPDATE users SET role = ?", (role,))

    return change


@pytest.mark.parametrize(
    ("spoil", "expected"),
    [
        pytest.param(_revoke, (401, "unauthorized"), id="revoked"),
        pytest.param(_disable, (401, "unauthorized"), id="user-disabled"),
        pytest.param(_role("pending"), (401, "unauthorized"), id="user-pending"),
        pytest.param(_role("viewer"), (403, "forbidden"), id="viewer"),
    ],
)
def test_a_poll_needs_a_live_token_of_a_writer(
    client: TestClient,
    login: LoginFn,
    ctx: AppContext,
    spoil: Spoil,
    expected: tuple[int, str],
) -> None:
    login("ann")
    token = new_token(client)
    spoil(client, ctx)
    assert error(poll_raw(client, token)) == expected


def test_a_poll_needs_a_known_bearer_token(client: TestClient, login: LoginFn) -> None:
    login("ann")
    new_token(client)
    assert error(poll_raw(client, secrets.token_urlsafe(32))) == (401, "unauthorized")
    no_bearer = client.post("/api/worker/poll", json={"wait_s": 0})  # the session cookie only
    assert error(no_bearer) == (401, "unauthorized")
