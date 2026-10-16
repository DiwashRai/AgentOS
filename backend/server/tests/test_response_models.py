"""Every GET response must validate against its response model.

FastAPI serializes a route's return value without validating it against the declared model, so a
service could drift from the contract unnoticed. Real data is created through the API (runs driven
through the worker API) so every optional field has a value somewhere.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr, TypeAdapter

from agentos.context import AppContext
from agentos.models import (
    AppConfig,
    ChatMessage,
    ClientToken,
    FileContent,
    FileInfo,
    FileVersionInfo,
    IntakeQuery,
    IntakeSettings,
    JiraField,
    JobPackage,
    MappingSettings,
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
    Workflow,
)
from agentos.sources.jira import JiraClient
from tests.run_support import INSTANCE, LEASE_SEQ, RunDriver, bearer

CSRF = {"X-AgentOS": "1"}
CreateTicket = Callable[..., TicketDetail]
LoginFn = Callable[..., Me]


def check(authed: TestClient, url: str, shape: Any) -> Any:
    response = authed.get(url)
    assert response.status_code == 200, f"{url}: {response.text}"
    return TypeAdapter(shape).validate_python(response.json())


def test_every_get_matches_its_model(
    login: LoginFn,
    client: TestClient,
    create_ticket: CreateTicket,
    runs: RunDriver,
    ctx: AppContext,
) -> None:
    sam = login("sam")
    dev = login("dev")
    # Client tokens: one seen (it leased), one never; then a client run waits for the client.
    tokens = [
        client.post("/api/me/clients", json={"name": n}, headers=CSRF).json()["token"]
        for n in ("seen", "never")
    ]
    seen = client.post(
        "/api/worker/lease",
        json={"instance": INSTANCE, "seq": next(LEASE_SEQ), "wait_s": 0},
        headers=bearer(tokens[0]),
    )
    assert seen.json()["job"] is None
    waiting = create_ticket("Client ticket", repo=None, owner_id=dev.id)
    assert runs.queue(waiting.key, target="client").client_online is True
    # A repo without a default branch (request 22A), for a blank, blocked ticket.
    repos = check(client, "/api/settings/repos", RepoSettings)
    bare = {"name": "bare", "url": "https://github.com/o/bare.git", "default_branch": None}
    entries = [*(r.model_dump() for r in repos.value.repos), bare]
    put = client.put(
        "/api/settings/repos",
        json={"value": {"repos": entries}, "base_version": 1},
        headers=CSRF,
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
    assert worker.sync_back(b"zip").status_code == 204
    assert worker.complete().status_code == 200
    worker = runs.start(repo.key)
    worker.events(
        {
            "seq": worker.next_seq(),
            "ts": "2026-10-10T12:00:00Z",
            "stage_id": "implement",
            "type": "stage_started",
            "data": {},
        }
    )
    assert client.post(f"/api/runs/{worker.run_id}/cancel", headers=CSRF).status_code == 200
    job = client.get("/api/worker/job", headers=worker.headers)  # the worker's own GET
    JobPackage.model_validate(job.json())
    assert worker.complete("cancelled", failed_stage="implement").status_code == 200
    worker = runs.start(no_repo.key)
    worker.run_stage("plan", "failed")
    assert worker.complete("failed", failed_stage="plan").status_code == 200

    check(client, "/api/config", AppConfig)
    check(client, "/api/me", Me)
    clients = check(client, "/api/me/clients", list[ClientToken])
    assert [c.last_seen_at is not None for c in clients] == [True, False]
    check(client, "/api/users", list[User])
    check(client, "/api/people", list[Person])
    check(client, "/api/template", Template)
    check(client, "/api/workflows", list[Workflow])
    # No intake queries in the test config; test_intake.py validates filled answers.
    check(client, "/api/intake", list[IntakeQuery])
    # Settings: seeded (updated_by null), then saved by someone (updated_by set).
    space = check(client, "/api/settings/space", SpaceSettings)
    assert space.updated_by is None
    body = {"value": space.value.model_dump(), "base_version": space.version}
    saved = client.put("/api/settings/space", json=body, headers=CSRF)
    assert saved.status_code == 200, saved.text
    assert check(client, "/api/settings/space", SpaceSettings).updated_by is not None
    assert check(client, "/api/settings/repos", RepoSettings).open_ticket_counts["bare"] == 1
    queries = [{"id": None, "name": "To do", "jql": "x"}]
    body = {"value": {"queries": queries}, "base_version": 1}
    assert client.put("/api/settings/intake", json=body, headers=CSRF).status_code == 200
    assert check(client, "/api/settings/intake", IntakeSettings).value.queries
    # Field mappings, and the Jira field picker against a mocked Jira.
    assert check(client, "/api/settings/mappings", MappingSettings).value.mappings == []
    mapping = {
        "target": "base_branch",
        "source": "jira",
        "field": "fixVersions",
        "field_name": "Fix versions",
        "rules": [{"match": "version_1*", "value": "App-1.Latest"}],
    }
    body = {"value": {"mappings": [mapping]}, "base_version": 1}
    assert client.put("/api/settings/mappings", json=body, headers=CSRF).status_code == 200
    assert check(client, "/api/settings/mappings", MappingSettings).updated_by is not None
    fields = [{"id": "fixVersions", "name": "Fix versions", "schema": {"type": "array"}}]
    ctx.jira = JiraClient(
        "https://jira.example.com",
        "cloud",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=fields)),
    )
    ctx.settings = ctx.settings.model_copy(update={"jira_service_token": SecretStr("t")})
    assert check(client, "/api/settings/mappings/jira-fields", list[JiraField])
    runtimes = check(client, "/api/settings/runtimes", RuntimeSettings)
    body = {"runtimes": [r.model_dump() for r in runtimes.runtimes], "base_version": 1}
    assert client.put("/api/settings/runtimes", json=body, headers=CSRF).status_code == 200
    assert check(client, "/api/settings/runtimes", RuntimeSettings).updated_by is not None
    runner = check(client, "/api/settings/runner", RunnerSettings)
    value = runner.value.model_dump() | {"github_repo": "o/r"}
    body = {"value": value, "base_version": 1}
    assert client.put("/api/settings/runner", json=body, headers=CSRF).status_code == 200
    assert check(client, "/api/settings/runner", RunnerSettings).value.github_repo == "o/r"
    summaries = check(client, "/api/tickets?include_closed=true", list[TicketSummary])
    assert len(summaries) == 6
    assert any(s.latest_run is not None for s in summaries)
    assert any(s.latest_run is not None and s.latest_run.client_online for s in summaries)
    assert any(s.resolution is not None for s in summaries)
    assert any(s.dispatch_block is not None for s in summaries)
    infos = []
    details: list[RunDetail] = []
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
            details.append(check(client, f"/api/runs/{run.id}", RunDetail))
            if run.status == "queued":  # the client run: no manifest or events yet
                continue
            check(client, f"/api/runs/{run.id}/manifest", JobPackage)
            assert check(client, f"/api/runs/{run.id}/events", list[RunEvent])
    assert any(d.sync_back is not None for d in details)
    deleted = check(client, f"/api/tickets/{repo.key}/versions/gone.md", list[FileVersionInfo])
    assert [v.deleted for v in deleted] == [False, True]
