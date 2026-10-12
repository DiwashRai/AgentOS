"""Every GET response must validate against its response model.

FastAPI serializes a route's return value without validating it against the declared model, so a
service could drift from the contract unnoticed. Real data is created through the API (runs driven
through the worker API) so every optional field has a value somewhere.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from agentos.models import (
    AppConfig,
    ChatMessage,
    FileContent,
    FileInfo,
    FileVersionInfo,
    IntakeQuery,
    IntakeSettings,
    Manifest,
    Me,
    Person,
    RepoSettings,
    RunDetail,
    RunEvent,
    RunnerSettings,
    RunSummary,
    RuntimeSettings,
    SpaceSettings,
    Template,
    TicketDetail,
    TicketSummary,
    User,
)
from tests.run_support import RunDriver

CSRF = {"X-AgentOS": "1"}
CreateTicket = Callable[..., TicketDetail]
LoginFn = Callable[..., Me]


def check(authed: TestClient, url: str, shape: Any) -> Any:
    response = authed.get(url)
    assert response.status_code == 200, f"{url}: {response.text}"
    return TypeAdapter(shape).validate_python(response.json())


def test_every_get_matches_its_model(
    login: LoginFn, client: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    sam = login("sam")
    login("dev")
    # A repo without a default branch (request 22A), for a blank, blocked ticket.
    repos = check(client, "/api/settings/repos", RepoSettings)
    bare = {"name": "bare", "url": "https://github.com/o/bare.git", "default_branch": None}
    entries = [*(r.model_dump(exclude={"open_ticket_count"}) for r in repos.repos), bare]
    put = client.put(
        "/api/settings/repos", json={"repos": entries, "base_version": 1}, headers=CSRF
    )
    assert put.status_code == 200, put.text
    create_ticket("Blank ticket", repo="bare")
    # Manual base branch (set_by), then locked by its first run.
    repo = create_ticket(
        "Repo ticket", labels=["x"], owner_id=sam.id, repo="docs", base_branch="develop"
    )
    no_repo = create_ticket("No-repo ticket", repo=None)
    closed = create_ticket("Closed ticket")
    client.patch(f"/api/tickets/{closed.key}", json={"state": "closed"}, headers=CSRF)
    abandoned = create_ticket("Abandoned ticket")
    response = client.post(
        f"/api/tickets/{abandoned.key}/resolve",
        json={"outcome": "abandoned", "note": "not needed"},
        headers=CSRF,
    )
    assert response.status_code == 200, response.text
    client.post(f"/api/tickets/{repo.key}/chat", json={"body": "hi"}, headers=CSRF)
    client.put(
        f"/api/tickets/{repo.key}/files/gone.md",
        json={"content": "x", "base_version": 0},
        headers=CSRF,
    )
    client.delete(f"/api/tickets/{repo.key}/files/gone.md?base_version=1", headers=CSRF)
    # repo: plan gated, then implement cancelled mid-stage; no-repo: plan failed.
    worker = runs.start(repo.key)
    worker.run_stage("plan")
    assert worker.complete().status_code == 204
    worker = runs.start(repo.key)
    worker.events(
        {
            "seq": 1,
            "ts": "2026-10-10T12:00:00Z",
            "stage_id": "implement",
            "type": "stage_started",
            "data": {},
        }
    )
    assert client.post(f"/api/runs/{worker.run_id}/cancel", headers=CSRF).status_code == 200
    assert worker.complete("cancelled", failed_stage="implement").status_code == 204
    worker = runs.start(no_repo.key)
    worker.run_stage("plan", "failed")
    assert worker.complete("failed", failed_stage="plan").status_code == 204

    check(client, "/api/config", AppConfig)
    check(client, "/api/me", Me)
    check(client, "/api/users", list[User])
    check(client, "/api/people", list[Person])
    check(client, "/api/template", Template)
    # No intake queries in the test config; test_intake.py validates filled answers.
    check(client, "/api/intake", list[IntakeQuery])
    # Settings: seeded (updated_by null), then saved by someone (updated_by set).
    space = check(client, "/api/settings/space", SpaceSettings)
    assert space.updated_by is None
    body = space.model_dump(exclude={"version", "updated_at", "updated_by"})
    saved = client.put(
        "/api/settings/space", json={**body, "base_version": space.version}, headers=CSRF
    )
    assert saved.status_code == 200, saved.text
    assert check(client, "/api/settings/space", SpaceSettings).updated_by is not None
    assert check(client, "/api/settings/repos", RepoSettings).repos
    queries = {"queries": [{"id": None, "name": "To do", "jql": "x"}], "base_version": 1}
    assert client.put("/api/settings/intake", json=queries, headers=CSRF).status_code == 200
    assert check(client, "/api/settings/intake", IntakeSettings).queries
    runtimes = check(client, "/api/settings/runtimes", RuntimeSettings)
    body = {"runtimes": [r.model_dump() for r in runtimes.runtimes], "base_version": 1}
    assert client.put("/api/settings/runtimes", json=body, headers=CSRF).status_code == 200
    assert check(client, "/api/settings/runtimes", RuntimeSettings).updated_by is not None
    runner = check(client, "/api/settings/runner", RunnerSettings)
    body = {"github_repo": "o/r", "workflow": runner.workflow, "ref": "main", "base_version": 1}
    assert client.put("/api/settings/runner", json=body, headers=CSRF).status_code == 200
    assert check(client, "/api/settings/runner", RunnerSettings).github_repo == "o/r"
    summaries = check(client, "/api/tickets?include_closed=true", list[TicketSummary])
    assert len(summaries) == 5
    assert any(s.latest_run is not None for s in summaries)
    assert any(s.resolution is not None for s in summaries)
    assert any(s.dispatch_block is not None for s in summaries)
    infos = []
    for summary in summaries:
        key = summary.key
        infos.append(check(client, f"/api/tickets/{key}", TicketDetail).base_branch_info)
    assert any(i is not None and i.set_by is not None and i.locked for i in infos)
    assert any(i is not None and i.reason is not None for i in infos)
    for summary in summaries:
        key = summary.key
        for info in check(client, f"/api/tickets/{key}/files", list[FileInfo]):
            check(client, f"/api/tickets/{key}/files/{info.path}", FileContent)
            check(client, f"/api/tickets/{key}/versions/{info.path}", list[FileVersionInfo])
        check(client, f"/api/tickets/{key}/chat", list[ChatMessage])
        for run in check(client, f"/api/tickets/{key}/runs", list[RunSummary]):
            check(client, f"/api/runs/{run.id}", RunDetail)
            check(client, f"/api/runs/{run.id}/manifest", Manifest)
            assert check(client, f"/api/runs/{run.id}/events", list[RunEvent])
    deleted = check(client, f"/api/tickets/{repo.key}/versions/gone.md", list[FileVersionInfo])
    assert [v.deleted for v in deleted] == [False, True]
