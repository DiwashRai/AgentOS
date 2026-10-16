"""Worker API (SPEC §8 claim table, §10): claim, events, sync, complete; idempotency and 410s."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentos.context import AppContext
from agentos.models import FileVersionInfo, JobPackage, TicketDetail
from agentos.worker_api import service as worker_service
from tests.run_support import CSRF, RunDriver, Worker, bearer, event, finished, log_event

CreateTicket = Callable[..., TicketDetail]
REPO = "https://github.com/o/r.git"


def error(r: Any) -> tuple[int, str]:
    return r.status_code, r.json()["error"]["code"]


@pytest.fixture
def ticket(authed: TestClient, create_ticket: CreateTicket) -> TicketDetail:
    return create_ticket("Worker API")


def skill_dir(t: TicketDetail) -> str:
    return f".claude/skills/{t.skill_name}"


# ------------------------------------------------------------------ claim


def test_claim_returns_the_manifest_and_starts_the_run(
    ticket: TicketDetail, runs: RunDriver
) -> None:
    run = runs.queue(ticket.key)
    runs.tick()
    r = runs.claim(run.id, "a" * 43)
    assert r.status_code == 200, r.text
    m = JobPackage.model_validate(r.json()["manifest"])
    assert (m.protocol, m.run_id) == (2, run.id)
    assert m.ticket.key == ticket.key
    assert m.workspace.mode == "repo"
    assert m.workspace.repo_url == REPO
    assert m.workspace.base_branch == "main"
    assert m.workspace.branch == f"agent/{ticket.key}"
    assert (m.workspace.protect, m.workspace.blocks) == ([skill_dir(ticket)], [ticket.key])
    assert [s.id for s in m.stages] == ["plan"]
    assert m.stages[0].argv == ["python", "fake_agent.py"]  # the test runtime
    assert m.stages[0].stdin is not None
    assert ticket.key in m.stages[0].stdin
    d = skill_dir(ticket)
    assert [(e.path, e.kind, e.access) for e in m.context] == [
        (f"{d}/SKILL.md", "skill", "write"),
        (f"{d}/TASKS.md", "plan", "write"),
        ("AGENTS.md", "instructions", "write"),
        ("CLAUDE.md", "instructions", "write"),
    ]
    assert [n.path for n in m.notes] == [f"{d}/learnings.log", f"{d}/swarm.log"]
    row = runs.db_run(run.id)
    assert row["status"] == "running"
    assert row["started_at"] is not None
    assert row["last_seen_at"] == row["started_at"]
    assert json.loads(row["manifest"]) == r.json()["manifest"]
    assert row["worker_token_hash"] != "a" * 43  # hashed


def test_claim_retry_with_the_same_token_and_a_different_one(
    ticket: TicketDetail, runs: RunDriver
) -> None:
    run = runs.queue(ticket.key)
    runs.tick()
    first = runs.claim(run.id, "a" * 43)
    again = runs.claim(run.id, "a" * 43)
    assert again.status_code == 200
    assert again.json() == first.json()
    assert error(runs.claim(run.id, "b" * 43)) == (409, "already_claimed")


def test_claim_after_cancel_is_gone(ticket: TicketDetail, runs: RunDriver) -> None:
    run = runs.queue(ticket.key)
    runs.tick()
    runs.client.post(f"/api/runs/{run.id}/cancel", headers=CSRF)
    assert error(runs.claim(run.id)) == (410, "run_gone")


def test_claim_needs_a_known_dispatch_token(ticket: TicketDetail, runs: RunDriver) -> None:
    run = runs.queue(ticket.key)
    runs.tick()
    body = {"worker_token": "a" * 43}
    r = runs.client.post("/api/worker/claim", json=body, headers=bearer("nope"))
    assert error(r) == (401, "unauthorized")
    assert error(runs.client.post("/api/worker/claim", json=body)) == (401, "unauthorized")
    r = runs.client.post("/api/worker/claim", json=body, headers={"Authorization": "Basic x"})
    assert error(r) == (401, "unauthorized")
    assert runs.db_run(run.id)["status"] == "dispatched"


def test_manifest_is_the_bundle_at_claim_time(
    authed: TestClient, ticket: TicketDetail, runs: RunDriver
) -> None:
    def put(path: str, content: str) -> None:
        r = authed.put(
            f"/api/tickets/{ticket.key}/files/{path}",
            json={"content": content, "base_version": 0},
            headers=CSRF,
        )
        assert r.status_code == 200, r.text

    run = runs.queue(ticket.key)
    put("while-queued.md", "q")
    runs.tick()
    put("while-dispatched.md", "d")
    r = authed.delete(f"/api/tickets/{ticket.key}/files/CLAUDE.md?base_version=1", headers=CSRF)
    assert r.status_code == 204, r.text
    m = JobPackage.model_validate(runs.claim(run.id).json()["manifest"])
    files = {e.path: e.content for e in m.context}
    assert files["while-queued.md"] == "q"
    assert files["while-dispatched.md"] == "d"
    assert "CLAUDE.md" not in files  # deleted files are not in the bundle


def test_a_manifest_that_cannot_be_built_fails_the_run(
    ticket: TicketDetail, runs: RunDriver, ctx: AppContext
) -> None:
    run = runs.queue(ticket.key)
    runs.tick()
    # Dispatch rendered the prompts; break one behind the API's back before the claim.
    with ctx.db.tx() as conn:
        row = conn.execute("SELECT pipeline FROM tickets WHERE key = ?", (ticket.key,)).fetchone()
        p = json.loads(row["pipeline"])
        p["stages"][0]["prompt"] = "{{ 1 / 0 }}"
        conn.execute("UPDATE tickets SET pipeline = ? WHERE key = ?", (json.dumps(p), ticket.key))
    assert error(runs.claim(run.id)) == (410, "run_gone")
    detail = runs.get(run.id)
    assert detail.status == "failed"
    assert detail.error is not None
    assert detail.error.startswith("cannot build the manifest: stage plan")
    assert [s.status for s in detail.stage_runs] == ["cancelled"]


def test_manifest_workspace_comes_from_the_repo_catalog(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    m = runs.start(create_ticket(repo="docs").key).manifest
    assert (m.workspace.repo_url, m.workspace.base_branch) == (
        "git@github.com:o/docs.git",
        "develop",
    )
    m = runs.start(create_ticket(repo="docs", base_branch="release/2").key).manifest
    assert m.workspace.base_branch == "release/2"


def test_a_repo_that_left_the_catalog_fails_the_run_at_claim(
    ticket: TicketDetail, runs: RunDriver, ctx: AppContext
) -> None:
    run = runs.queue(ticket.key)
    runs.tick()
    with ctx.db.tx() as conn:  # as if the catalog lost the repo between dispatch and claim
        conn.execute("UPDATE tickets SET repo = 'gone' WHERE key = ?", (ticket.key,))
    assert error(runs.claim(run.id)) == (410, "run_gone")
    detail = runs.get(run.id)
    assert detail.status == "failed"
    assert detail.error is not None
    assert "cannot build the manifest: repo: 'gone'" in detail.error


# ------------------------------------------------------------------ events


def test_events_are_idempotent_and_move_stage_runs(
    ticket: TicketDetail, runs: RunDriver, ctx: AppContext
) -> None:
    worker = runs.start(ticket.key, "implement")
    runs.set_run(worker.run_id, last_seen_at="2000-01-01T00:00:00.000000+00:00")
    batch = [event(1, "stage_started", "implement"), log_event(2, "implement", "hi")]
    assert worker.events(*batch).json() == {"cancel": False}
    assert worker.events(*batch).status_code == 200  # a retried batch
    assert runs.db_run(worker.run_id)["last_seen_at"] > "2000"
    detail = runs.get(worker.run_id)
    assert [(s.stage_id, s.status) for s in detail.stage_runs] == [
        ("implement", "running"),
        ("review", "pending"),
        ("open-pr", "pending"),
    ]
    assert detail.stage_runs[0].started_at is not None
    worker.events(finished(3, "implement", "failed", 2), log_event(4, "elsewhere", "stray"))
    detail = runs.get(worker.run_id)
    first = detail.stage_runs[0]
    assert (first.status, first.exit_code, first.finished_at is not None) == ("failed", 2, True)
    assert [s.status for s in detail.stage_runs[1:]] == ["pending", "pending"]
    events = runs.client.get(f"/api/runs/{worker.run_id}/events").json()
    assert [e["seq"] for e in events] == [1, 2, 3, 4]  # the unknown stage's event is kept
    assert events[2]["data"] == {"status": "failed", "exit_code": 2, "reason": None, "round": 1}


def test_stage_times_come_from_events_capped_at_now(ticket: TicketDetail, runs: RunDriver) -> None:
    worker = runs.start(ticket.key, "implement")
    start = event(1, "stage_started", "implement") | {"ts": "2026-01-01T00:00:00Z"}
    end = finished(2, "implement") | {"ts": "2099-01-01T00:00:00Z"}  # a runner clock far ahead
    worker.events(start, end)
    sr = runs.get(worker.run_id).stage_runs[0]
    assert sr.started_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert sr.finished_at is not None
    assert sr.finished_at <= datetime.now(UTC)


def test_empty_batch_is_a_heartbeat(ticket: TicketDetail, runs: RunDriver) -> None:
    worker = runs.start(ticket.key)
    runs.set_run(worker.run_id, last_seen_at="2000-01-01T00:00:00.000000+00:00")
    assert worker.events().json() == {"cancel": False}
    assert runs.db_run(worker.run_id)["last_seen_at"] > "2000"


def test_events_limits(
    ticket: TicketDetail, runs: RunDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = runs.start(ticket.key)
    too_many = [log_event(i, "plan", "x") for i in range(1, 502)]
    assert error(worker.events(*too_many)) == (422, "validation_error")
    monkeypatch.setattr(worker_service, "MAX_EVENTS_PER_RUN", 3)
    worker.events(*(log_event(i, "plan", "x") for i in range(1, 6)))
    worker.events(event(6, "warning", "plan", message="w"), finished(7, "plan"))
    events = runs.client.get(f"/api/runs/{worker.run_id}/events").json()
    assert [(e["seq"], e["type"]) for e in events] == [
        (1, "log"),
        (2, "log"),
        (3, "log"),
        (6, "warning"),  # non-log events are still stored past the cap
        (7, "stage_finished"),
    ]


def test_events_need_a_worker_token(ticket: TicketDetail, runs: RunDriver) -> None:
    run = runs.queue(ticket.key)
    runs.tick()
    r = runs.client.post(
        "/api/worker/events", json={"events": []}, headers=bearer(runs.launcher.token(run.id))
    )
    assert error(r) == (401, "unauthorized")  # the dispatch token isn't a worker token
    r = runs.client.post("/api/worker/events", json={"events": []})
    assert error(r) == (401, "unauthorized")


# ------------------------------------------------------------------ sync


def versions(client: TestClient, key: str, path: str) -> list[FileVersionInfo]:
    r = client.get(f"/api/tickets/{key}/versions/{path}")
    assert r.status_code == 200, r.text
    return [FileVersionInfo.model_validate(v) for v in r.json()]


def test_sync_writes_versions_authored_by_the_run(
    authed: TestClient, ticket: TicketDetail, runs: RunDriver, ctx: AppContext
) -> None:
    worker = runs.start(ticket.key)
    d = skill_dir(ticket)
    tasks = f"{d}/TASKS.md"
    r = worker.sync(
        "plan",
        [
            {"path": tasks, "content": "# Tasks\n- [x] one\n- [ ] two\n", "mode": "replace"},
            {"path": "AGENTS.md", "content": "edited block", "mode": "block"},
            {"path": f"{d}/learnings.log", "content": None, "mode": "replace"},
        ],
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "versions": {tasks: 2, "AGENTS.md": 2, f"{d}/learnings.log": 2},
        "refused": [],
    }
    latest = versions(authed, ticket.key, tasks)[-1]
    assert (latest.author, latest.author_run_id) == (None, worker.run_id)
    with ctx.db.read() as conn:
        stage = conn.execute(
            "SELECT author_stage FROM file_versions WHERE path = ? AND version = 2", (tasks,)
        ).fetchone()[0]
    assert stage == "plan"
    assert versions(authed, ticket.key, f"{d}/learnings.log")[-1].deleted
    t = TicketDetail.model_validate(authed.get(f"/api/tickets/{ticket.key}").json())
    assert t.tasks is not None
    assert (t.tasks.done, t.tasks.total) == (1, 2)
    # Unchanged content appends nothing.
    again = worker.sync(
        "plan", [{"path": tasks, "content": "# Tasks\n- [x] one\n- [ ] two\n", "mode": "replace"}]
    )
    assert again.json() == {"versions": {tasks: 2}, "refused": []}


@pytest.mark.parametrize(
    "path",
    [
        "{skill}/new.md",  # under workspace.protect, but undeclared
        "src/app.py",
        "../escape.md",
        ".claude/skills/other/SKILL.md",
        "AGENTS.md",  # declared as a block file, sent as a replace file
        "{skill}/.git/config",
        "{skill}",
    ],
)
def test_sync_refuses_undeclared_paths_and_applies_the_rest(
    authed: TestClient, ticket: TicketDetail, runs: RunDriver, path: str
) -> None:
    def files() -> dict[str, int]:
        listed = authed.get(f"/api/tickets/{ticket.key}/files").json()
        return {f["path"]: f["version"] for f in listed}

    before = files()
    worker = runs.start(ticket.key)
    d = skill_dir(ticket)
    tasks = f"{d}/TASKS.md"
    bad = path.replace("{skill}", d)
    good = {"path": tasks.upper(), "content": "# Tasks\n- [ ] x\n", "mode": "replace"}
    r = worker.sync("plan", [{"path": bad, "content": "x", "mode": "replace"}, good])
    assert r.status_code == 200, r.text
    assert r.json() == {"versions": {tasks: 2}, "refused": [bad]}
    assert files() == {**before, tasks: 2}


def test_a_human_edit_based_on_a_version_before_an_agent_sync_is_stale(
    authed: TestClient, ticket: TicketDetail, runs: RunDriver
) -> None:
    tasks = f"{skill_dir(ticket)}/TASKS.md"
    url = f"/api/tickets/{ticket.key}/files/{tasks}"
    seen = authed.get(url).json()["version"]
    worker = runs.start(ticket.key)
    r = worker.sync("plan", [{"path": tasks, "content": "# Tasks\n- [ ] a\n", "mode": "replace"}])
    synced = r.json()["versions"][tasks]
    assert synced == seen + 1
    worker.run_stage("plan")
    assert worker.complete().status_code == 204  # the gate: the bundle is unlocked again

    def put(base: int) -> Any:
        body = {"content": "# Tasks\n- [ ] mine\n", "mode": "replace", "base_version": base}
        return authed.put(url, json=body, headers=CSRF)

    assert error(put(seen)) == (409, "stale_version")
    assert authed.get(url).json()["content"] == "# Tasks\n- [ ] a\n"
    r = put(synced)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == synced + 1


def test_sync_rejects_a_stage_outside_the_run(ticket: TicketDetail, runs: RunDriver) -> None:
    worker = runs.start(ticket.key)
    assert error(worker.sync("review", [])) == (422, "validation_error")
    assert worker.sync("plan", []).json() == {"versions": {}, "refused": []}


# ------------------------------------------------------------------ sync_back


def test_sync_back_shows_on_the_run_and_downloads_as_an_attachment(
    ticket: TicketDetail, runs: RunDriver
) -> None:
    worker = runs.start(ticket.key)
    url = f"/api/runs/{worker.run_id}/sync-back"
    assert runs.get(worker.run_id).sync_back is None
    assert error(runs.client.get(url)) == (404, "not_found")

    assert worker.sync_back(b"first zip", files=3).status_code == 204
    zip_bytes = bytes(range(256)) * 4
    assert worker.sync_back(zip_bytes, files=2).status_code == 204  # a repeat replaces it
    assert worker.complete().status_code == 204

    info = runs.get(worker.run_id).sync_back
    assert info is not None
    assert (info.files, info.bytes) == (2, len(zip_bytes))
    download = runs.client.get(url)
    assert download.status_code == 200
    assert download.content == zip_bytes
    assert download.headers["content-type"] == "application/zip"
    assert download.headers["content-disposition"] == 'attachment; filename="sync_back.zip"'


def test_sync_back_refuses_a_bad_upload(
    ticket: TicketDetail, runs: RunDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = runs.start(ticket.key)
    monkeypatch.setattr(worker_service, "MAX_SYNC_BACK_BYTES", 8)

    assert error(worker.sync_back(b"zip", sha256="0" * 64)) == (422, "validation_error")
    assert error(worker.sync_back(b"zip", files=0)) == (422, "validation_error")
    assert error(worker.sync_back(b"")) == (422, "validation_error")
    assert error(worker.sync_back(b"x" * 9)) == (413, "too_large")
    assert worker.sync_back(b"x" * 8).status_code == 204


def test_sync_back_checks_the_token_before_the_body(
    ticket: TicketDetail, runs: RunDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = runs.start(ticket.key)
    monkeypatch.setattr(worker_service, "MAX_SYNC_BACK_BYTES", 8)
    worker.token += "-wrong"

    assert error(worker.sync_back(b"junk", sha256="0" * 64)) == (401, "unauthorized")
    assert error(worker.sync_back(b"x" * 9)) == (401, "unauthorized")


# ------------------------------------------------------------------ complete


def test_complete_gated_then_repeat_is_idempotent(ticket: TicketDetail, runs: RunDriver) -> None:
    worker = runs.start(ticket.key)
    worker.run_stage("plan")
    assert worker.complete().status_code == 204
    detail = runs.get(worker.run_id)
    assert detail.status == "gated"
    assert detail.finished_at is not None
    assert detail.error is None
    assert worker.complete().status_code == 204  # a retried complete
    assert error(worker.complete("failed")) == (410, "run_gone")
    assert error(worker.complete("cancelled")) == (410, "run_gone")


def test_gate_on_the_last_enabled_stage_succeeds(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    t = create_ticket(repo=None)  # open-pr is disabled
    detail = authed.get(f"/api/tickets/{t.key}").json()
    pipeline = detail["pipeline"]
    next(s for s in pipeline["stages"] if s["id"] == "review")["gate_after"] = True
    r = authed.put(
        f"/api/tickets/{t.key}/pipeline",
        json={"pipeline": pipeline, "base_version": detail["pipeline_version"]},
        headers=CSRF,
    )
    assert r.status_code == 200, r.text
    worker = runs.start(t.key, "implement")
    worker.run_stage("implement")
    worker.run_stage("review")
    assert worker.complete().status_code == 204
    assert runs.get(worker.run_id).status == "succeeded"


@pytest.mark.parametrize(
    ("body", "error_text"),
    [
        ({"status": "failed", "failed_stage": "plan"}, "stage plan failed"),
        (
            {"status": "failed", "failed_stage": None, "error": "git clone failed"},
            "git clone failed",
        ),
        ({"status": "failed"}, "the run failed"),
    ],
)
def test_complete_failed_records_the_error(
    ticket: TicketDetail, runs: RunDriver, body: dict[str, Any], error_text: str
) -> None:
    worker = runs.start(ticket.key)
    r = worker.post("complete", body)
    assert r.status_code == 204, r.text
    detail = runs.get(worker.run_id)
    assert (detail.status, detail.error) == ("failed", error_text)
    assert [s.status for s in detail.stage_runs] == ["cancelled"]
    assert worker.post("complete", body).status_code == 204
    assert error(worker.complete()) == (410, "run_gone")


PR = "https://github.com/o/r/pull/7"


def pr_urls(client: TestClient, key: str) -> tuple[str | None, str | None]:
    """The ticket's pr_url as (detail, list row)."""
    detail = client.get(f"/api/tickets/{key}").json()
    row = next(t for t in client.get("/api/tickets").json() if t["key"] == key)
    return detail["pr_url"], row["pr_url"]


def test_complete_stores_the_pr_url_and_a_later_run_without_one_keeps_it(
    authed: TestClient, ticket: TicketDetail, runs: RunDriver
) -> None:
    assert pr_urls(authed, ticket.key) == (None, None)
    worker = runs.start(ticket.key)
    worker.run_stage("plan")
    assert worker.complete(pr_url=PR).status_code == 204
    assert pr_urls(authed, ticket.key) == (PR, PR)

    later = runs.start(ticket.key, "implement")
    assert later.complete("failed", failed_stage="implement").status_code == 204
    assert pr_urls(authed, ticket.key) == (PR, PR)


@pytest.mark.parametrize(
    "bad",
    [
        "http://github.com/o/r/pull/7",
        "javascript:alert(1)",
        "https://user:pw@github.com/o/r/pull/7",
        "https://github.com/o/r/pull/7 now",
    ],
)
def test_complete_ignores_an_invalid_pr_url(
    authed: TestClient, ticket: TicketDetail, runs: RunDriver, bad: str
) -> None:
    worker = runs.start(ticket.key)
    assert worker.complete(pr_url=PR).status_code == 204
    later = runs.start(ticket.key, "implement")
    r = later.complete("cancelled", pr_url=bad)
    assert r.status_code == 204, r.text
    assert runs.get(later.run_id).status == "cancelled"
    assert pr_urls(authed, ticket.key) == (PR, PR)


def gone_routes(worker: Worker, other_status: str = "failed") -> list[Any]:
    """Every worker-token route; `complete` with a status that isn't the run's final one."""
    return [
        worker.events(log_event(99, "plan", "late")),
        worker.sync("plan", []),
        worker.sync_back(b"zip"),
        worker.complete(other_status),
    ]


@pytest.mark.parametrize("how", ["completed", "cancelled", "reaped"])
def test_every_worker_route_is_gone_after_terminal(
    ticket: TicketDetail, runs: RunDriver, how: str
) -> None:
    worker = runs.start(ticket.key)
    if how == "completed":
        worker.complete()
    elif how == "cancelled":
        runs.client.post(f"/api/runs/{worker.run_id}/cancel", headers=CSRF)
        worker.complete("cancelled")
    else:
        with runs.ctx.db.tx() as conn:
            conn.execute("UPDATE runs SET status = 'failed' WHERE id = ?", (worker.run_id,))
    for r in gone_routes(worker, "failed" if how == "completed" else "completed"):
        assert error(r) == (410, "run_gone")
    assert error(runs.claim(worker.run_id)) == (410, "run_gone")


def test_unknown_worker_token_is_401(ticket: TicketDetail, runs: RunDriver) -> None:
    worker = runs.start(ticket.key)
    stranger = Worker(runs.client, worker.run_id, "z" * 43, worker.manifest)
    for r in gone_routes(stranger):
        assert error(r) == (401, "unauthorized")


def test_worker_source_is_served(client: TestClient) -> None:
    response = client.get("/api/worker/worker.py")
    assert response.status_code == 200
    assert "PROTOCOL = 2" in response.text
