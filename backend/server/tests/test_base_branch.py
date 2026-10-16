"""Request 22A: an optional repo default branch, a dispatch blocked while a repo ticket has no
base branch, and the lock once a run has started work from it."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentos.context import AppContext
from agentos.models import Me, RepoSettings, TicketDetail, TicketSummary
from agentos.pipeline.space import RepoSpec
from tests.run_support import FakeProvider, RunDriver, event, log_event
from tests.settings_support import set_repos

CSRF = {"X-AgentOS": "1"}
CreateTicket = Callable[..., TicketDetail]
LoginFn = Callable[..., Me]
BARE = RepoSpec(name="bare", url="https://github.com/o/bare.git", default_branch=None)


@pytest.fixture
def bare(ctx: AppContext) -> str:
    """Add a repo without a default branch to the catalog; returns its name."""
    set_repos(ctx, [*ctx.space.repos, BARE])
    return BARE.name


def code(response: Any) -> str:
    value: str = response.json()["error"]["code"]
    return value


def patch(client: TestClient, key: str, **body: Any) -> Any:
    return client.patch(f"/api/tickets/{key}", json=body, headers=CSRF)


def detail(client: TestClient, key: str) -> TicketDetail:
    response = client.get(f"/api/tickets/{key}")
    assert response.status_code == 200, response.text
    return TicketDetail.model_validate(response.json())


def run_count(ctx: AppContext, key: str) -> int:
    with ctx.db.read() as conn:
        n: int = conn.execute(
            "SELECT COUNT(*) FROM runs r JOIN tickets t ON t.id = r.ticket_id WHERE t.key = ?",
            (key,),
        ).fetchone()[0]
    return n


# ------------------------------------------------------------------ create and detail


def test_create_on_a_repo_without_a_default_branch(
    authed: TestClient, create_ticket: CreateTicket, bare: str
) -> None:
    me = Me.model_validate(authed.get("/api/me").json())
    blank = create_ticket(repo=bare)  # no 422: created blank and blocked
    assert (blank.workspace_mode, blank.base_branch, blank.branch) == ("repo", None, "agent/AOS-1")
    info = blank.base_branch_info
    assert info is not None
    assert (info.source, info.set_by, info.locked, info.locked_by_run) == (
        "none",
        None,
        False,
        None,
    )
    assert info.reason is not None
    assert blank.dispatch_block is not None
    assert blank.dispatch_block.code == "base_branch_missing"

    own = create_ticket(repo=bare, base_branch="App-1.Latest")
    assert own.base_branch == "App-1.Latest"
    assert own.base_branch_info is not None
    assert own.base_branch_info.source == "manual"
    assert own.base_branch_info.set_by is not None
    assert own.base_branch_info.set_by.id == me.id
    assert own.base_branch_info.reason is None
    assert own.dispatch_block is None


def test_repo_default_and_no_repo_tickets(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket()  # repo r, default branch main
    assert t.base_branch == "main"
    assert t.base_branch_info is not None
    assert (t.base_branch_info.source, t.base_branch_info.set_by) == ("repo_default", None)
    assert t.dispatch_block is None
    none = create_ticket(repo=None)
    assert (none.base_branch, none.base_branch_info, none.dispatch_block) == (None, None, None)


def test_the_board_lists_the_dispatch_block(
    authed: TestClient, create_ticket: CreateTicket, bare: str
) -> None:
    blank = create_ticket(repo=bare)
    fine = create_ticket()
    none = create_ticket(repo=None)
    response = authed.get("/api/tickets")
    assert response.status_code == 200, response.text
    board = {t.key: t for t in (TicketSummary.model_validate(x) for x in response.json())}
    block = board[blank.key].dispatch_block
    assert block is not None
    assert block.code == "base_branch_missing"
    assert board[fine.key].dispatch_block is None
    assert board[none.key].dispatch_block is None
    # Setting a branch lifts it.
    assert patch(authed, blank.key, base_branch="rel").status_code == 200
    assert detail(authed, blank.key).dispatch_block is None


# ------------------------------------------------------------------ dispatch


def test_dispatch_is_refused_while_blank(
    authed: TestClient, create_ticket: CreateTicket, bare: str, runs: RunDriver, ctx: AppContext
) -> None:
    t = create_ticket(repo=bare)
    for from_stage in (None, "implement"):  # Dispatch / Continue / Retry and Run from…
        response = runs.dispatch(t.key, from_stage)
        assert response.status_code == 409, response.text
        assert code(response) == "base_branch_missing"
    assert run_count(ctx, t.key) == 0
    assert patch(authed, t.key, base_branch="App-1.Latest").status_code == 200
    worker = runs.start(t.key)
    assert worker.manifest.workspace.base_branch == "App-1.Latest"


def test_a_queued_run_fails_at_dispatch_if_its_base_branch_went_away(
    authed: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    ctx: AppContext,
    provider: FakeProvider,
) -> None:
    t = create_ticket(repo="docs")  # docs: default branch develop
    run = runs.queue(t.key)
    set_repos(ctx, [r.model_copy(update={"default_branch": None}) for r in ctx.space.repos])
    assert run.id in runs.tick()
    assert provider.launched == []
    got = runs.get(run.id)
    assert got.status == "failed"
    assert got.error is not None
    assert "base branch" in got.error


# ------------------------------------------------------------------ the lock


def locked_by(ctx: AppContext, key: str) -> int | None:
    with ctx.db.read() as conn:
        value: int | None = conn.execute(
            "SELECT base_branch_locked_by_run FROM tickets WHERE key = ?", (key,)
        ).fetchone()[0]
    return value


def test_the_first_stage_started_locks_the_base_branch(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, ctx: AppContext
) -> None:
    t = create_ticket()
    worker = runs.start(t.key)
    # Claimed and setting up the workspace: not locked yet.
    assert worker.events(log_event(worker.next_seq(), None, "cloning")).status_code == 200
    assert locked_by(ctx, t.key) is None
    worker.run_stage("plan")
    assert locked_by(ctx, t.key) == worker.run_id
    info = detail(authed, t.key).base_branch_info
    assert info is not None
    assert (info.locked, info.locked_by_run) == (True, worker.run_id)
    assert worker.complete().status_code == 200
    # A later run keeps the first run's lock.
    second = runs.start(t.key)
    second.run_stage("implement")
    assert locked_by(ctx, t.key) == worker.run_id


def test_a_run_that_fails_before_any_stage_never_locks(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, ctx: AppContext
) -> None:
    t = create_ticket()
    worker = runs.start(t.key)
    assert worker.events(log_event(worker.next_seq(), None, "cannot start")).status_code == 200
    response = worker.complete("failed", error="cannot start agent/AOS-1 from origin/main")
    assert response.status_code == 200
    assert locked_by(ctx, t.key) is None
    fixed = patch(authed, t.key, base_branch="develop")
    assert fixed.status_code == 200, fixed.text  # the owner can still fix the base


def test_a_no_repo_ticket_is_never_locked(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, ctx: AppContext
) -> None:
    t = create_ticket(repo=None)
    worker = runs.start(t.key)
    worker.run_stage("plan")
    assert locked_by(ctx, t.key) is None
    assert worker.complete().status_code == 200
    assert detail(authed, t.key).base_branch_info is None


def test_a_duplicate_stage_started_after_the_lock_is_a_no_op(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, ctx: AppContext
) -> None:
    t = create_ticket()
    worker = runs.start(t.key)
    first = event(worker.next_seq(), "stage_started", "plan")
    assert worker.events(first).status_code == 200
    assert worker.events(first).status_code == 200  # a retried batch: stored once
    assert locked_by(ctx, t.key) == worker.run_id


def test_a_locked_ticket_refuses_base_branch_and_repo_changes(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    t = create_ticket(base_branch="rel")
    worker = runs.start(t.key)
    worker.run_stage("plan")
    assert worker.complete().status_code == 200
    for body in (
        {"base_branch": "other"},
        {"base_branch": None},
        {"repo": "docs"},
        {"repo": None},
        {"repo": "docs", "base_branch": "rel"},
    ):
        response = patch(authed, t.key, **body)
        assert response.status_code == 409, body
        assert code(response) == "base_branch_locked"
        assert f"run #{worker.run_id}" in response.json()["error"]["message"]
    # The same values are not a change; other fields still are.
    same = patch(authed, t.key, repo="r", base_branch="rel", title="Renamed")
    assert same.status_code == 200, same.text
    got = TicketDetail.model_validate(same.json())
    assert (got.title, got.base_branch) == ("Renamed", "rel")
    assert got.base_branch_info is not None
    assert (got.base_branch_info.source, got.base_branch_info.locked) == ("manual", True)


def test_the_lock_pins_a_repo_default_ticket_to_the_branch_it_started_from(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver, ctx: AppContext
) -> None:
    t = create_ticket()  # no own branch: the repo's default ("main")
    worker = runs.start(t.key)
    worker.run_stage("plan")
    assert worker.complete().status_code == 200
    with ctx.db.read() as conn:
        own = conn.execute("SELECT base_branch FROM tickets WHERE key = ?", (t.key,)).fetchone()
    assert own["base_branch"] == "main"  # pinned from the locking run's manifest
    info = detail(authed, t.key).base_branch_info
    assert info is not None
    assert (info.source, info.locked) == ("repo_default", True)
    assert patch(authed, t.key, base_branch="main").status_code == 200  # the same value
    response = patch(authed, t.key, base_branch=None)  # back to "follow the default": a change
    assert response.status_code == 409
    assert code(response) == "base_branch_locked"


# ------------------------------------------------------------------ PATCH provenance


def test_patch_sets_manual_and_null_resets_to_default(
    client: TestClient, create_ticket: CreateTicket, login: LoginFn
) -> None:
    sam = login("sam")
    t = create_ticket()
    set_by_sam = TicketDetail.model_validate(patch(client, t.key, base_branch="rel").json())
    info = set_by_sam.base_branch_info
    assert info is not None
    assert (info.source, info.set_by.id if info.set_by else None) == ("manual", sam.id)
    login("dev")
    again = TicketDetail.model_validate(patch(client, t.key, base_branch="rel").json())
    assert again.base_branch_info is not None
    assert again.base_branch_info.set_by is not None
    assert again.base_branch_info.set_by.id == sam.id  # the same value: still Sam's
    assert again.updated_at == set_by_sam.updated_at
    cleared = TicketDetail.model_validate(patch(client, t.key, base_branch=None).json())
    assert cleared.base_branch == "main"
    assert cleared.base_branch_info is not None
    assert (cleared.base_branch_info.source, cleared.base_branch_info.set_by) == (
        "repo_default",
        None,
    )


def test_patch_keeps_the_source_value_for_the_record(
    authed: TestClient, create_ticket: CreateTicket, ctx: AppContext
) -> None:
    t = create_ticket()
    with ctx.db.tx() as conn:  # as a 22B mapping would leave it: blank, with its reason
        conn.execute(
            "UPDATE tickets SET base_branch_source = 'mapping', base_branch_source_value = ?, "
            "base_branch_reason = ? WHERE key = ?",
            ("version_3.0", "No rule matches version_3.0", t.key),
        )
    blank = detail(authed, t.key)
    assert blank.base_branch is None  # the repo default does not fill a mapping's blank
    assert blank.base_branch_info is not None
    assert (blank.base_branch_info.source, blank.base_branch_info.reason) == (
        "none",
        "No rule matches version_3.0",
    )
    assert blank.dispatch_block is not None
    manual = TicketDetail.model_validate(patch(authed, t.key, base_branch="App-3").json())
    assert manual.base_branch_info is not None
    assert (
        manual.base_branch_info.source,
        manual.base_branch_info.source_value,
        manual.base_branch_info.reason,
    ) == ("manual", "version_3.0", None)


# ------------------------------------------------------------------ settings


def test_settings_repos_take_an_empty_default_branch(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    repos = RepoSettings.model_validate(authed.get("/api/settings/repos").json())
    entries = [
        {"name": "r", "url": "https://github.com/o/r.git", "default_branch": ""},
        {"name": "docs", "url": "git@github.com:o/docs.git", "default_branch": None},
    ]
    body = {"value": {"repos": entries}, "base_version": repos.version}
    response = authed.put("/api/settings/repos", json=body, headers=CSRF)
    assert response.status_code == 200, response.text
    saved = RepoSettings.model_validate(response.json())
    assert [(r.name, r.default_branch) for r in saved.value.repos] == [("r", None), ("docs", None)]
    config = authed.get("/api/config").json()
    assert [r["default_branch"] for r in config["repos"]] == [None, None]
    t = create_ticket()
    assert (t.repo_name, t.base_branch) == ("r", None)
    assert t.dispatch_block is not None
