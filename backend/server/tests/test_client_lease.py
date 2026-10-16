"""The client lease (POST /api/worker/lease): a client token takes the jobs of its user's client
runs, oldest first, one client per run; the handed-out job token then fetches and binds the job as
any worker's does. Each lease stamps the jobs the client lists as `alive`."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from agentos.config import WORKER_PATH
from agentos.context import AppContext
from agentos.models import ClientToken, LeasedJob, LeaseResponse, Me, Role, TicketDetail
from agentos.worker_api import service
from tests.run_support import CSRF, RunDriver, bearer

CreateTicket = Callable[..., TicketDetail]
LoginFn = Callable[..., Me]


def new_token(client: TestClient, name: str = "laptop") -> str:
    r = client.post("/api/me/clients", json={"name": name}, headers=CSRF)
    assert r.status_code == 201, r.text
    token: str = r.json()["token"]
    return token


def lease_raw(
    client: TestClient,
    token: str,
    wait_s: int = 0,
    *,
    take: bool = True,
    alive: Sequence[str] = (),
) -> Response:
    body = {"wait_s": wait_s, "take": take, "alive": list(alive)}
    r: Response = client.post("/api/worker/lease", json=body, headers=bearer(token))
    return r


def lease(client: TestClient, token: str, wait_s: int = 0) -> LeasedJob | None:
    """The job the lease handed out, if any."""
    r = lease_raw(client, token, wait_s)
    assert r.status_code == 200, r.text
    return LeaseResponse.model_validate(r.json()).job


def leased_run(client: TestClient, token: str, wait_s: int = 0) -> int | None:
    job = lease(client, token, wait_s)
    return None if job is None else job.run_id


def client_run(create_ticket: CreateTicket, runs: RunDriver, owner: Me, title: str = "T") -> int:
    return runs.queue(create_ticket(title, repo=None, owner_id=owner.id).key, target="client").id


def alive_at(ctx: AppContext, job_id: str) -> str | None:
    with ctx.db.read() as conn:
        row = conn.execute("SELECT alive_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
    value: str | None = row["alive_at"]
    return value


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

    assert leased_run(client, bobs) == bobs_own
    assert leased_run(client, bobs) is None

    job = lease(client, anns)
    assert job is not None
    assert (job.run_id, job.server_url) == (first, ctx.settings.public_url)
    assert job.job_id.startswith(f"{first}-1-")
    row = runs.db_run(first)
    assert (row["status"], row["launch_ref"]) == ("dispatched", "ann's laptop")
    assert row["dispatched_at"] is not None
    assert job.job_token not in repr(row)

    assert leased_run(client, anns) == second
    assert leased_run(client, anns) is None
    assert runs.db_run(server)["status"] == "queued"  # the server launcher's run


def test_racing_leases_hand_each_run_to_one_client(
    client: TestClient, login: LoginFn, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    ann = login("ann")
    tokens = [new_token(client, f"machine {i}") for i in range(4)]
    queued = {client_run(create_ticket, runs, ann, f"T{i}") for i in range(3)}
    start = threading.Barrier(len(tokens))
    taken: list[int | None] = []

    def race(token: str) -> None:
        start.wait()
        taken.append(leased_run(client, token))

    threads = [threading.Thread(target=race, args=(t,)) for t in tokens]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    handed = [run_id for run_id in taken if run_id is not None]
    assert len(taken) == len(tokens)
    assert sorted(handed) == sorted(queued)  # each run exactly once


def test_a_waiting_lease_gets_a_run_queued_meanwhile(
    client: TestClient, login: LoginFn, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    ann = login("ann")
    token = new_token(client)
    key = create_ticket(repo=None, owner_id=ann.id).key
    queued: list[int] = []
    later = threading.Timer(0.3, lambda: queued.append(runs.queue(key, target="client").id))

    began = time.monotonic()
    later.start()
    taken = leased_run(client, token, wait_s=10)
    later.join()

    assert taken == queued[0]
    assert time.monotonic() - began < 5


def test_the_job_token_fetches_the_job_and_drives_the_run(
    client: TestClient, login: LoginFn, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    ann = login("ann")
    token = new_token(client)
    run_id = client_run(create_ticket, runs, ann)
    job = lease(client, token)
    assert job is not None

    worker = runs.bind(run_id, job.job_token)  # GET /api/worker/job, then `started`
    assert worker.manifest.job.id == job.job_id
    worker.run_stage("plan")
    synced = worker.manifest.context[0]
    files = [{"path": synced.path, "content": "edited", "mode": synced.mode}]
    assert worker.sync("plan", files).status_code == 200
    assert worker.complete().status_code == 200
    assert runs.get(run_id).status == "gated"


def test_a_lease_stamps_its_users_alive_jobs_only(
    client: TestClient,
    login: LoginFn,
    create_ticket: CreateTicket,
    runs: RunDriver,
    ctx: AppContext,
) -> None:
    bob = login("bob")
    bobs = new_token(client)
    client_run(create_ticket, runs, bob, "bob's")
    bobs_job = lease(client, bobs)
    ann = login("ann")
    anns = new_token(client)
    client_run(create_ticket, runs, ann, "ann's")
    anns_job = lease(client, anns)
    assert anns_job is not None
    assert bobs_job is not None
    assert alive_at(ctx, anns_job.job_id) is None

    alive = [anns_job.job_id, bobs_job.job_id, "no-such-job"]
    r = lease_raw(client, anns, take=False, alive=alive)
    assert r.status_code == 200, r.text

    assert alive_at(ctx, anns_job.job_id) is not None
    assert alive_at(ctx, bobs_job.job_id) is None


def test_leasing_marks_the_client_online(client: TestClient, login: LoginFn) -> None:
    login("ann")
    token = new_token(client)
    [before] = client.get("/api/me/clients").json()
    assert (before["online"], before["last_seen_at"]) == (False, None)
    assert lease(client, token) is None
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
    r = lease_raw(client, token, wait_s=25, take=False)
    assert r.status_code == 200, r.text
    answer = LeaseResponse.model_validate(r.json())
    assert answer.job is None
    assert time.monotonic() - began < 5  # answered at once
    assert answer.worker_sha256 == hashlib.sha256(WORKER_PATH.read_bytes()).hexdigest()

    assert runs.db_run(run_id)["status"] == "queued"
    assert ClientToken.model_validate(client.get("/api/me/clients").json()[0]).online


def test_a_lease_with_nothing_queued_takes_no_write_lock(
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
    lease(client, token)  # nothing queued now, but the client was seen
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
def test_a_lease_needs_a_live_token_of_a_writer(
    client: TestClient,
    login: LoginFn,
    ctx: AppContext,
    spoil: Spoil,
    expected: tuple[int, str],
) -> None:
    login("ann")
    token = new_token(client)
    spoil(client, ctx)
    assert error(lease_raw(client, token)) == expected


def test_a_lease_needs_a_known_bearer_token(client: TestClient, login: LoginFn) -> None:
    login("ann")
    new_token(client)
    assert error(lease_raw(client, secrets.token_urlsafe(32))) == (401, "unauthorized")
    no_bearer = client.post("/api/worker/lease", json={"wait_s": 0})  # the session cookie only
    assert error(no_bearer) == (401, "unauthorized")
