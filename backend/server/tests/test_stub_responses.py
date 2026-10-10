"""Every GET response must validate against its model (FastAPI serializes without validating).

Real data is created through the API (plus a run inserted into the db) so every optional field
has a value somewhere.

TODO(runs): the runs endpoints still serve stub data keyed by the stub's own tickets, so
`/api/tickets/{key}/runs`, `/api/runs/{id}`, `/api/runs/{id}/manifest` and
`/api/runs/{id}/events` are left out of the walk below (only the stub run is checked). Add them
back to `test_every_get_matches_its_model` once runs are real.
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
    Manifest,
    Me,
    Person,
    RunDetail,
    RunEvent,
    RunSummary,
    Template,
    TicketDetail,
    TicketSummary,
    User,
)

CSRF = {"X-AgentOS": "1"}
CreateTicket = Callable[..., TicketDetail]
AddRun = Callable[..., int]
LoginFn = Callable[..., Me]


def check(authed: TestClient, url: str, shape: Any) -> Any:
    response = authed.get(url)
    assert response.status_code == 200, f"{url}: {response.text}"
    return TypeAdapter(shape).validate_python(response.json())


def test_every_get_matches_its_model(
    login: LoginFn, client: TestClient, create_ticket: CreateTicket, add_run: AddRun
) -> None:
    sam = login("sam")
    dev = login("dev")
    repo = create_ticket(
        "Repo ticket", labels=["x"], owner_id=sam.id, repo_url="https://github.com/o/r.git"
    )
    create_ticket("No-repo ticket", workspace_mode="none")
    closed = create_ticket("Closed ticket")
    client.patch(f"/api/tickets/{closed.key}", json={"state": "closed"}, headers=CSRF)
    client.post(f"/api/tickets/{repo.key}/chat", json={"body": "hi"}, headers=CSRF)
    client.put(
        f"/api/tickets/{repo.key}/files/gone.md",
        json={"content": "x", "base_version": 0},
        headers=CSRF,
    )
    client.delete(f"/api/tickets/{repo.key}/files/gone.md?base_version=1", headers=CSRF)
    add_run(repo.key, "gated", [("plan", "succeeded")], created_by=dev.id)

    check(client, "/api/config", AppConfig)
    check(client, "/api/me", Me)
    check(client, "/api/users", list[User])
    check(client, "/api/people", list[Person])
    check(client, "/api/template", Template)
    summaries = check(client, "/api/tickets?include_closed=true", list[TicketSummary])
    assert len(summaries) == 3
    assert any(s.latest_run is not None for s in summaries)
    for summary in summaries:
        key = summary.key
        check(client, f"/api/tickets/{key}", TicketDetail)
        for info in check(client, f"/api/tickets/{key}/files", list[FileInfo]):
            check(client, f"/api/tickets/{key}/files/{info.path}", FileContent)
            check(client, f"/api/tickets/{key}/versions/{info.path}", list[FileVersionInfo])
        check(client, f"/api/tickets/{key}/chat", list[ChatMessage])
    deleted = check(client, f"/api/tickets/{repo.key}/versions/gone.md", list[FileVersionInfo])
    assert [v.deleted for v in deleted] == [False, True]


def test_stub_run_endpoints_match_their_models(authed: TestClient) -> None:
    """TODO(runs): stub run 1 (the stub's AOS-3); fold into the walk above once runs are real."""
    run = check(authed, "/api/runs/1", RunDetail)
    check(authed, "/api/runs/1/manifest", Manifest)
    check(authed, "/api/runs/1/events", list[RunEvent])
    assert run.ticket_key == "AOS-3"


def test_patch_rejects_null_for_required_fields(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    t = create_ticket()
    response = authed.patch(f"/api/tickets/{t.key}", json={"title": None}, headers=CSRF)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    check(authed, "/api/tickets", list[TicketSummary])


def test_patch_allows_clearing_repo_url(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket(repo_url="https://github.com/o/r.git")
    response = authed.patch(f"/api/tickets/{t.key}", json={"repo_url": None}, headers=CSRF)
    assert response.status_code == 200
    assert TicketDetail.model_validate(response.json()).repo_url is None


def test_cancel_running_run_waits_for_worker(authed: TestClient) -> None:
    """TODO(runs): runs still use the stub store; its run 1 is running (the stub's AOS-3)."""
    run = check(authed, "/api/runs/1", RunDetail)
    assert run.status == "running"
    response = authed.post(f"/api/runs/{run.id}/cancel", headers=CSRF)
    cancelled = RunSummary.model_validate(response.json())
    assert cancelled.status == "running"
    assert cancelled.cancel_requested_at is not None
