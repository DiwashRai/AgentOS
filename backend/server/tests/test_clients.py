"""Client tokens (Settings > My client): create (shown once), list, revoke; own tokens only."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from agentos.context import AppContext
from agentos.models import ClientToken, ClientTokenCreated, Me
from tests.run_support import CSRF

LoginFn = Callable[..., Me]


def create(client: TestClient, name: str = "laptop") -> ClientTokenCreated:
    r = client.post("/api/me/clients", json={"name": name}, headers=CSRF)
    assert r.status_code == 201, r.text
    return ClientTokenCreated.model_validate(r.json())


def listed(client: TestClient) -> list[ClientToken]:
    r = client.get("/api/me/clients")
    assert r.status_code == 200, r.text
    return [ClientToken.model_validate(t) for t in r.json()]


def test_create_list_revoke(authed: TestClient, ctx: AppContext) -> None:
    created = create(authed, "  work laptop ")
    assert created.client.name == "work laptop"
    assert (created.client.last_seen_at, created.client.online) == (None, False)
    assert created.server_url == ctx.settings.public_url
    assert len(created.token) >= 43
    other = create(authed, "desktop")

    tokens = listed(authed)
    assert [t.name for t in tokens] == ["work laptop", "desktop"]
    assert created.token not in authed.get("/api/me/clients").text

    r = authed.delete(f"/api/me/clients/{created.client.id}", headers=CSRF)
    assert r.status_code == 204, r.text
    assert [t.id for t in listed(authed)] == [other.client.id]
    again = authed.delete(f"/api/me/clients/{created.client.id}", headers=CSRF)
    assert (again.status_code, again.json()["error"]["code"]) == (404, "not_found")


def test_only_your_own_tokens(client: TestClient, login: LoginFn) -> None:
    login("ann")
    ann = create(client)
    login("bob")
    assert listed(client) == []
    r = client.delete(f"/api/me/clients/{ann.client.id}", headers=CSRF)
    assert (r.status_code, r.json()["error"]["code"]) == (404, "not_found")
    login("ann")
    assert [t.id for t in listed(client)] == [ann.client.id]


def test_only_the_digest_is_stored(authed: TestClient, ctx: AppContext) -> None:
    created = create(authed)
    with ctx.db.read() as conn:
        row = dict(conn.execute("SELECT * FROM client_tokens").fetchone())
    assert row["token_hash"] == hashlib.sha256(created.token.encode()).hexdigest()
    assert created.token not in repr(row)


@pytest.mark.parametrize(
    ("method", "path"), [("POST", "/api/me/clients"), ("DELETE", "/api/me/clients/1")]
)
def test_csrf_is_required(authed: TestClient, method: str, path: str) -> None:
    r = authed.request(method, path, json={"name": "x"} if method == "POST" else None)
    assert (r.status_code, r.json()["error"]["code"]) == (403, "csrf_header_missing")


@pytest.mark.parametrize("name", ["", "   ", "x" * 81], ids=["empty", "blank", "too-long"])
def test_name_is_validated(authed: TestClient, name: str) -> None:
    r = authed.post("/api/me/clients", json={"name": name}, headers=CSRF)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "validation_error")
