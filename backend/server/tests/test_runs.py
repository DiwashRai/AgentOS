"""Run routes (SPEC §8, §10): dispatch validation, reads, cancel, the bundle lock and the board
fields through a whole run lifecycle. The worker side is driven through the worker API."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentos.context import AppContext
from agentos.models import RunSummary, TicketDetail, TicketSummary
from tests.run_support import CSRF, RunDriver, event, log_event

CreateTicket = Callable[..., TicketDetail]


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


# ------------------------------------------------------------------ dispatch


@pytest.mark.parametrize(
    ("launcher_name", "launch_ref", "url"),
    [
        (
            "github",
            "https://github.com/o/r/actions/runs/9",
            "https://github.com/o/r/actions/runs/9",
        ),
        ("local", "4242", None),  # a PID
        ("github", None, None),  # not launched yet
    ],
)
def test_launch_url_links_the_github_run(
    authed: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    launcher_name: str,
    launch_ref: str | None,
    url: str | None,
) -> None:
    t = create_ticket()
    run = runs.queue(t.key)
    runs.set_run(run.id, launcher=launcher_name, launch_ref=launch_ref)
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
    assert row["launcher"] == "local"
    assert row["manifest"] is None
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
    assert worker.complete().status_code == 204
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


def test_manifest_is_404_until_claimed(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    t = create_ticket()
    run = runs.queue(t.key)
    r = authed.get(f"/api/runs/{run.id}/manifest")
    assert error(r) == (404, "not_found")
    assert "claims" in r.json()["error"]["message"]
    runs.tick()
    worker_token = "w" * 43
    claimed = runs.claim(run.id, worker_token).json()["manifest"]
    assert authed.get(f"/api/runs/{run.id}/manifest").json() == claimed


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
    assert worker.events(*(log_event(i, "plan", f"line {i}") for i in range(1, 11))).json() == {
        "cancel": False
    }

    def seqs(query: str) -> list[int]:
        r = authed.get(f"/api/runs/{worker.run_id}/events{query}")
        assert r.status_code == 200, r.text
        return [e["seq"] for e in r.json()]

    assert seqs("") == list(range(1, 11))
    assert seqs("?limit=3") == [8, 9, 10]
    assert seqs("?after_seq=4&limit=3") == [5, 6, 7]
    assert seqs("?before_seq=4") == [1, 2, 3]
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
    worker.events(event(1, "stage_started", "plan"))
    worker.seq = 1
    r = authed.post(f"/api/runs/{worker.run_id}/cancel", headers=CSRF)
    first = RunSummary.model_validate(r.json())
    assert first.status == "running"
    assert first.cancel_requested_at is not None
    again = RunSummary.model_validate(
        authed.post(f"/api/runs/{worker.run_id}/cancel", headers=CSRF).json()
    )
    assert again.cancel_requested_at == first.cancel_requested_at  # set once
    assert worker.events().json() == {"cancel": True}  # the heartbeat carries it
    assert worker.complete("cancelled", failed_stage="plan").status_code == 204
    detail = runs.get(worker.run_id)
    assert detail.status == "cancelled"
    assert [s.status for s in detail.stage_runs] == ["cancelled"]
    assert detail.stage_runs[0].started_at is not None
    assert detail.stage_runs[0].finished_at is not None


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
    assert r.json()["cancel_requested_at"] is None
    assert error(authed.post("/api/runs/999/cancel", headers=CSRF)) == (404, "run_not_found")


# ------------------------------------------------------------------ bundle lock


def test_bundle_locked_from_claim_to_complete(
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
    assert put("dispatched", 1).status_code == 200  # nor does a dispatched one
    worker_token = "k" * 43
    assert runs.claim(run.id, worker_token).status_code == 200
    assert error(put("running", 2)) == (409, "bundle_locked")
    assert error(put("new", 0, "new.md")) == (409, "bundle_locked")
    deleted = authed.delete(f"/api/tickets/{t.key}/files/notes.md?base_version=2", headers=CSRF)
    assert error(deleted) == (409, "bundle_locked")
    assert runs.get(run.id).status == "running"
    r = authed.post(
        "/api/worker/complete",
        json={"status": "completed"},
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert r.status_code == 204
    assert put("after", 2).status_code == 200


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
    worker = runs.launch_and_claim(run.id)
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
    assert worker.complete().status_code == 204
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
    w2 = runs.launch_and_claim(cont.id)
    w2.run_stage("implement")
    assert board(authed, t.key)[:4] == (
        "running",
        "review",
        "done done done skipped current pending",
        "done done skipped current skipped",
    )
    w2.run_stage("review")
    assert w2.complete().status_code == 204
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
    assert worker.complete("failed", failed_stage="review").status_code == 204
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
