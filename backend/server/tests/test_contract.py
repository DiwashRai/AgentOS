"""Section 0 contract checks: shapes, error envelope, CSRF, stub data coverage."""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest
from fastapi.testclient import TestClient

from agentos.main import create_app
from agentos.models import Attention, Manifest, Phase, PhaseState, RailState, TicketSummary

FIXTURES = Path(__file__).parent / "fixtures"
CSRF = {"X-AgentOS": "1"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def authed(client: TestClient) -> TestClient:
    assert client.post("/api/auth/login", json={"username": "dev"}, headers=CSRF).status_code == 200
    return client


def test_manifest_fixture_matches_model() -> None:
    manifest = Manifest.model_validate_json((FIXTURES / "manifest_v1.json").read_text("utf-8"))
    assert manifest.protocol == 1
    assert {f.mode for f in manifest.files} == {"replace", "block"}
    assert {s.kind for s in manifest.stages} == {"agent", "shell"}


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}


def test_openapi_includes_sse_payload(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert "StreamEvent" in schemas
    assert "ErrorBody" in schemas


def test_mutation_without_csrf_header_is_rejected(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "dev"})
    assert response.status_code == 403
    assert response.json() == {
        "error": {"code": "csrf_header_missing", "message": "Missing X-AgentOS: 1 header"}
    }


def test_unauthenticated_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/tickets")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_validation_uses_error_envelope(authed: TestClient) -> None:
    response = authed.post("/api/tickets", json={"title": ""}, headers=CSRF)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_stub_board_covers_every_attention_group_and_state(authed: TestClient) -> None:
    tickets = [TicketSummary.model_validate(t) for t in authed.get("/api/tickets").json()]
    assert {t.attention for t in tickets} == set(get_args(Attention))
    assert {s.state for t in tickets for s in t.phase_track} == set(get_args(PhaseState))
    assert {s.state for t in tickets for s in t.rail} == set(get_args(RailState))
    assert all([p.phase for p in t.phase_track] == list(get_args(Phase)) for t in tickets)


def test_worker_source_is_served(client: TestClient) -> None:
    response = client.get("/worker.py")
    assert response.status_code == 200
    assert "PROTOCOL = 1" in response.text
