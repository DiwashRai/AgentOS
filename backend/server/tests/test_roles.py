"""Role gates on every route (SPEC §10 role column).

Every mutating `/api` route must be classified in MUTATING_ROLES (worker routes use run tokens and
auth routes are public or any-user), so a new route without a role fails here. Role dependencies
must reject before body validation: every request below is sent without a body.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentos.models import Me, Role

CSRF = {"X-AgentOS": "1"}
LoginFn = Callable[..., Me]
ROLES: tuple[Role, ...] = ("viewer", "writer", "admin")

MUTATING_ROLES: dict[tuple[str, str], Role] = {
    ("POST", "/api/tickets"): "writer",
    ("POST", "/api/tickets/import"): "writer",
    ("PATCH", "/api/tickets/{key}"): "writer",
    ("POST", "/api/tickets/{key}/resolve"): "writer",
    ("PUT", "/api/tickets/{key}/pipeline"): "writer",
    ("POST", "/api/tickets/{key}/workflow"): "writer",
    ("PUT", "/api/tickets/{key}/files/{path}"): "writer",
    ("DELETE", "/api/tickets/{key}/files/{path}"): "writer",
    ("POST", "/api/tickets/{key}/chat"): "viewer",
    ("POST", "/api/tickets/{key}/runs"): "writer",
    ("POST", "/api/runs/{run_id}/cancel"): "writer",
    ("PUT", "/api/template"): "admin",
    ("POST", "/api/workflows"): "admin",
    ("PUT", "/api/workflows/{workflow_id}"): "admin",
    ("POST", "/api/workflows/{workflow_id}/default"): "admin",
    ("DELETE", "/api/workflows/{workflow_id}"): "admin",
    ("PATCH", "/api/users/{user_id}"): "admin",
    ("PUT", "/api/settings/space"): "admin",
    ("POST", "/api/settings/space/validate"): "admin",
    ("PUT", "/api/settings/repos"): "admin",
    ("POST", "/api/settings/repos/validate"): "admin",
    ("PUT", "/api/settings/intake"): "admin",
    ("POST", "/api/settings/intake/validate"): "admin",
    ("POST", "/api/settings/intake/preview"): "admin",
    ("PUT", "/api/settings/mappings"): "admin",
    ("POST", "/api/settings/mappings/validate"): "admin",
    ("POST", "/api/settings/mappings/preview"): "admin",
    ("PUT", "/api/settings/runtimes"): "admin",
    ("PUT", "/api/settings/runner"): "admin",
    ("POST", "/api/settings/runner/validate"): "admin",
    ("POST", "/api/me/clients"): "writer",
    ("DELETE", "/api/me/clients/{client_id}"): "viewer",
}
PUBLIC_GETS = {"/api/health", "/api/config", "/api/worker/worker.py"}
ADMIN_GETS = {
    "/api/users",
    "/api/settings/space",
    "/api/settings/repos",
    "/api/settings/intake",
    "/api/settings/mappings",
    "/api/settings/mappings/jira-fields",
    "/api/settings/runtimes",
    "/api/settings/runner",
}

# Path parameters point at things that don't exist, so a missing gate can't mutate stub data.
SAMPLE = {
    "key": "NOPE-999",
    "path": "SKILL.md",
    "run_id": "999999",
    "user_id": "999999",
    "workflow_id": "999999",
    "client_id": "999999",
}
_PARAM = re.compile(r"\{(\w+)\}")


def url(path: str) -> str:
    return _PARAM.sub(lambda m: SAMPLE[m.group(1)], path)


def api_routes(app: FastAPI) -> set[tuple[str, str]]:
    """(METHOD, path) for every /api route, read from the OpenAPI schema (FastAPI's public view of
    the routes; `{path:path}` appears as `{path}`). A route with include_in_schema=False would
    escape this check, so don't hide /api routes."""
    return {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/")
        for method in operations
    }


def classified(method: str, path: str) -> bool:
    return method != "GET" and not path.startswith(("/api/worker/", "/api/auth/"))


def test_every_mutating_route_has_a_role(app: FastAPI) -> None:
    mutating = {(m, p) for m, p in api_routes(app) if classified(m, p)}
    assert mutating == set(MUTATING_ROLES), (
        "Add new routes to MUTATING_ROLES (and gate them with CurrentUser/Writer/Admin): "
        f"missing={sorted(mutating - set(MUTATING_ROLES))}, "
        f"stale={sorted(set(MUTATING_ROLES) - mutating)}"
    )


def test_get_classification_is_current(app: FastAPI) -> None:
    gets = {p for m, p in api_routes(app) if m == "GET"}
    assert gets >= PUBLIC_GETS | ADMIN_GETS


TOO_LOW = [
    pytest.param(method, path, lower, id=f"{method} {path} as {lower}")
    for (method, path), required in MUTATING_ROLES.items()
    for lower in ROLES[: ROLES.index(required)]
]


@pytest.mark.parametrize(("method", "path", "role"), TOO_LOW)
def test_too_low_role_is_forbidden(
    login: LoginFn, client: TestClient, method: str, path: str, role: Role
) -> None:
    login("someone", role)
    response = client.request(method, url(path), headers=CSRF)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize(
    ("method", "path", "role"),
    [pytest.param(m, p, r, id=f"{m} {p} as {r}") for (m, p), r in MUTATING_ROLES.items()],
)
def test_required_role_passes_the_gate(
    login: LoginFn, client: TestClient, method: str, path: str, role: Role
) -> None:
    """With exactly the required role the request passes auth (then 404/422 on the fake input)."""
    login("someone", role)
    response = client.request(method, url(path), headers=CSRF)
    assert response.status_code not in (401, 403), response.text


@pytest.mark.parametrize(
    ("method", "path"),
    [pytest.param(m, p, id=f"{m} {p}") for m, p in MUTATING_ROLES],
)
def test_mutating_routes_require_a_session(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, url(path), headers=CSRF)
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "unauthorized"


def test_gets_require_a_session_unless_public(app: FastAPI, client: TestClient) -> None:
    for method, path in sorted(api_routes(app)):
        if method != "GET" or (path.startswith("/api/worker/") and path not in PUBLIC_GETS):
            continue  # worker routes authenticate with run tokens, not sessions
        response = client.get(url(path))
        expected = 200 if path in PUBLIC_GETS else 401
        assert response.status_code == expected, f"GET {path}: {response.text}"


def test_pending_user_reaches_only_me_and_logout(
    app: FastAPI, client: TestClient, login: LoginFn
) -> None:
    """Approval is enforced by the API: every session route but GET /api/me (and logout) answers
    403 `pending_approval`, so a new route can't slip through. Worker routes use run tokens."""
    login("newcomer", "pending")
    for method, path in sorted(api_routes(app)):
        if path.startswith(("/api/worker/", "/api/auth/")) or path in PUBLIC_GETS:
            continue
        response = client.request(method, url(path), headers=CSRF)
        if (method, path) == ("GET", "/api/me"):
            assert response.json()["role"] == "pending"
            continue
        assert response.status_code == 403, f"{method} {path}: {response.text}"
        assert response.json()["error"]["code"] == "pending_approval", f"{method} {path}"
    assert client.post("/api/auth/logout", headers=CSRF).status_code == 204


@pytest.mark.parametrize("role", ["viewer", "writer"])
def test_admin_gets_are_forbidden_below_admin(
    login: LoginFn, client: TestClient, role: Role
) -> None:
    login("someone", role)
    for path in sorted(ADMIN_GETS):
        assert client.get(url(path)).status_code == 403, path
