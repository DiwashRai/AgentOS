"""Mailbox mode (design/mailbox.md): the mailbox dispatcher, replay, the mailbox reaper's rules,
the sweep, the runs reaper's exemption and the lifespan's loops (D8)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentos.clock import from_db, to_db, utcnow
from agentos.config import Settings
from agentos.context import AppContext
from agentos.launchers.github import GitHubActionsLauncher, GitHubTarget
from agentos.launchers.mailbox import MailboxError, MailboxLauncher
from agentos.mailbox import dispatcher as mailbox_dispatcher
from agentos.mailbox import service
from agentos.mailbox.reaper import (
    CANCEL_GRACE,
    LAUNCH_GRACE,
    LAUNCH_INTERRUPTED,
    NOT_PICKED_UP,
    TOO_LARGE,
    MailboxReaper,
)
from agentos.main import create_app
from agentos.models import TicketDetail
from agentos.runs import dispatcher as runs_dispatcher
from agentos.runs.reaper import Reaper
from agentos.runs.service import BUDGET_GRACE, OVER_BUDGET
from agentos.stores.artifactory import ArtifactoryStore
from agentos.worker_api import service as worker_api
from tests.mailbox_support import FakeMailbox, Outbox
from tests.run_support import CSRF, FakeLauncher, RunDriver, log_event

CreateTicket = Callable[..., TicketDetail]
AddRun = Callable[..., int]
PR = "https://github.com/o/r/pull/7"


@pytest.fixture
def launcher() -> FakeLauncher:
    """Overrides conftest's: dispatches store `launcher = 'mailbox'` (the loops never call it)."""
    fake = FakeLauncher(max_concurrency=None)
    fake.name = "mailbox"
    return fake


@pytest.fixture
def mailbox() -> FakeMailbox:
    return FakeMailbox()


@pytest.fixture
def reaper(ctx: AppContext, mailbox: FakeMailbox) -> MailboxReaper:
    return MailboxReaper(ctx, mailbox)


@pytest.fixture
def ticket(authed: TestClient, create_ticket: CreateTicket) -> TicketDetail:
    return create_ticket("Mailbox")


def launch(runs: RunDriver, mailbox: FakeMailbox, key: str) -> int:
    """Queue a run and tick the mailbox dispatcher: the run is claimed and sent."""
    run_id = runs.queue(key).id
    assert mailbox_dispatcher.tick(runs.ctx, mailbox) == [run_id]
    return run_id


def started_at(runs: RunDriver, run_id: int) -> datetime:
    return from_db(runs.db_run(run_id)["started_at"])


def tasks_path(t: TicketDetail) -> str:
    return f".claude/skills/{t.skill_name}/TASKS.md"


def counts(ctx: AppContext, run_id: int, path: str) -> tuple[int, int]:
    """(stored events, versions of `path`)."""
    with ctx.db.read() as conn:
        events = conn.execute(
            "SELECT COUNT(*) FROM run_events WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        versions = conn.execute(
            "SELECT COUNT(*) FROM file_versions WHERE path = ?", (path,)
        ).fetchone()[0]
    return int(events), int(versions)


def full_outbox(run_id: int, t: TicketDetail) -> Outbox:
    return (
        Outbox(run_id)
        .run_stage("plan")
        .sync(
            "plan", [{"path": tasks_path(t), "content": "# Tasks\n- [x] one\n", "mode": "replace"}]
        )
        .complete(pr_url=PR)
    )


# ------------------------------------------------------------------ dispatcher


def test_dispatch_claims_sends_the_manifest_and_locks_the_bundle(
    authed: TestClient, ticket: TicketDetail, runs: RunDriver, mailbox: FakeMailbox
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    row = runs.db_run(run_id)
    assert row["status"] == "running"
    assert row["launcher"] == "mailbox"
    [(sent_id, folder, manifest)] = mailbox.sent
    assert sent_id == run_id
    assert folder == f"{run_id}-{row['dispatch_token_hash'][:16]}"
    assert manifest == row["manifest"].encode("utf-8")  # byte-identical to the stored manifest
    assert row["launch_ref"] == mailbox.ref_of(run_id)
    assert runs.get(run_id).launch_url == mailbox.ref_of(run_id)
    r = authed.put(
        f"/api/tickets/{ticket.key}/files/new.md",
        json={"content": "x", "base_version": 0},
        headers=CSRF,
    )
    assert (r.status_code, r.json()["error"]["code"]) == (409, "bundle_locked")
    assert mailbox_dispatcher.tick(runs.ctx, mailbox) == []  # nothing else queued


def test_a_manifest_build_failure_fails_the_run_and_sends_nothing(
    ticket: TicketDetail, runs: RunDriver, mailbox: FakeMailbox, ctx: AppContext
) -> None:
    run_id = runs.queue(ticket.key).id
    with ctx.db.tx() as conn:  # break a prompt behind the API's back after the dispatch
        row = conn.execute("SELECT pipeline FROM tickets WHERE key = ?", (ticket.key,)).fetchone()
        p = json.loads(row["pipeline"])
        p["stages"][0]["prompt"] = "{{ 1 / 0 }}"
        conn.execute("UPDATE tickets SET pipeline = ? WHERE key = ?", (json.dumps(p), ticket.key))
    assert mailbox_dispatcher.tick(ctx, mailbox) == [run_id]
    detail = runs.get(run_id)
    assert detail.status == "failed"
    assert detail.error is not None
    assert detail.error.startswith("cannot build the manifest")
    assert mailbox.sent == []


def test_a_send_failure_fails_the_run(
    ticket: TicketDetail, runs: RunDriver, mailbox: FakeMailbox
) -> None:
    mailbox.fail_send = MailboxError("Artifactory answered 503")
    run_id = launch(runs, mailbox, ticket.key)
    detail = runs.get(run_id)
    assert (detail.status, detail.error) == ("failed", "launch failed: Artifactory answered 503")
    assert [s.status for s in detail.stage_runs] == ["cancelled"]
    assert len(mailbox.deleted) == 1  # best-effort cleanup of the half-sent folder


def test_a_run_cancelled_during_the_send_cancels_the_github_run(
    ticket: TicketDetail, runs: RunDriver, mailbox: FakeMailbox
) -> None:
    def reaped_meanwhile(run_id: int) -> None:
        service.end(runs.ctx.db, runs.ctx.broker, run_id, "failed", "reaped meanwhile")

    mailbox.on_send = reaped_meanwhile
    run_id = launch(runs, mailbox, ticket.key)
    assert mailbox.cancelled == [mailbox.ref_of(run_id)]
    assert runs.db_run(run_id)["launch_ref"] == mailbox.ref_of(run_id)


# ------------------------------------------------------------------ replay


def test_replay_applies_events_sync_and_complete(
    authed: TestClient,
    ticket: TicketDetail,
    runs: RunDriver,
    mailbox: FakeMailbox,
    reaper: MailboxReaper,
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    folder = mailbox.folder_of(run_id)
    assert reaper.tick(utcnow()) == {}  # queued on GitHub, no outbox yet
    mailbox.set_status(run_id, "in_progress")
    mailbox.put_outbox(run_id, full_outbox(run_id, ticket).data())
    assert reaper.tick(utcnow()) == {run_id: "replayed"}
    detail = runs.get(run_id)
    assert detail.status == "gated"
    assert [s.status for s in detail.stage_runs] == ["succeeded"]
    assert counts(runs.ctx, run_id, tasks_path(ticket)) == (3, 2)
    r = authed.get(f"/api/tickets/{ticket.key}/versions/{tasks_path(ticket)}")
    assert r.json()[-1]["author_run_id"] == run_id
    assert authed.get(f"/api/tickets/{ticket.key}").json()["pr_url"] == PR
    assert folder in mailbox.deleted
    assert folder not in mailbox.folders
    assert reaper.tick(utcnow()) == {}  # terminal: no longer watched


def test_replay_is_idempotent(
    ticket: TicketDetail,
    runs: RunDriver,
    mailbox: FakeMailbox,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    outbox = tmp_path / "outbox.jsonl"
    outbox.write_bytes(full_outbox(run_id, ticket).data())
    real_complete = worker_api.complete

    def crash(*args: Any) -> None:
        raise RuntimeError("the server died mid-replay")

    monkeypatch.setattr(worker_api, "complete", crash)
    with pytest.raises(RuntimeError):
        service.replay(runs.ctx.db, runs.ctx.broker, run_id, outbox)
    before = counts(runs.ctx, run_id, tasks_path(ticket))
    assert before == (3, 2)
    monkeypatch.setattr(worker_api, "complete", real_complete)
    assert service.replay(runs.ctx.db, runs.ctx.broker, run_id, outbox) == "completed"
    assert counts(runs.ctx, run_id, tasks_path(ticket)) == before
    assert service.replay(runs.ctx.db, runs.ctx.broker, run_id, outbox) == "gone"
    assert counts(runs.ctx, run_id, tasks_path(ticket)) == before
    assert runs.get(run_id).status == "gated"


@pytest.mark.parametrize(
    ("first_line", "error"),
    [
        ('{"outbox": 1, "run_id": 4242}', "the results belong to run 4242"),
        ("not json", "the run's results are not an AgentOS outbox"),
        ('{"outbox": 2, "run_id": RUN}', "the run's results are not an AgentOS outbox"),
    ],
)
def test_a_bad_header_fails_the_run(
    ticket: TicketDetail,
    runs: RunDriver,
    mailbox: FakeMailbox,
    reaper: MailboxReaper,
    first_line: str,
    error: str,
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    outbox = full_outbox(run_id, ticket)
    outbox.lines[0] = first_line.replace("RUN", str(run_id))
    mailbox.put_outbox(run_id, outbox.data())
    assert reaper.tick(utcnow()) == {run_id: "ended"}
    detail = runs.get(run_id)
    assert (detail.status, detail.error) == ("failed", error)
    assert counts(runs.ctx, run_id, tasks_path(ticket))[0] == 0


def test_a_truncated_last_line_is_ignored(
    ticket: TicketDetail, runs: RunDriver, mailbox: FakeMailbox, reaper: MailboxReaper
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    outbox = Outbox(run_id).run_stage("plan")
    mailbox.put_outbox(run_id, outbox.data(tail='{"path": "/api/worker/complete", "bo'))
    assert reaper.tick(utcnow()) == {run_id: "ended"}
    detail = runs.get(run_id)
    assert (detail.status, detail.error) == ("failed", service.NO_COMPLETE)
    assert [s.status for s in detail.stage_runs] == ["succeeded"]  # the events still landed
    assert counts(runs.ctx, run_id, tasks_path(ticket))[0] == 3


def test_rejected_and_unknown_lines_are_skipped(
    ticket: TicketDetail, runs: RunDriver, mailbox: FakeMailbox, reaper: MailboxReaper
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    outbox = (
        Outbox(run_id)
        .sync("plan", [{"path": "../escape.md", "content": "x", "mode": "replace"}])  # 422
        .call("events", {"events": "nope"})  # invalid body
        .call("claim", {"worker_token": "x"})  # not a recorded call
        .raw("[1, 2]")
        .run_stage("plan")
        .complete()
    )
    mailbox.put_outbox(run_id, outbox.data())
    assert reaper.tick(utcnow()) == {run_id: "replayed"}
    assert runs.get(run_id).status == "gated"


def test_an_over_long_line_is_skipped(
    ticket: TicketDetail,
    runs: RunDriver,
    mailbox: FakeMailbox,
    reaper: MailboxReaper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "MAX_LINE_BYTES", 2000)
    run_id = launch(runs, mailbox, ticket.key)
    outbox = Outbox(run_id).events(log_event(100, "plan", "x" * 5000)).run_stage("plan").complete()
    mailbox.put_outbox(run_id, outbox.data())
    assert reaper.tick(utcnow()) == {run_id: "replayed"}
    assert counts(runs.ctx, run_id, tasks_path(ticket))[0] == 3  # not the long one


@pytest.mark.parametrize("cancel", [False, True])
def test_no_complete_fails_or_cancels(
    authed: TestClient,
    ticket: TicketDetail,
    runs: RunDriver,
    mailbox: FakeMailbox,
    reaper: MailboxReaper,
    cancel: bool,
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    if cancel:
        assert authed.post(f"/api/runs/{run_id}/cancel", headers=CSRF).status_code == 200
        assert reaper.tick(utcnow()) == {}
        assert mailbox.cancelled == [mailbox.ref_of(run_id)]
    mailbox.put_outbox(run_id, Outbox(run_id).run_stage("plan").data())
    assert reaper.tick(utcnow()) == {run_id: "ended"}
    detail = runs.get(run_id)
    if cancel:
        assert (detail.status, detail.error) == ("cancelled", None)
        assert mailbox.cancelled == [mailbox.ref_of(run_id)]  # once per run
    else:
        assert (detail.status, detail.error) == ("failed", service.NO_COMPLETE)


def test_an_error_in_one_run_does_not_stop_the_others(
    authed: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    mailbox: FakeMailbox,
    reaper: MailboxReaper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one, two, three = (create_ticket(t) for t in ("one", "two", "three"))
    first, second, third = (launch(runs, mailbox, t.key) for t in (one, two, three))
    for run_id, t in ((first, one), (second, two)):
        mailbox.put_outbox(run_id, full_outbox(run_id, t).data())
    mailbox.statuses[mailbox.ref_of(third)] = RuntimeError("a bug")  # not a MailboxError
    real = worker_api.post_events
    calls = {"n": 0}

    def post_events_failing_once(*args: Any) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(*args)

    monkeypatch.setattr(worker_api, "post_events", post_events_failing_once)
    assert reaper.tick(utcnow()) == {first: "ended", second: "replayed"}
    detail = runs.get(first)
    assert (detail.status, detail.error) == ("failed", "could not replay the results: RuntimeError")
    assert mailbox.folder_of(first) in mailbox.deleted
    assert runs.get(second).status == "gated"
    assert runs.get(third).status == "running"  # its check failed; logged, retried next tick


# ------------------------------------------------------------------ reaper rules


@pytest.mark.parametrize("cancel", [False, True])
def test_github_completed_without_an_outbox(
    authed: TestClient,
    ticket: TicketDetail,
    runs: RunDriver,
    mailbox: FakeMailbox,
    reaper: MailboxReaper,
    cancel: bool,
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    if cancel:
        authed.post(f"/api/runs/{run_id}/cancel", headers=CSRF)
    mailbox.set_status(run_id, "completed", "failure")
    assert reaper.tick(utcnow()) == {run_id: "ended"}
    detail = runs.get(run_id)
    if cancel:
        assert (detail.status, detail.error) == ("cancelled", None)
    else:
        assert detail.status == "failed"
        assert detail.error == (
            "GitHub job ended (failure) without sending results; see the GitHub run"
        )
    assert mailbox.folder_of(run_id) in mailbox.deleted


def test_a_store_error_skips_the_outbox_rules(
    ticket: TicketDetail, runs: RunDriver, mailbox: FakeMailbox, reaper: MailboxReaper
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    mailbox.set_status(run_id, "completed", "success")
    mailbox.fail_fetch = MailboxError("Artifactory unreachable")
    assert reaper.tick(utcnow()) == {}  # the results may exist: don't fail the run
    assert runs.get(run_id).status == "running"
    mailbox.fail_fetch = None
    mailbox.put_outbox(run_id, full_outbox(run_id, ticket).data())
    assert reaper.tick(utcnow()) == {run_id: "replayed"}


def test_an_outbox_too_large_fails_the_run(
    ticket: TicketDetail, runs: RunDriver, mailbox: FakeMailbox, reaper: MailboxReaper
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    mailbox.put_outbox(run_id, b"TOO LARGE")
    assert reaper.tick(utcnow()) == {run_id: "ended"}
    assert runs.get(run_id).error == TOO_LARGE


def test_queued_too_long(
    ticket: TicketDetail, runs: RunDriver, mailbox: FakeMailbox, reaper: MailboxReaper
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    deadline = started_at(runs, run_id) + timedelta(seconds=runs.ctx.space.claim_timeout_s)
    assert reaper.tick(deadline - timedelta(seconds=1)) == {}
    assert mailbox.cancelled == []
    assert reaper.tick(deadline + timedelta(seconds=1)) == {run_id: "ended"}
    assert (runs.get(run_id).status, runs.get(run_id).error) == ("failed", NOT_PICKED_UP)
    assert mailbox.cancelled == [mailbox.ref_of(run_id)]


def test_a_started_job_is_not_failed_by_the_queue_rule(
    ticket: TicketDetail, runs: RunDriver, mailbox: FakeMailbox, reaper: MailboxReaper
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    mailbox.set_status(run_id, "in_progress")
    later = started_at(runs, run_id) + timedelta(seconds=runs.ctx.space.claim_timeout_s + 60)
    assert reaper.tick(later) == {}


def test_over_budget(
    ticket: TicketDetail, runs: RunDriver, mailbox: FakeMailbox, reaper: MailboxReaper
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    mailbox.set_status(run_id, "in_progress")
    row = runs.db_run(run_id)
    budget = timedelta(seconds=row["time_budget_s"] + runs.ctx.space.claim_timeout_s) + BUDGET_GRACE
    deadline = started_at(runs, run_id) + budget
    assert reaper.tick(deadline - timedelta(seconds=1)) == {}
    assert reaper.tick(deadline + timedelta(seconds=1)) == {run_id: "ended"}
    assert (runs.get(run_id).status, runs.get(run_id).error) == ("failed", OVER_BUDGET)
    assert mailbox.cancelled == [mailbox.ref_of(run_id)]


def test_cancel_grace_applies_only_while_the_status_is_unknown(
    authed: TestClient,
    ticket: TicketDetail,
    runs: RunDriver,
    mailbox: FakeMailbox,
    reaper: MailboxReaper,
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    mailbox.set_status(run_id, "in_progress")
    authed.post(f"/api/runs/{run_id}/cancel", headers=CSRF)
    requested = from_db(runs.db_run(run_id)["cancel_requested_at"])
    late = requested + CANCEL_GRACE + timedelta(seconds=1)
    assert reaper.tick(late) == {}  # GitHub answers: wait for it to complete
    mailbox.statuses[mailbox.ref_of(run_id)] = MailboxError("GitHub unreachable")
    assert reaper.tick(late - timedelta(seconds=2)) == {}
    assert reaper.tick(late) == {run_id: "ended"}
    assert runs.get(run_id).status == "cancelled"
    assert mailbox.cancelled == [mailbox.ref_of(run_id)]  # once per run per process


def test_a_launch_without_a_ref_is_failed_after_the_launch_grace(
    ticket: TicketDetail, runs: RunDriver, mailbox: FakeMailbox, reaper: MailboxReaper
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    runs.set_run(run_id, launch_ref=None)  # the server died between the claim and the ref
    deadline = started_at(runs, run_id) + LAUNCH_GRACE
    assert reaper.tick(deadline - timedelta(seconds=1)) == {}
    assert reaper.tick(deadline + timedelta(seconds=1)) == {run_id: "ended"}
    assert (runs.get(run_id).status, runs.get(run_id).error) == ("failed", LAUNCH_INTERRUPTED)


def test_the_sweep_keeps_active_runs_folders(
    create_ticket: CreateTicket,
    authed: TestClient,
    runs: RunDriver,
    mailbox: FakeMailbox,
    reaper: MailboxReaper,
) -> None:
    run_id = launch(runs, mailbox, create_ticket("active").key)
    active = mailbox.folder_of(run_id)
    same_id = f"{run_id}-{'f' * 16}"  # another server's run with our active id: never touched
    stale, other = "999-0123456789abcdef", "notes"
    for name in (same_id, stale, other):
        mailbox.folders[name] = {"outbox.jsonl": b""}
    now = utcnow()
    reaper.tick(now)  # the first tick sweeps
    assert sorted(mailbox.folders) == sorted([active, same_id, other])
    mailbox.folders[stale] = {}
    reaper.tick(now + timedelta(minutes=30))
    assert stale in mailbox.folders  # hourly
    reaper.tick(now + timedelta(hours=1))
    assert stale not in mailbox.folders


# ------------------------------------------------------------------ the runs reaper and loops


def test_the_runs_reaper_leaves_mailbox_runs_alone(
    authed: TestClient,
    ticket: TicketDetail,
    create_ticket: CreateTicket,
    add_run: AddRun,
    runs: RunDriver,
    mailbox: FakeMailbox,
) -> None:
    run_id = launch(runs, mailbox, ticket.key)
    authed.post(f"/api/runs/{run_id}/cancel", headers=CSRF)
    reaper = Reaper(runs.ctx)
    now = utcnow() + timedelta(hours=1)
    reaper.tick(now - timedelta(seconds=15))  # the grace tick (refreshes last_seen_at)
    old = to_db(now - timedelta(hours=1))
    runs.set_run(run_id, last_seen_at=old, cancel_requested_at=old, time_budget_s=1)
    local = add_run(create_ticket("local").key, "running", [("plan", "running")])  # silent
    # No heartbeat, budget or cancel rule for mailbox runs; other launchers' runs still reaped.
    assert [(r.run_id, r.reason) for r in reaper.tick(now)] == [(local, "worker lost")]
    assert runs.db_run(run_id)["status"] == "running"


def test_the_lifespan_runs_the_mailbox_loops_instead_of_the_runs_dispatcher(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, int] = {"runs": 0, "dispatcher": 0, "reaper": 0}
    seen = {name: threading.Event() for name in ("dispatcher", "reaper")}

    def record(name: str) -> Callable[..., Any]:
        def tick(*args: Any) -> Any:
            calls[name] += 1
            if name in seen:
                seen[name].set()
            return {}

        return tick

    monkeypatch.setattr(runs_dispatcher, "tick", record("runs"))
    monkeypatch.setattr(mailbox_dispatcher, "tick", record("dispatcher"))
    monkeypatch.setattr(MailboxReaper, "tick", record("reaper"))
    github = GitHubActionsLauncher(
        target=lambda: GitHubTarget(repo="o/r", workflow="agentos-mailbox.yml", ref="main"),
        token="t0ken",  # noqa: S106 - a fake; no request is made
    )
    store = ArtifactoryStore(
        base_url="https://a.example/artifactory/r",
        user="u",
        password="p",  # noqa: S106 - a fake; no request is made
    )
    launcher = MailboxLauncher(github=github, store=store, worker_path=Path("worker.py"))
    with TestClient(create_app(settings, launcher=launcher)):
        assert seen["dispatcher"].wait(10)
        assert seen["reaper"].wait(10)
    assert calls["runs"] == 0
