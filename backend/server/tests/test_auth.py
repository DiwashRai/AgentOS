"""Dev auth, sessions, user admin and people (SPEC §10, §11)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentos.auth.deps import SESSION_COOKIE
from agentos.config import Settings
from agentos.context import AppContext
from agentos.main import create_app
from agentos.models import Me, Person, Role, User

CSRF = {"X-AgentOS": "1"}
BASE_URL = "http://127.0.0.1:8000"
LoginFn = Callable[..., Me]


@contextmanager
def started(
    settings: Settings, base_url: str = BASE_URL
) -> Iterator[tuple[TestClient, AppContext]]:
    """A separately configured app (the shared `client` fixture uses the default settings)."""
    app = create_app(settings)
    with TestClient(app, base_url=base_url) as c:
        ctx = app.state.ctx
        assert isinstance(ctx, AppContext)
        yield c, ctx


def rows(ctx: AppContext, sql: str, *params: Any) -> list[dict[str, Any]]:
    with ctx.db.read() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def execute(ctx: AppContext, sql: str, *params: Any) -> None:
    with ctx.db.tx() as conn:
        conn.execute(sql, params)


def post_login(client: TestClient, username: str, **headers: str) -> Any:
    return client.post("/api/auth/login", json={"username": username}, headers={**CSRF, **headers})


def error_code(response: Any) -> str:
    code: str = response.json()["error"]["code"]
    return code


# ------------------------------------------------------------------ login


def test_dev_login_creates_user_identity_and_hashed_session(
    client: TestClient, ctx: AppContext
) -> None:
    response = post_login(client, "  alice  ")
    assert response.status_code == 200
    me = Me.model_validate(response.json())
    assert (me.display_name, me.email, me.role) == ("alice", None, "admin")

    token = response.cookies[SESSION_COOKIE]
    sessions = rows(ctx, "SELECT * FROM sessions")
    assert len(sessions) == 1
    assert sessions[0]["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert sessions[0]["user_id"] == me.id
    assert token not in str(sessions)  # only the hash is stored
    assert rows(ctx, "SELECT provider, subject, user_id FROM user_identities") == [
        {"provider": "dev", "subject": "alice", "user_id": me.id}
    ]
    assert rows(ctx, "SELECT last_login_at FROM users")[0]["last_login_at"] is not None

    # Same (trimmed) username → same user, a second session.
    again = Me.model_validate(post_login(client, "alice").json())
    assert again.id == me.id
    assert len(rows(ctx, "SELECT * FROM sessions")) == 2


def test_dev_login_reapplies_admin(login: LoginFn, client: TestClient) -> None:
    alice = login("alice", "viewer")
    assert client.get("/api/me").json()["role"] == "viewer"
    assert login("alice").role == "admin"
    assert alice.id == login("alice").id


def test_session_cookie_flags(client: TestClient) -> None:
    cookie = post_login(client, "alice").headers["set-cookie"]
    parts = {p.strip().split("=")[0].lower() for p in cookie.split(";")}
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert {"httponly", "max-age", "path", "samesite"} <= parts
    assert "Max-Age=1209600" in cookie
    assert "Path=/" in cookie
    assert "samesite=lax" in cookie.lower()
    assert "secure" not in parts


def test_session_cookie_is_secure_with_https_public_url(settings: Settings) -> None:
    https = settings.model_copy(update={"public_url": "https://127.0.0.1:8443"})
    with started(https, base_url="https://127.0.0.1:8443") as (client, _):
        cookie = post_login(client, "alice").headers["set-cookie"]
    assert "secure" in {p.strip().split("=")[0].lower() for p in cookie.split(";")}


@pytest.mark.parametrize("body", [{}, {"username": ""}, {"username": "   "}, {"token": "x"}])
def test_dev_login_requires_username(client: TestClient, body: dict[str, str]) -> None:
    response = client.post("/api/auth/login", json=body, headers=CSRF)
    assert response.status_code == 400
    assert error_code(response) == "missing_credentials"


@pytest.mark.parametrize("username", ["x" * 65, "tab\there"])
def test_dev_login_rejects_bad_username(client: TestClient, username: str) -> None:
    response = post_login(client, username)
    assert response.status_code == 422
    assert error_code(response) == "validation_error"


def test_dev_login_accepts_64_chars(client: TestClient) -> None:
    assert post_login(client, "x" * 64).status_code == 200


def test_login_requires_csrf_header(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"username": "alice"})
    assert response.status_code == 403
    assert error_code(response) == "csrf_header_missing"


@pytest.mark.parametrize(
    "host", ["evil.example.com", "evil.example.com:8000", "10.0.0.5:8000", "0.0.0.0:8000", ""]
)
def test_dev_login_refuses_non_loopback_host(
    client: TestClient, ctx: AppContext, host: str
) -> None:
    response = post_login(client, "alice", host=host)
    assert response.status_code == 403
    assert error_code(response) == "forbidden"
    assert rows(ctx, "SELECT * FROM users") == []


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost:8000", "[::1]:8000", "127.0.0.2"])
def test_dev_login_accepts_loopback_hosts(client: TestClient, host: str) -> None:
    assert post_login(client, "alice", host=host).status_code == 200


@pytest.mark.parametrize("header", ["X-Forwarded-For", "CF-Connecting-IP", "x-forwarded-for"])
def test_dev_login_refuses_proxied_requests(client: TestClient, header: str) -> None:
    response = client.post(
        "/api/auth/login", json={"username": "alice"}, headers={**CSRF, header: "127.0.0.1"}
    )
    assert response.status_code == 403
    assert error_code(response) == "forbidden"


def test_jira_mode_login_is_not_implemented(settings: Settings) -> None:
    jira = settings.model_copy(
        update={"dev_auth": False, "jira_url": "https://jira.example.com", "jira_board_ids": [1]}
    )
    with started(jira) as (client, ctx):
        response = client.post("/api/auth/login", json={"token": "t"}, headers=CSRF)
        assert response.status_code == 501
        assert error_code(response) == "not_implemented"
        assert rows(ctx, "SELECT * FROM users") == []


# ------------------------------------------------------------------ sessions


def test_me_requires_a_session(client: TestClient) -> None:
    response = client.get("/api/me")
    assert response.status_code == 401
    assert error_code(response) == "unauthorized"


def test_unknown_session_is_rejected(client: TestClient) -> None:
    response = client.get("/api/me", headers={"Cookie": f"{SESSION_COOKIE}=forged"})
    assert response.status_code == 401


def test_me_returns_signed_in_user(login: LoginFn, client: TestClient) -> None:
    alice = login("alice")
    assert Me.model_validate(client.get("/api/me").json()) == alice


def test_logout_deletes_session(client: TestClient, ctx: AppContext) -> None:
    token = post_login(client, "alice").cookies[SESSION_COOKIE]
    response = client.post("/api/auth/logout", headers=CSRF)
    assert response.status_code == 204
    assert SESSION_COOKIE in response.headers["set-cookie"]
    assert rows(ctx, "SELECT * FROM sessions") == []
    assert client.get("/api/me").status_code == 401
    replayed = client.get("/api/me", headers={"Cookie": f"{SESSION_COOKIE}={token}"})
    assert replayed.status_code == 401


def test_logout_without_session_is_fine(client: TestClient) -> None:
    assert client.post("/api/auth/logout", headers=CSRF).status_code == 204


def test_expired_session_is_rejected_and_deleted(client: TestClient, ctx: AppContext) -> None:
    post_login(client, "alice")
    execute(ctx, "UPDATE sessions SET expires_at = '2000-01-01T00:00:00.000000+00:00'")
    response = client.get("/api/me")
    assert response.status_code == 401
    assert error_code(response) == "unauthorized"
    assert rows(ctx, "SELECT * FROM sessions") == []


def test_disabled_user_is_locked_out(login: LoginFn, client: TestClient, ctx: AppContext) -> None:
    alice = login("alice")
    execute(ctx, "UPDATE users SET disabled = 1 WHERE id = ?", alice.id)
    assert client.get("/api/me").status_code == 401  # existing session
    response = post_login(client, "alice")
    assert response.status_code == 403
    assert error_code(response) == "user_disabled"


# ------------------------------------------------------------------ users & people


def patch_user(client: TestClient, user_id: int, body: dict[str, object]) -> Any:
    return client.patch(f"/api/users/{user_id}", json=body, headers=CSRF)


def test_admin_lists_and_patches_users(login: LoginFn, client: TestClient) -> None:
    bob = login("bob")
    admin = login("admin")
    users = [User.model_validate(u) for u in client.get("/api/users").json()]
    assert [u.display_name for u in users] == ["admin", "bob"]

    response = patch_user(client, bob.id, {"role": "writer"})
    assert response.status_code == 200
    assert User.model_validate(response.json()).role == "writer"
    assert patch_user(client, admin.id, {"role": "admin"}).status_code == 200  # no-op on self


def test_patch_unknown_user_is_404(login: LoginFn, client: TestClient) -> None:
    login("admin")
    response = patch_user(client, 999, {"role": "writer"})
    assert response.status_code == 404
    assert error_code(response) == "user_not_found"


@pytest.mark.parametrize("body", [{"role": None}, {"disabled": None}, {"name": "x"}])
def test_patch_user_rejects_bad_bodies(
    login: LoginFn, client: TestClient, body: dict[str, object]
) -> None:
    bob = login("bob")
    login("admin")
    assert patch_user(client, bob.id, body).status_code == 422


@pytest.mark.parametrize("body", [{"role": "writer"}, {"role": "viewer"}, {"disabled": True}])
def test_admin_cannot_demote_or_disable_self(
    login: LoginFn, client: TestClient, body: dict[str, object]
) -> None:
    admin = login("admin")
    response = patch_user(client, admin.id, body)
    assert response.status_code == 422
    assert error_code(response) == "validation_error"
    assert client.get("/api/me").json()["role"] == "admin"


@pytest.mark.parametrize("entry", ["boss", "BOSS@example.com"])
def test_env_admin_cannot_be_demoted(settings: Settings, entry: str) -> None:
    with started(settings.model_copy(update={"admins": [entry]})) as (client, ctx):
        boss = Me.model_validate(post_login(client, "boss").json())
        execute(ctx, "UPDATE users SET email = 'boss@example.com' WHERE id = ?", boss.id)
        post_login(client, "admin")
        response = patch_user(client, boss.id, {"role": "writer"})
        assert response.status_code == 422
        assert error_code(response) == "validation_error"
        assert patch_user(client, boss.id, {"role": "admin"}).status_code == 200
        # Not demoting: disabling an env admin is allowed.
        assert patch_user(client, boss.id, {"disabled": True}).status_code == 200


def test_non_env_admin_can_be_demoted(settings: Settings) -> None:
    with started(settings.model_copy(update={"admins": ["someone-else"]})) as (client, _):
        bob = Me.model_validate(post_login(client, "bob").json())
        post_login(client, "admin")
        assert patch_user(client, bob.id, {"role": "viewer"}).status_code == 200


def test_disabling_a_user_deletes_their_sessions(
    client: TestClient, ctx: AppContext, login: LoginFn
) -> None:
    bob_token = post_login(client, "bob").cookies[SESSION_COOKIE]
    bob = Me.model_validate(client.get("/api/me").json())
    admin = login("admin")
    response = patch_user(client, bob.id, {"disabled": True})
    assert response.status_code == 200
    assert User.model_validate(response.json()).disabled is True
    assert rows(ctx, "SELECT user_id FROM sessions") == [{"user_id": admin.id}]
    replayed = client.get("/api/me", headers={"Cookie": f"{SESSION_COOKIE}={bob_token}"})
    assert replayed.status_code == 401
    # Re-enabling lets them sign in again.
    assert patch_user(client, bob.id, {"disabled": False}).status_code == 200
    assert post_login(client, "bob").status_code == 200


def test_people_lists_enabled_users_by_name(
    login: LoginFn, client: TestClient, ctx: AppContext
) -> None:
    zed = login("Zed")
    login("carol")
    amy = login("amy")
    execute(ctx, "UPDATE users SET disabled = 1 WHERE id = ?", zed.id)
    people = [Person.model_validate(p) for p in client.get("/api/people").json()]
    assert [p.display_name for p in people] == ["amy", "carol"]
    assert people[0] == Person(id=amy.id, display_name="amy", avatar_url=None)


@pytest.mark.parametrize("role", ["viewer", "writer"])
def test_people_is_open_to_every_role(login: LoginFn, client: TestClient, role: Role) -> None:
    login("alice", role)
    assert client.get("/api/people").status_code == 200
    assert client.get("/api/me").json()["role"] == role
