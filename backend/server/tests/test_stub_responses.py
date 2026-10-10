"""Every response must validate against its model (FastAPI serializes without validating)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from agentos.main import create_app
from agentos.models import (
    AppConfig,
    ChatMessage,
    FileContent,
    FileInfo,
    FileVersionInfo,
    Me,
    RunDetail,
    RunEvent,
    RunSummary,
    Template,
    TicketDetail,
    TicketSummary,
    User,
)

CSRF = {"X-AgentOS": "1"}


@pytest.fixture
def client() -> TestClient:
    client = TestClient(create_app())
    assert client.post("/api/auth/login", json={"username": "dev"}, headers=CSRF).status_code == 200
    return client


def check(client: TestClient, url: str, shape: Any) -> Any:
    response = client.get(url)
    assert response.status_code == 200, f"{url}: {response.text}"
    return TypeAdapter(shape).validate_python(response.json())


def test_every_stub_get_matches_its_model(client: TestClient) -> None:
    check(client, "/api/config", AppConfig)
    check(client, "/api/me", Me)
    check(client, "/api/users", list[User])
    check(client, "/api/template", Template)
    for summary in check(client, "/api/tickets?include_closed=true", list[TicketSummary]):
        key = summary.key
        check(client, f"/api/tickets/{key}", TicketDetail)
        files = check(client, f"/api/tickets/{key}/files", list[FileInfo])
        for info in files:
            check(client, f"/api/tickets/{key}/files/{info.path}", FileContent)
            check(client, f"/api/tickets/{key}/versions/{info.path}", list[FileVersionInfo])
        check(client, f"/api/tickets/{key}/chat", list[ChatMessage])
        for run in check(client, f"/api/tickets/{key}/runs", list[RunSummary]):
            check(client, f"/api/runs/{run.id}", RunDetail)
            check(client, f"/api/runs/{run.id}/events", list[RunEvent])


def test_patch_rejects_null_for_required_fields(client: TestClient) -> None:
    response = client.patch("/api/tickets/AOS-1", json={"title": None}, headers=CSRF)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    check(client, "/api/tickets", list[TicketSummary])


def test_patch_allows_clearing_repo_url(client: TestClient) -> None:
    response = client.patch("/api/tickets/AOS-1", json={"repo_url": None}, headers=CSRF)
    assert response.status_code == 200
    assert TicketDetail.model_validate(response.json()).repo_url is None


def test_cancel_running_run_waits_for_worker(client: TestClient) -> None:
    run = RunSummary.model_validate(client.get("/api/tickets/AOS-3").json()["latest_run"])
    assert run.status == "running"
    response = client.post(f"/api/runs/{run.id}/cancel", headers=CSRF)
    cancelled = RunSummary.model_validate(response.json())
    assert cancelled.status == "running"
    assert cancelled.cancel_requested_at is not None
