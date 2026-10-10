"""Section 0 contract checks: shapes, error envelope, CSRF."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from agentos.models import Manifest

FIXTURES = Path(__file__).parent / "fixtures"
CSRF = {"X-AgentOS": "1"}


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


# The board-coverage check (every attention group, phase and rail state) moved to
# test_ticket_board.py::test_board_covers_every_attention_group_and_state (real tickets).


def test_worker_source_is_served(client: TestClient) -> None:
    response = client.get("/api/worker/worker.py")
    assert response.status_code == 200
    assert "PROTOCOL = 1" in response.text
