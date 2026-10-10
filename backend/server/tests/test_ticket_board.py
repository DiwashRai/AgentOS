"""Board fields through the API (SPEC §13): real tickets, runs inserted into the db.

The derivation rules themselves are table-tested in test_board.py; this checks the batch loading
(latest run, stage_runs, ever-succeeded, TASKS.md, owners, chat counts) feeds them correctly.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import get_args

import pytest
from fastapi.testclient import TestClient

from agentos.context import AppContext
from agentos.db import Conn
from agentos.models import (
    Attention,
    Me,
    Person,
    Phase,
    PhaseState,
    RailState,
    TaskProgress,
    TicketDetail,
    TicketSummary,
)

CSRF = {"X-AgentOS": "1"}
CreateTicket = Callable[..., TicketDetail]
AddRun = Callable[..., int]
LoginFn = Callable[..., Me]


def board(client: TestClient) -> dict[str, TicketSummary]:
    response = client.get("/api/tickets?include_closed=true")
    assert response.status_code == 200, response.text
    return {t["key"]: TicketSummary.model_validate(t) for t in response.json()}


def detail(client: TestClient, key: str) -> TicketDetail:
    response = client.get(f"/api/tickets/{key}")
    assert response.status_code == 200, response.text
    return TicketDetail.model_validate(response.json())


def rail(t: TicketSummary) -> str:
    return " ".join(r.state for r in t.rail)


def track(t: TicketSummary) -> str:
    return " ".join(p.state for p in t.phase_track)


def put_tasks(client: TestClient, t: TicketDetail, content: str) -> None:
    path = f".claude/skills/{t.skill_name}/TASKS.md"
    body = {"content": content, "mode": "replace", "base_version": 1}
    response = client.put(f"/api/tickets/{t.key}/files/{path}", json=body, headers=CSRF)
    assert response.status_code == 200, response.text


def test_a_fresh_ticket(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket()
    s = board(authed)[t.key]
    assert (s.attention, s.phase, s.phase_since) == ("up_next", "inbox", t.created_at)
    assert track(s) == "current pending pending skipped pending pending"
    assert rail(s) == "pending pending pending pending"
    assert (s.now.text, s.now.actor, s.now.person, s.now.run_id) == (
        "add context, then dispatch",
        None,
        None,
        None,
    )
    assert (s.latest_run, s.tasks, s.chat_count) == (None, None, 0)
    assert t.next_stage == "plan"


def test_a_running_ticket(authed: TestClient, create_ticket: CreateTicket, add_run: AddRun) -> None:
    me = Me.model_validate(authed.get("/api/me").json())
    t = create_ticket()
    put_tasks(authed, t, "# Tasks\n- [x] Reproduce\n- [ ] Run tests\n- [ ] Ship\n")
    add_run(t.key, "gated", [("plan", "succeeded")], created_by=me.id)
    run_id = add_run(
        t.key, "running", [("implement", "running"), ("review", "pending")], created_by=me.id
    )
    s = board(authed)[t.key]
    assert (s.attention, s.phase) == ("moving", "build")
    assert rail(s) == "done current pending pending"
    assert s.now.actor == "Implement"
    assert s.now.text == "Run tests"
    assert s.now.run_id == run_id
    assert s.now.progress == TaskProgress(done=1, total=3)
    assert s.tasks == TaskProgress(done=1, total=3)
    assert s.latest_run is not None
    assert (s.latest_run.id, s.latest_run.status, s.latest_run.stage_ids) == (
        run_id,
        "running",
        ["implement", "review"],
    )
    assert s.latest_run.created_by is not None
    assert s.latest_run.created_by.id == me.id
    d = detail(authed, t.key)
    assert (d.active_run_id, d.next_stage) == (run_id, None)
    assert TicketSummary.model_validate(d.model_dump(include=set(TicketSummary.model_fields))) == s


def test_gated_attention_depends_on_the_viewer(
    login: LoginFn, client: TestClient, create_ticket: CreateTicket, add_run: AddRun
) -> None:
    sam = login("sam")
    dev = login("dev")
    mine = create_ticket("Mine", owner_id=dev.id)
    sams = create_ticket("Sam's", owner_id=sam.id)
    nobody = create_ticket("Nobody's")
    for t in (mine, sams, nobody):
        add_run(t.key, "gated", [("plan", "succeeded")])
    as_dev = board(client)
    assert {k: t.attention for k, t in as_dev.items()} == {
        mine.key: "needs_you",
        sams.key: "waiting",
        nobody.key: "needs_you",
    }
    assert as_dev[sams.key].now.person == Person(id=sam.id, display_name="sam", avatar_url=None)
    assert as_dev[sams.key].now.text == "review plan and continue"
    assert as_dev[mine.key].now.person is None
    login("sam")
    as_sam = board(client)
    assert as_sam[mine.key].attention == "waiting"
    assert as_sam[sams.key].attention == "needs_you"
    # Clearing the owner makes it anyone's to act on.
    login("dev")
    response = client.patch(f"/api/tickets/{sams.key}", json={"owner_id": None}, headers=CSRF)
    assert response.json()["attention"] == "needs_you"


def test_ever_succeeded_spans_runs(
    authed: TestClient, create_ticket: CreateTicket, add_run: AddRun
) -> None:
    t = create_ticket()
    add_run(
        t.key,
        "succeeded",
        [("plan", "succeeded"), ("implement", "succeeded"), ("review", "succeeded")],
    )
    add_run(t.key, "gated", [("plan", "succeeded")])
    s = board(authed)[t.key]
    assert rail(s) == "current stale stale pending"
    assert track(s) == "done current stale skipped stale pending"
    assert detail(authed, t.key).next_stage == "implement"


def test_a_failed_run(authed: TestClient, create_ticket: CreateTicket, add_run: AddRun) -> None:
    t = create_ticket()
    add_run(t.key, "gated", [("plan", "succeeded")])
    run_id = add_run(t.key, "failed", [("implement", "succeeded"), ("review", "failed")])
    s = board(authed)[t.key]
    assert (s.attention, s.phase) == ("stuck", "review")
    assert rail(s) == "done done failed pending"
    assert (s.now.text, s.now.run_id) == ("Review failed: retry or adjust", run_id)
    d = detail(authed, t.key)
    assert (d.next_stage, d.active_run_id) == ("review", None)


def test_board_covers_every_attention_group_and_state(
    login: LoginFn, client: TestClient, create_ticket: CreateTicket, add_run: AddRun
) -> None:
    """Moved from test_contract's stub-board check: real tickets reach every value the UI
    renders."""
    sam = login("sam")
    login("dev")
    create_ticket("Up next")
    moving = create_ticket("Moving")
    add_run(moving.key, "running", [("plan", "running")])
    needs = create_ticket("Needs you")
    add_run(needs.key, "gated", [("plan", "succeeded")])
    waiting = create_ticket("Waiting", owner_id=sam.id)
    add_run(
        waiting.key,
        "succeeded",
        [("plan", "succeeded"), ("implement", "succeeded"), ("review", "succeeded")],
    )
    add_run(waiting.key, "gated", [("plan", "succeeded")])
    stuck = create_ticket("Stuck")
    add_run(stuck.key, "gated", [("plan", "succeeded")])
    add_run(stuck.key, "failed", [("implement", "succeeded"), ("review", "failed")])
    done = create_ticket("Done")
    assert client.patch(f"/api/tickets/{done.key}", json={"state": "done"}, headers=CSRF).is_success
    tickets = list(board(client).values())
    assert {t.attention for t in tickets} == set(get_args(Attention))
    assert {s.state for t in tickets for s in t.phase_track} == set(get_args(PhaseState))
    assert {s.state for t in tickets for s in t.rail} == set(get_args(RailState))
    assert all([p.phase for p in t.phase_track] == list(get_args(Phase)) for t in tickets)


def test_the_board_is_batch_loaded(
    authed: TestClient,
    ctx: AppContext,
    create_ticket: CreateTicket,
    add_run: AddRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fixed number of statements for the whole board, whatever the number of tickets."""
    statements: list[str] = []
    connect = ctx.db.connect

    def traced() -> Conn:
        conn = connect()
        conn.set_trace_callback(statements.append)
        return conn

    def count() -> int:
        statements.clear()
        assert authed.get("/api/tickets").status_code == 200
        return len(statements)

    def add(n: int) -> None:
        for _ in range(n):
            t = create_ticket()
            add_run(t.key, "running", [("plan", "running")])
            authed.post(f"/api/tickets/{t.key}/chat", json={"body": "hi"}, headers=CSRF)

    add(1)
    monkeypatch.setattr(ctx.db, "connect", traced)
    one = count()
    monkeypatch.undo()
    add(5)
    monkeypatch.setattr(ctx.db, "connect", traced)
    assert count() == one
