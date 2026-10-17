"""Board fields through the API (SPEC §13): real tickets, runs inserted into the db.

The derivation rules themselves are table-tested in test_board.py; this checks the batch loading
(latest run, stage_runs, ever-succeeded, TASKS.md, owners, chat counts) feeds them correctly.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import get_args

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agentos.context import AppContext
from agentos.db import Conn
from agentos.models import (
    Attention,
    GateState,
    Me,
    Person,
    Phase,
    PhaseState,
    PlanItem,
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


def test_a_running_ticket(authed: TestClient, create_ticket: CreateTicket, add_run: AddRun) -> None:
    me = Me.model_validate(authed.get("/api/me").json())
    t = create_ticket()
    put_tasks(authed, t, "# Tasks\n- [x] Reproduce\n- [ ] Run tests\n- [ ] Ship\n")
    add_run(t.key, "gated", [("plan", "succeeded")], created_by=me.id)
    run_id = add_run(
        t.key, "running", [("implement", "running"), ("review", "pending")], created_by=me.id
    )
    s = board(authed)[t.key]
    assert (s.attention, s.phase) == ("running", "build")
    assert rail(s) == "done current skipped pending pending"
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
    assert rail(s) == "current stale skipped stale pending"
    assert track(s) == "done current stale skipped stale pending"
    assert detail(authed, t.key).next_stage == "implement"


def test_board_covers_every_attention_group_and_state(
    login: LoginFn, client: TestClient, create_ticket: CreateTicket, add_run: AddRun
) -> None:
    """Real tickets reach every attention group, phase and rail state the UI renders."""
    sam = login("sam")
    login("dev")
    create_ticket("Inbox")
    running = create_ticket("Running")
    add_run(running.key, "running", [("plan", "running")])
    queued = create_ticket("Queued")
    add_run(queued.key, "queued", [("plan", "pending")])
    needs = create_ticket("Needs you")
    add_run(needs.key, "gated", [("plan", "succeeded")])
    waiting = create_ticket("Waiting", owner_id=sam.id)
    add_run(
        waiting.key,
        "succeeded",
        [("plan", "succeeded"), ("implement", "succeeded"), ("review", "succeeded")],
    )
    add_run(waiting.key, "gated", [("plan", "succeeded")])
    blocked = create_ticket("Blocked")
    add_run(blocked.key, "gated", [("plan", "succeeded")])
    add_run(blocked.key, "failed", [("implement", "succeeded"), ("review", "failed")])
    done = create_ticket("Done")
    assert client.patch(f"/api/tickets/{done.key}", json={"state": "done"}, headers=CSRF).is_success
    tickets = list(board(client).values())
    assert {t.attention for t in tickets} == set(get_args(Attention))
    assert {s.state for t in tickets for s in t.phase_track} == set(get_args(PhaseState))
    assert {s.state for t in tickets for s in t.rail} == set(get_args(RailState))
    gates = {s.gate.state for t in tickets for s in t.rail if s.gate is not None}
    assert gates == set(get_args(GateState))
    assert all([p.phase for p in t.phase_track] == list(get_args(Phase)) for t in tickets)


def test_attention_since_follows_the_latest_run(
    authed: TestClient, create_ticket: CreateTicket, add_run: AddRun
) -> None:
    t = create_ticket()
    assert board(authed)[t.key].attention_since == t.created_at
    start = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
    add_run(t.key, "gated", [("plan", "succeeded")], start=start)
    s = board(authed)[t.key]
    assert (s.attention, s.attention_since) == ("needs_you", start + timedelta(minutes=4))
    add_run(t.key, "running", [("implement", "running")], start=start + timedelta(hours=1))
    s = board(authed)[t.key]
    assert (s.attention, s.attention_since) == ("running", start + timedelta(hours=1))


def test_a_run_row_missing_what_its_status_implies(
    authed: TestClient, create_ticket: CreateTicket, add_run: AddRun, ctx: AppContext
) -> None:
    """Never expected, but read the same way: a missing start or end falls back to the run's
    created_at, and a run without stage_runs counts its planned stages as pending."""
    start = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
    running, failed, bare = create_ticket("running"), create_ticket("failed"), create_ticket("bare")
    plan_then_implement = [("plan", "succeeded"), ("implement", "running")]
    no_start = add_run(running.key, "running", plan_then_implement, start=start)
    no_end = add_run(failed.key, "failed", [("plan", "failed")], start=start)
    add_run(bare.key, "gated", [("plan", "succeeded")])
    rest = [("implement", "cancelled"), ("review", "cancelled"), ("open-pr", "cancelled")]
    no_stage_runs = add_run(bare.key, "failed", rest)
    with ctx.db.tx() as conn:
        conn.execute("UPDATE runs SET started_at = NULL WHERE id = ?", (no_start,))
        conn.execute("UPDATE runs SET finished_at = NULL WHERE id = ?", (no_end,))
        conn.execute("DELETE FROM stage_runs WHERE run_id = ?", (no_stage_runs,))

    tickets = board(authed)
    created = start - timedelta(minutes=1)
    assert tickets[running.key].attention_since == created
    assert tickets[failed.key].attention_since == created
    assert rail(tickets[bare.key]) == "done failed skipped pending pending"


def test_a_dispatched_run_shows_no_leftover_queue_reason(
    authed: TestClient, create_ticket: CreateTicket, add_run: AddRun, ctx: AppContext
) -> None:
    """Both writers to `dispatched` clear `queue_reason`; a row that kept one still reads as a
    run handed to its runner."""
    t = create_ticket()
    run_id = add_run(t.key, "dispatched", [("plan", "pending")])
    with ctx.db.tx() as conn:
        conn.execute("UPDATE runs SET queue_reason = 'held' WHERE id = ?", (run_id,))
    assert board(authed)[t.key].now.text == "queued for a runner"


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param("UPDATE runs SET status = 'bogus'", id="unknown-status"),
        pytest.param("UPDATE runs SET stage_ids = '[\"plan\", 42]'", id="stage-id-not-text"),
        pytest.param("UPDATE runs SET stage_ids = '[]'", id="no-planned-stage"),
        pytest.param("UPDATE stage_runs SET idx = 'zero'", id="stage-run-idx-not-a-number"),
    ],
)
def test_a_corrupt_run_row_raises(
    authed: TestClient, create_ticket: CreateTicket, add_run: AddRun, ctx: AppContext, damage: str
) -> None:
    t = create_ticket()
    add_run(t.key, "failed", [("plan", "failed")])
    with ctx.db.tx() as conn:
        conn.execute(damage)
    with pytest.raises(ValidationError):
        board(authed)


def test_detail_plan_is_the_current_tasks_md(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    t = create_ticket()
    assert detail(authed, t.key).plan == []  # the scaffold's "# Tasks\n"
    put_tasks(authed, t, "# Tasks\n- [x] Reproduce\n- [ ] Run tests\n  - [X]  Nested  \n")
    d = detail(authed, t.key)
    assert d.plan == [
        PlanItem(text="Reproduce", done=True, section="Tasks"),
        PlanItem(text="Run tests", done=False, section="Tasks"),
        PlanItem(text="Nested", done=True, section="Tasks"),
    ]
    assert d.tasks == TaskProgress(done=2, total=3)
    assert "plan" not in authed.get("/api/tickets").json()[0]  # detail only
    path = f".claude/skills/{t.skill_name}/TASKS.md"
    response = authed.delete(f"/api/tickets/{t.key}/files/{path}?base_version=2", headers=CSRF)
    assert response.status_code == 204, response.text
    assert detail(authed, t.key).plan is None


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
