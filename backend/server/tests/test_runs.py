"""Run routes (SPEC §8, §10): dispatch validation, reads, cancel, the bundle lock and the board
fields through a whole run lifecycle. The worker side is driven through the worker API."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentos.clock import to_db, utcnow
from agentos.context import AppContext
from agentos.models import JobPackage, Me, Route, RunSummary, TicketDetail, TicketSummary
from agentos.runs.service import BUDGET_GRACE, NEVER_STARTED, OVER_BUDGET
from tests.run_support import (
    CSRF,
    INSTANCE,
    LEASE_SEQ,
    FakeProvider,
    RunDriver,
    bearer,
    event,
    log_event,
)

CreateTicket = Callable[..., TicketDetail]
LoginFn = Callable[..., Me]


def put_pipeline(client: TestClient, key: str, edit: Callable[[dict[str, Any]], None]) -> None:
    t = client.get(f"/api/tickets/{key}").json()
    pipeline = t["pipeline"]
    edit(pipeline)
    r = client.put(
        f"/api/tickets/{key}/pipeline",
        json={"pipeline": pipeline, "base_version": t["pipeline_version"]},
        headers=CSRF,
    )
    assert r.status_code == 200, r.text


def stage_of(pipeline: dict[str, Any], stage_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(s for s in pipeline["stages"] if s["id"] == stage_id)
    return found


def edit_in_db(ctx: AppContext, key: str, edit: Callable[[dict[str, Any]], None]) -> None:
    """Change a ticket's pipeline behind the API's back (states the API refuses to save)."""
    with ctx.db.tx() as conn:
        row = conn.execute("SELECT pipeline FROM tickets WHERE key = ?", (key,)).fetchone()
        pipeline = json.loads(row["pipeline"])
        edit(pipeline)
        conn.execute("UPDATE tickets SET pipeline = ? WHERE key = ?", (json.dumps(pipeline), key))


def error(r: Any) -> tuple[int, str]:
    return r.status_code, r.json()["error"]["code"]


def run_count(ctx: AppContext) -> int:
    with ctx.db.read() as conn:
        n: int = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    return n


def route(runs: RunDriver, run_id: int) -> tuple[str, str | None, str]:
    row = runs.db_run(run_id)
    return row["start"], row["provider"], row["report"]


# ------------------------------------------------------------------ dispatch


@pytest.mark.parametrize(
    ("start", "launch_ref", "url"),
    [
        (
            "push",
            "https://github.com/o/r/actions/runs/9",
            "https://github.com/o/r/actions/runs/9",
        ),
        ("push", "4242", None),  # a ref that isn't a URL
        ("push", None, None),  # not launched yet
        ("pull", "https://laptop", None),  # a client's name
    ],
)
def test_launch_url_links_the_github_run(
    authed: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    start: str,
    launch_ref: str | None,
    url: str | None,
) -> None:
    t = create_ticket()
    run = runs.queue(t.key)
    runs.set_run(run.id, start=start, launch_ref=launch_ref)
    assert runs.get(run.id).launch_url == url
    latest = authed.get(f"/api/tickets/{t.key}").json()["latest_run"]
    assert latest["launch_url"] == url


def test_dispatch_queues_the_next_segment(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, ctx: AppContext
) -> None:
    t = create_ticket()
    run = runs.queue(t.key)
    assert run.status == "queued"
    assert run.from_stage == "plan"
    assert run.stage_ids == ["plan"]  # plan gates
    assert run.ticket_key == t.key
    assert run.created_by is not None
    assert run.created_by.display_name == "dev"
    assert run.started_at is None
    assert run.launch_ref is None
    assert run.launch_url is None
    row = runs.db_run(run.id)
    assert row["time_budget_s"] == 3600
    detail = runs.get(run.id)
    assert [(s.stage_id, s.name, s.idx, s.status) for s in detail.stage_runs] == [
        ("plan", "Plan", 0, "pending")
    ]
    ticket = TicketDetail.model_validate(authed.get(f"/api/tickets/{t.key}").json())
    assert ticket.active_run_id == run.id
    assert ticket.updated_at >= t.updated_at
    assert run_count(ctx) == 1


def test_dispatch_from_a_stage_runs_to_the_end_and_sums_the_budget(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    t = create_ticket()
    run = runs.queue(t.key, "implement")
    assert run.stage_ids == ["implement", "review", "open-pr"]  # lint is disabled
    assert runs.db_run(run.id)["time_budget_s"] == 3 * 3600


@pytest.mark.parametrize("state", ["done", "closed"])
def test_dispatch_needs_an_open_ticket(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, state: str
) -> None:
    t = create_ticket()
    authed.patch(f"/api/tickets/{t.key}", json={"state": state}, headers=CSRF)
    assert error(runs.dispatch(t.key)) == (409, "invalid_stage")


def test_dispatch_needs_the_repo_in_the_catalog(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, ctx: AppContext
) -> None:
    t = create_ticket()
    with ctx.db.tx() as conn:  # as if the repo was removed from the catalog
        conn.execute("UPDATE tickets SET repo = 'gone' WHERE key = ?", (t.key,))
    r = runs.dispatch(t.key)
    assert error(r) == (422, "validation_error")
    assert "'gone'" in r.json()["error"]["message"]
    assert run_count(ctx) == 0


def test_dispatch_unknown_ticket(authed: TestClient, runs: RunDriver) -> None:
    assert error(runs.dispatch("AOS-999")) == (404, "ticket_not_found")


@pytest.mark.parametrize(
    ("from_stage", "why"),
    [("nope", "no such stage"), ("lint", "disabled")],
)
def test_dispatch_rejects_bad_stages(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, from_stage: str, why: str
) -> None:
    t = create_ticket()
    r = runs.dispatch(t.key, from_stage)
    assert error(r) == (422, "invalid_stage")
    assert why in r.json()["error"]["message"]


def test_dispatch_rejects_requires_repo_in_no_repo_mode(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, ctx: AppContext
) -> None:
    # A no-repo ticket can't enable open-pr (pipeline save refuses), so flip it in the db.
    t = create_ticket(repo=None)
    edit_in_db(ctx, t.key, lambda p: stage_of(p, "open-pr").update(enabled=True))
    r = runs.dispatch(t.key, "review")
    assert error(r) == (422, "invalid_stage")
    assert "requires a repo" in r.json()["error"]["message"]
    assert run_count(ctx) == 0


def test_nothing_to_run_after_the_last_stage(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    t = create_ticket(repo=None)
    worker = runs.start(t.key, "review")  # the last enabled stage (open-pr is disabled)
    worker.run_stage("review")
    assert worker.complete().status_code == 200
    assert runs.get(worker.run_id).status == "succeeded"
    r = runs.dispatch(t.key)
    assert error(r) == (422, "invalid_stage")
    assert "pick a stage" in r.json()["error"]["message"]
    assert runs.queue(t.key, "plan").status == "queued"  # "Run from…" still works


def test_render_error_leaves_no_run(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, ctx: AppContext
) -> None:
    t = create_ticket()
    # Prompts are render-checked on save, so break one in the db (as a later Jinja change could).
    edit_in_db(ctx, t.key, lambda p: stage_of(p, "plan").update(prompt="{{ nope.x }}"))
    r = runs.dispatch(t.key)
    assert error(r) == (422, "render_error")
    assert "stage plan" in r.json()["error"]["message"]
    assert run_count(ctx) == 0
    with ctx.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM stage_runs").fetchone()[0] == 0


def test_one_active_run_per_ticket(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    t = create_ticket()
    runs.queue(t.key)
    assert error(runs.dispatch(t.key)) == (409, "run_active")
    assert error(runs.dispatch(t.key, "implement")) == (409, "run_active")


def test_shell_stage_without_command_is_rejected(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, ctx: AppContext
) -> None:
    t = create_ticket(repo=None)
    edit_in_db(ctx, t.key, lambda p: stage_of(p, "lint").update(enabled=True))
    r = runs.dispatch(t.key, "lint")
    assert error(r) == (422, "invalid_stage")
    assert "no command" in r.json()["error"]["message"]


# ------------------------------------------------------------------ dispatch target


def new_client(client: TestClient) -> str:
    """A client token for the signed-in user."""
    r = client.post("/api/me/clients", json={"name": "laptop"}, headers=CSRF)
    assert r.status_code == 201, r.text
    token: str = r.json()["token"]
    return token


def test_the_owner_can_dispatch_to_their_client(
    client: TestClient, login: LoginFn, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    ann = login("ann")
    new_client(client)
    t = create_ticket(owner_id=ann.id)
    run = runs.queue(t.key, target="client")
    assert (run.target, run.client_online, run.launch_url) == ("client", False, None)
    assert route(runs, run.id) == ("pull", None, "live")
    latest = TicketDetail.model_validate(client.get(f"/api/tickets/{t.key}").json()).latest_run
    assert latest is not None
    assert (latest.target, latest.client_online) == ("client", False)


def test_only_the_owner_can_dispatch_to_a_client(
    client: TestClient,
    login: LoginFn,
    create_ticket: CreateTicket,
    runs: RunDriver,
    ctx: AppContext,
) -> None:
    ann = login("ann")
    new_client(client)
    owned = create_ticket(owner_id=ann.id)
    unowned = create_ticket()
    assert error(runs.dispatch(unowned.key, target="client")) == (403, "forbidden")
    login("bob")
    new_client(client)
    assert error(runs.dispatch(owned.key, target="client")) == (403, "forbidden")
    assert run_count(ctx) == 0


def test_dispatching_to_a_client_needs_a_client_token(
    client: TestClient,
    login: LoginFn,
    create_ticket: CreateTicket,
    runs: RunDriver,
    ctx: AppContext,
) -> None:
    ann = login("ann")
    t = create_ticket(owner_id=ann.id)
    assert error(runs.dispatch(t.key, target="client")) == (422, "validation_error")
    assert run_count(ctx) == 0


@pytest.mark.parametrize("target", [None, "server"])
def test_the_server_target_stores_the_servers_route(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, target: str | None
) -> None:
    runs.ctx.route = Route("push", "github", "file")
    run = runs.queue(create_ticket().key, target=target)
    assert (run.target, run.client_online, run.log_delivery) == ("server", None, "at_end")
    assert route(runs, run.id) == ("push", "github", "file")


def test_the_server_target_needs_a_route(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, ctx: AppContext
) -> None:
    runs.ctx.route = None
    r = runs.dispatch(create_ticket().key)
    assert error(r) == (422, "validation_error")
    assert [i["loc"] for i in r.json()["error"]["issues"]] == [["target"]]
    assert run_count(ctx) == 0


def test_a_waiting_client_run_blocks_another_dispatch(
    client: TestClient, login: LoginFn, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    ann = login("ann")
    new_client(client)
    t = create_ticket(owner_id=ann.id)
    runs.queue(t.key, target="client")
    assert error(runs.dispatch(t.key)) == (409, "run_active")
    assert error(runs.dispatch(t.key, target="client")) == (409, "run_active")


def test_a_waiting_client_run_keeps_what_its_owner_dispatched(
    client: TestClient, login: LoginFn, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    bob = login("bob")
    ann = login("ann")
    token = new_client(client)
    t = create_ticket(owner_id=ann.id)
    run = runs.queue(t.key, target="client")
    login("bob")  # another writer

    def put(path: str = "notes.md") -> Any:
        body = {"content": "theirs", "base_version": 0}
        return client.put(f"/api/tickets/{t.key}/files/{path}", json=body, headers=CSRF)

    def patch(body: dict[str, Any]) -> Any:
        return client.patch(f"/api/tickets/{t.key}", json=body, headers=CSRF)

    assert error(put()) == (409, "bundle_locked")
    assert error(patch({"owner_id": bob.id})) == (409, "run_active")
    assert error(patch({"description": "theirs"})) == (409, "run_active")
    assert patch({"priority": "high"}).status_code == 200  # nothing a run renders

    taken = client.post(
        "/api/worker/lease",
        json={"instance": INSTANCE, "seq": next(LEASE_SEQ), "wait_s": 0},
        headers=bearer(token),
    )
    assert taken.json()["job"]["run_id"] == run.id  # dispatched, not bound yet
    assert error(put()) == (409, "bundle_locked")
    assert error(patch({"owner_id": bob.id})) == (409, "run_active")

    assert client.post(f"/api/runs/{run.id}/cancel", headers=CSRF).status_code == 200
    assert put().status_code == 200
    assert patch({"owner_id": bob.id}).status_code == 200


def test_a_client_run_whose_package_cannot_be_built_fails_at_the_take(
    client: TestClient,
    login: LoginFn,
    create_ticket: CreateTicket,
    runs: RunDriver,
    ctx: AppContext,
) -> None:
    ann = login("ann")
    token = new_client(client)
    t = create_ticket(owner_id=ann.id)
    run = runs.queue(t.key, target="client")
    with ctx.db.tx() as conn:  # as if the catalog lost the repo while the run waited
        conn.execute("UPDATE tickets SET repo = 'gone' WHERE key = ?", (t.key,))

    taken = client.post(
        "/api/worker/lease",
        json={"instance": INSTANCE, "seq": next(LEASE_SEQ), "wait_s": 0},
        headers=bearer(token),
    )
    assert taken.json()["job"] is None
    assert runs.get(run.id).status == "failed"


# ------------------------------------------------------------------ reads


def test_list_runs_newest_first(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    t = create_ticket()
    first = runs.queue(t.key)
    authed.post(f"/api/runs/{first.id}/cancel", headers=CSRF)
    second = runs.queue(t.key)
    listed = [RunSummary.model_validate(r) for r in authed.get(f"/api/tickets/{t.key}/runs").json()]
    assert [r.id for r in listed] == [second.id, first.id]
    assert authed.get("/api/tickets/AOS-999/runs").status_code == 404


def test_get_run_404(authed: TestClient) -> None:
    for path in ("", "/manifest", "/events"):
        assert error(authed.get(f"/api/runs/999{path}")) == (404, "run_not_found")


def test_the_package_is_built_at_dispatch(
    authed: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    ctx: AppContext,
    provider: FakeProvider,
) -> None:
    t = create_ticket()
    run = runs.queue(t.key)
    assert error(authed.get(f"/api/runs/{run.id}/manifest")) == (404, "not_found")

    before = utcnow()
    runs.tick()
    package = JobPackage.model_validate(authed.get(f"/api/runs/{run.id}/manifest").json())
    job = package.job
    assert job.id.startswith(f"{run.id}-1-")
    assert len(job.id.rsplit("-", 1)[1]) == 16
    assert (job.run_id, job.segment, job.segments, job.budget_s) == (run.id, 1, 1, 3600)
    allowance = timedelta(seconds=ctx.space.claim_timeout_s + 3600) + BUDGET_GRACE
    assert before + allowance <= job.not_after <= utcnow() + allowance
    assert package.journal.first_seq == 1
    assert package.report.model_dump() == {
        "mode": "live",
        "url": ctx.settings.effective_worker_url,
    }

    # The worker's job returns the same package.
    fetched = runs.client.get("/api/worker/job", headers=bearer(provider.token(run.id))).json()
    assert authed.get(f"/api/runs/{run.id}/manifest").json() == fetched
    assert provider.launched == [run.id]


def test_stage_names_fall_back_to_the_id(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    t = create_ticket()
    worker = runs.start(t.key)
    worker.run_stage("plan")
    worker.complete()

    def rename(p: dict[str, Any]) -> None:
        stage_of(p, "plan")["name"] = "Think"

    put_pipeline(authed, t.key, rename)
    assert runs.get(worker.run_id).stage_runs[0].name == "Think"

    def drop(p: dict[str, Any]) -> None:
        p["stages"] = [s for s in p["stages"] if s["id"] != "plan"]

    put_pipeline(authed, t.key, drop)
    assert runs.get(worker.run_id).stage_runs[0].name == "plan"


def test_event_paging(authed: TestClient, create_ticket: CreateTicket, runs: RunDriver) -> None:
    t = create_ticket()
    worker = runs.start(t.key)
    # Seq 1 is the job's `started` record, which is no event.
    lines = (log_event(i, "plan", f"line {i}") for i in range(2, 11))
    assert worker.events(*lines).json()["ack"] == 10

    def seqs(query: str) -> list[int]:
        r = authed.get(f"/api/runs/{worker.run_id}/events{query}")
        assert r.status_code == 200, r.text
        return [e["seq"] for e in r.json()]

    assert seqs("") == list(range(2, 11))
    assert seqs("?limit=3") == [8, 9, 10]
    assert seqs("?after_seq=4&limit=3") == [5, 6, 7]
    assert seqs("?before_seq=4") == [2, 3]
    assert seqs("?before_seq=8&limit=2") == [6, 7]
    assert seqs("?after_seq=10") == []
    assert authed.get(f"/api/runs/{worker.run_id}/events?limit=1001").status_code == 422
    first = authed.get(f"/api/runs/{worker.run_id}/events?limit=1").json()[0]
    assert first["type"] == "log"
    assert first["data"] == {"stream": "stdout", "text": "line 10", "truncated": False}


# ------------------------------------------------------------------ cancel


def test_cancel_queued(authed: TestClient, create_ticket: CreateTicket, runs: RunDriver) -> None:
    t = create_ticket()
    run = runs.queue(t.key)
    r = authed.post(f"/api/runs/{run.id}/cancel", headers=CSRF)
    cancelled = RunSummary.model_validate(r.json())
    assert cancelled.status == "cancelled"
    assert cancelled.finished_at is not None
    assert [s.status for s in runs.get(run.id).stage_runs] == ["cancelled"]
    assert runs.tick() == []  # nothing to launch any more


def test_cancel_running_waits_for_the_worker(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    t = create_ticket()
    worker = runs.start(t.key)
    worker.events(event(worker.next_seq(), "stage_started", "plan"))
    r = authed.post(f"/api/runs/{worker.run_id}/cancel", headers=CSRF)
    first = RunSummary.model_validate(r.json())
    assert (first.status, first.stop_reason) == ("running", "cancel")
    assert first.stop_requested_at is not None
    again = RunSummary.model_validate(
        authed.post(f"/api/runs/{worker.run_id}/cancel", headers=CSRF).json()
    )
    assert again.stop_requested_at == first.stop_requested_at  # set once
    with runs.ctx.db.read() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE run_id = ?", (worker.run_id,)).fetchone()
    assert job["stop_reason"] == "cancel"
    assert job["stop_requested_at"] is not None
    assert worker.events().json()["cancel"] is True  # the heartbeat carries it
    assert worker.complete("cancelled", failed_stage="plan").status_code == 200
    detail = runs.get(worker.run_id)
    assert detail.status == "cancelled"
    assert [s.status for s in detail.stage_runs] == ["cancelled"]
    assert detail.stage_runs[0].started_at is not None
    assert detail.stage_runs[0].finished_at is not None


@pytest.mark.parametrize(
    ("reason", "status", "error_text"),
    [
        (None, "cancelled", None),  # the worker stopped on its own
        ("cancel", "cancelled", None),
        ("budget", "failed", OVER_BUDGET),
        ("never_started", "failed", NEVER_STARTED),
    ],
)
def test_a_cancelled_result_ends_the_run_by_its_stop_reason(
    authed: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    reason: str | None,
    status: str,
    error_text: str | None,
) -> None:
    worker = runs.start(create_ticket().key)
    if reason is not None:
        runs.set_run(worker.run_id, stop_requested_at=to_db(utcnow()), stop_reason=reason)
    assert worker.events().json()["cancel"] is (reason is not None)
    assert worker.complete("cancelled").status_code == 200
    detail = runs.get(worker.run_id)
    assert (detail.status, detail.stop_reason, detail.error) == (status, reason, error_text)
    assert worker.complete("cancelled").status_code == 200  # a repeated final result


@pytest.mark.parametrize("reason", [None, "budget"])
def test_a_run_failed_otherwise_refuses_a_late_cancelled_result(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, reason: str | None
) -> None:
    """Only a failure the stop table made accepts `cancelled` as its final result."""
    worker = runs.start(create_ticket().key)
    runs.set_run(worker.run_id, stop_requested_at=to_db(utcnow()), stop_reason=reason)
    runs.set_run(worker.run_id, status="failed", error="worker lost", finished_at=to_db(utcnow()))
    assert error(worker.complete("cancelled")) == (410, "run_gone")


def test_cancel_terminal_is_unchanged(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    t = create_ticket()
    worker = runs.start(t.key)
    worker.run_stage("plan")
    worker.complete()
    r = authed.post(f"/api/runs/{worker.run_id}/cancel", headers=CSRF)
    assert r.status_code == 200
    assert r.json()["status"] == "gated"
    assert r.json()["stop_requested_at"] is None
    assert error(authed.post("/api/runs/999/cancel", headers=CSRF)) == (404, "run_not_found")


# ------------------------------------------------------------------ bundle lock


def test_bundle_locked_from_dispatch_to_complete(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    t = create_ticket()

    def put(content: str, base: int, path: str = "notes.md") -> Any:
        return authed.put(
            f"/api/tickets/{t.key}/files/{path}",
            json={"content": content, "base_version": base},
            headers=CSRF,
        )

    run = runs.queue(t.key)
    assert put("queued", 0).status_code == 200  # a queued run doesn't lock
    runs.tick()
    assert error(put("dispatched", 1)) == (409, "bundle_locked")  # its package is built
    worker = runs.bind(run.id, runs.provider.token(run.id))
    assert error(put("running", 1)) == (409, "bundle_locked")
    assert error(put("new", 0, "new.md")) == (409, "bundle_locked")
    deleted = authed.delete(f"/api/tickets/{t.key}/files/notes.md?base_version=1", headers=CSRF)
    assert error(deleted) == (409, "bundle_locked")
    assert runs.get(run.id).status == "running"
    assert worker.complete().status_code == 200
    assert put("after", 1).status_code == 200


# ------------------------------------------------------------------ the board through a run


def board(client: TestClient, key: str) -> tuple[str, str, str, str, str | None]:
    t = TicketSummary.model_validate(
        next(s for s in client.get("/api/tickets").json() if s["key"] == key)
    )
    d = TicketDetail.model_validate(client.get(f"/api/tickets/{key}").json())
    return (
        t.attention,
        t.phase,
        " ".join(p.state for p in t.phase_track),
        " ".join(r.state for r in t.rail),
        d.next_stage,
    )


def test_board_through_plan_gate_continue_succeed(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    t = create_ticket(repo=None)  # plan(gate) → implement → review (open-pr off)
    assert board(authed, t.key) == (
        "inbox",
        "inbox",
        "current pending pending skipped pending pending",
        "pending pending skipped pending skipped",
        "plan",
    )
    run = runs.queue(t.key)
    assert board(authed, t.key) == (
        "queued",
        "plan",
        "done current pending skipped pending pending",
        "current pending skipped pending skipped",
        None,
    )
    worker = runs.launch_and_bind(run.id)
    worker.events(event(worker.next_seq(), "stage_started", "plan"))
    summary = next(s for s in authed.get("/api/tickets").json() if s["key"] == t.key)
    assert summary["now"]["actor"] == "Plan"
    assert board(authed, t.key)[:4] == (
        "running",
        "plan",
        "done current pending skipped pending pending",
        "current pending skipped pending skipped",
    )
    worker.events(
        event(
            worker.next_seq(),
            "stage_finished",
            "plan",
            status="succeeded",
            exit_code=0,
            reason=None,
        )
    )
    assert worker.complete().status_code == 200
    assert runs.get(run.id).status == "gated"
    assert board(authed, t.key) == (
        "needs_you",
        "plan",
        "done current pending skipped pending pending",
        "current pending skipped pending skipped",
        "implement",
    )
    cont = runs.queue(t.key)  # Continue = dispatch with the default from_stage
    assert cont.stage_ids == ["implement", "review"]
    w2 = runs.launch_and_bind(cont.id)
    w2.run_stage("implement")
    assert board(authed, t.key)[:4] == (
        "running",
        "review",
        "done done done skipped current pending",
        "done done skipped current skipped",
    )
    w2.run_stage("review")
    assert w2.complete().status_code == 200
    assert runs.get(cont.id).status == "succeeded"
    assert board(authed, t.key) == (
        "needs_you",
        "review",
        "done done done skipped done pending",
        "done done skipped done skipped",
        None,
    )


def test_retry_after_a_failure_starts_at_the_failed_stage(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    t = create_ticket(repo=None)
    worker = runs.start(t.key, "implement")
    worker.run_stage("implement")
    worker.run_stage("review", "failed")
    assert worker.complete("failed", failed_stage="review").status_code == 200
    detail = runs.get(worker.run_id)
    assert detail.status == "failed"
    assert detail.error == "stage review failed"
    attention, phase, _, rail, nxt = board(authed, t.key)
    assert (attention, phase, rail, nxt) == (
        "blocked",
        "review",
        "pending done skipped failed skipped",
        "review",
    )
    ticket = TicketDetail.model_validate(authed.get(f"/api/tickets/{t.key}").json())
    assert (ticket.now.run_id, ticket.active_run_id) == (worker.run_id, None)
    retry = runs.queue(t.key)
    assert retry.from_stage == "review"
    assert retry.stage_ids == ["review"]
