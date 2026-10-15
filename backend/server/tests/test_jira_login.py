"""Jira login (SPEC §11, contract request 10) against a mocked Jira: credentials via `/myself`,
access via the Space's projects, `pending` until an admin approves, the login rate limit."""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agentos.auth.deps import SESSION_COOKIE
from agentos.config import Settings
from agentos.context import AppContext
from agentos.main import create_app
from agentos.models import Me
from agentos.sources.jira import JiraClient, JiraCredentials
from tests.run_support import FakeLauncher
from tests.settings_support import set_space

CSRF = {"X-AgentOS": "1"}
BASE_URL = "http://127.0.0.1:8000"
JIRA = "https://jira.example.com"
TOKEN = "tok-ana-0123456789"  # noqa: S105 - a fake token for the mock
_PROJECT = re.compile(r"/rest/api/2/project/([^/]+)")


@dataclass
class Account:
    myself: dict[str, Any]
    projects: set[str] = field(default_factory=lambda: {"SCRUM"})


@dataclass
class FakeJira:
    """Accounts keyed by the exact Authorization header they authenticate with."""

    accounts: dict[str, Account] = field(default_factory=dict)
    failure: Exception | int | None = None  # raise this / answer every request with this status
    calls: list[httpx.Request] = field(default_factory=list)

    def add(self, auth: str, account_id: str, *, email: str | None = None, **kw: Any) -> Account:
        account = Account(
            {
                "accountId": account_id,
                "key": account_id,
                "displayName": kw.pop("name", account_id.title()),
                "emailAddress": email,
                "avatarUrls": {"48x48": f"https://avatars.example/{account_id}.png"},
            },
            **kw,
        )
        self.accounts[auth] = account
        return account

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if isinstance(self.failure, Exception):
            raise self.failure
        if isinstance(self.failure, int):
            return httpx.Response(self.failure)
        account = self.accounts.get(request.headers.get("authorization", ""))
        if account is None:
            return httpx.Response(401, json={"errorMessages": ["unauthorized"]})
        if request.url.path == "/rest/api/2/myself":
            return httpx.Response(200, json=account.myself)
        if (m := _PROJECT.fullmatch(request.url.path)) and m.group(1) in account.projects:
            return httpx.Response(200, json={"key": m.group(1)})
        return httpx.Response(404)


def basic(email: str, token: str = TOKEN) -> str:
    return JiraCredentials(token=token, email=email).authorization()


@pytest.fixture
def fake_jira() -> FakeJira:
    jira = FakeJira()
    jira.add(basic("ana@example.com"), "acc-ana", email="ana@example.com", name="Ana")
    return jira


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Jira login instead of dev auth."""
    return settings.model_copy(
        update={
            "dev_auth": False,
            "jira_url": JIRA,
            "admins": ["acc-boss", "Chief@Example.com"],
        }
    )


@pytest.fixture
def ctx(ctx: AppContext, fake_jira: FakeJira) -> AppContext:
    """The test's context with the mocked Jira and the Space's projects SCRUM and OPS."""
    assert ctx.jira is not None  # built from AGENTOS_JIRA_URL
    ctx.jira = JiraClient(
        JIRA, ctx.settings.jira_deployment, transport=httpx.MockTransport(fake_jira.handler)
    )
    set_space(ctx, jira_projects=("SCRUM", "OPS"))
    return ctx


def login(client: TestClient, body: dict[str, str], **headers: str) -> Any:
    return client.post("/api/auth/login", json=body, headers={**CSRF, **headers})


def ana(client: TestClient, token: str = TOKEN) -> Any:
    return login(client, {"email": "ana@example.com", "token": token})


def code(response: Any) -> str:
    result: str = response.json()["error"]["code"]
    return result


def rows(ctx: AppContext, sql: str, *params: Any) -> list[dict[str, Any]]:
    with ctx.db.read() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def set_role(ctx: AppContext, user_id: int, role: str) -> None:
    with ctx.db.tx() as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


# ------------------------------------------------------------------ outcomes


def test_first_login_is_pending_with_a_session(client: TestClient, ctx: AppContext) -> None:
    response = ana(client)
    assert response.status_code == 200, response.text
    assert SESSION_COOKIE in response.cookies
    me = Me.model_validate(response.json())
    assert (me.display_name, me.email, me.role) == ("Ana", "ana@example.com", "pending")
    assert me.avatar_url == "https://avatars.example/acc-ana.png"
    assert rows(ctx, "SELECT provider, subject FROM user_identities") == [
        {"provider": "jira", "subject": "acc-ana"}
    ]
    # Only /api/me works until an admin approves.
    assert client.get("/api/me").json()["role"] == "pending"
    blocked = client.get("/api/tickets")
    assert (blocked.status_code, code(blocked)) == (403, "pending_approval")


def test_login_checks_the_space_projects_with_the_same_credentials(
    client: TestClient, fake_jira: FakeJira
) -> None:
    fake_jira.accounts[basic("ana@example.com")].projects = {"OPS"}  # the second one is enough
    assert ana(client).status_code == 200
    paths = [r.url.path for r in fake_jira.calls]
    assert paths == ["/rest/api/2/myself", "/rest/api/2/project/SCRUM", "/rest/api/2/project/OPS"]
    assert {r.headers["authorization"] for r in fake_jira.calls} == {basic("ana@example.com")}


@pytest.mark.parametrize(
    "body",
    [{}, {"token": TOKEN}, {"email": "ana@example.com"}, {"email": " ", "token": TOKEN}],
)
def test_missing_credentials(client: TestClient, fake_jira: FakeJira, body: dict[str, str]) -> None:
    response = login(client, body)
    assert (response.status_code, code(response)) == (400, "missing_credentials")
    assert fake_jira.calls == []


@pytest.mark.parametrize("token", ["wrong-token", "töken", "has space", "x" * 1025])
def test_bad_credentials(client: TestClient, ctx: AppContext, token: str) -> None:
    response = ana(client, token)
    assert (response.status_code, code(response)) == (401, "invalid_credentials")
    assert rows(ctx, "SELECT * FROM users") == []


def test_no_project_access(client: TestClient, ctx: AppContext, fake_jira: FakeJira) -> None:
    fake_jira.accounts[basic("ana@example.com")].projects = {"OTHER"}
    response = ana(client)
    assert (response.status_code, code(response)) == (403, "no_project_access")
    assert "SCRUM, OPS" in response.json()["error"]["message"]
    assert rows(ctx, "SELECT * FROM users") == []


def test_the_project_check_uses_the_saved_projects(
    client: TestClient, ctx: AppContext, fake_jira: FakeJira
) -> None:
    """Settings > Space applies at once: the next login checks the new projects."""
    fake_jira.accounts[basic("ana@example.com")].projects = {"OTHER"}
    assert ana(client).status_code == 403
    set_space(ctx, jira_projects=("OTHER",))
    assert ana(client).status_code == 200
    assert [r.url.path for r in fake_jira.calls][-1] == "/rest/api/2/project/OTHER"


def test_no_projects_yet_refuses_everyone_but_env_admins(
    client: TestClient, ctx: AppContext, fake_jira: FakeJira
) -> None:
    set_space(ctx, jira_projects=())
    response = ana(client)
    assert (response.status_code, code(response)) == (403, "no_project_access")
    assert "no Jira projects" in response.json()["error"]["message"]
    fake_jira.add(basic("boss@example.com"), "acc-boss", email="boss@example.com")
    assert login(client, {"email": "boss@example.com", "token": TOKEN}).json()["role"] == "admin"


def test_env_admins_skip_the_project_check(
    client: TestClient, ctx: AppContext, fake_jira: FakeJira
) -> None:
    """A bad `jira_projects` save can't lock the AGENTOS_ADMINS people out: a valid token is
    enough (no project call at all)."""
    fake_jira.add(basic("boss@example.com"), "acc-boss", email="boss@example.com", projects=set())
    response = login(client, {"email": "boss@example.com", "token": TOKEN})
    assert (response.status_code, response.json()["role"]) == (200, "admin")
    assert [r.url.path for r in fake_jira.calls] == ["/rest/api/2/myself"]
    bad = login(client, {"email": "boss@example.com", "token": "wrong-token"})
    assert (bad.status_code, code(bad)) == (401, "invalid_credentials")


@pytest.mark.parametrize(
    "failure", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), 503, 500]
)
def test_jira_down_is_upstream_error(
    client: TestClient, fake_jira: FakeJira, failure: Exception | int
) -> None:
    fake_jira.failure = failure
    response = ana(client)
    assert (response.status_code, code(response)) == (502, "upstream_error")


def test_disabled_user_is_refused(client: TestClient, ctx: AppContext) -> None:
    user_id = ana(client).json()["id"]
    with ctx.db.tx() as conn:
        conn.execute("UPDATE users SET disabled = 1 WHERE id = ?", (user_id,))
    response = ana(client)
    assert (response.status_code, code(response)) == (403, "user_disabled")


@pytest.mark.parametrize(
    ("account_id", "email"),
    [("acc-boss", None), ("acc-chief", "chief@example.com"), ("acc-chief", "CHIEF@example.COM")],
)
def test_admins_match_the_myself_response(
    client: TestClient, ctx: AppContext, fake_jira: FakeJira, account_id: str, email: str | None
) -> None:
    fake_jira.add(basic("x@example.com"), account_id, email=email)
    response = login(client, {"email": "x@example.com", "token": TOKEN})
    me = Me.model_validate(response.json())
    assert me.role == "admin"
    # Re-applied on every login, even after a demotion in the db.
    set_role(ctx, me.id, "viewer")
    assert login(client, {"email": "x@example.com", "token": TOKEN}).json()["role"] == "admin"


def test_admins_never_match_request_input(client: TestClient, fake_jira: FakeJira) -> None:
    # The submitted email is an admin's, but Jira says this token belongs to someone else.
    fake_jira.add(basic("chief@example.com"), "acc-imposter", email="imp@example.com")
    response = login(client, {"email": "chief@example.com", "token": TOKEN})
    assert response.json()["role"] == "pending"


def test_existing_user_keeps_role_and_profile_is_refreshed(
    client: TestClient, ctx: AppContext, fake_jira: FakeJira
) -> None:
    user_id = ana(client).json()["id"]
    set_role(ctx, user_id, "admin")  # promoted in the UI: never demoted by a login
    account = fake_jira.accounts[basic("ana@example.com")]
    account.myself |= {"displayName": "Ana B", "avatarUrls": {"48x48": "https://a.example/2"}}
    me = Me.model_validate(ana(client).json())
    assert (me.id, me.role, me.display_name, me.avatar_url) == (
        user_id,
        "admin",
        "Ana B",
        "https://a.example/2",
    )


def test_token_is_never_stored_or_logged(
    client: TestClient, ctx: AppContext, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    assert ana(client).status_code == 200
    assert ana(client, "wrong-token").status_code == 401
    encoded = base64.b64encode(f"ana@example.com:{TOKEN}".encode()).decode()
    tables = ("SELECT * FROM users", "SELECT * FROM user_identities", "SELECT * FROM sessions")
    dump = str([rows(ctx, sql) for sql in tables])
    for secret in (TOKEN, encoded):
        assert secret not in dump
        assert secret not in caplog.text
    assert repr(JiraCredentials(token=TOKEN, email="a")).count(TOKEN) == 0


# ------------------------------------------------------------------ approval


def test_admin_approves_and_can_send_back_to_pending(
    client: TestClient, ctx: AppContext, fake_jira: FakeJira
) -> None:
    ana_token = ana(client).cookies[SESSION_COOKIE]
    ana_id = client.get("/api/me").json()["id"]
    fake_jira.add(basic("boss@example.com"), "acc-boss", email="boss@example.com")
    assert login(client, {"email": "boss@example.com", "token": TOKEN}).json()["role"] == "admin"

    as_ana = {"Cookie": f"{SESSION_COOKIE}={ana_token}"}
    for role, status in (("writer", 200), ("pending", 403), ("viewer", 200)):
        response = client.patch(f"/api/users/{ana_id}", json={"role": role}, headers=CSRF)
        assert response.json()["role"] == role
        assert client.get("/api/people", headers=as_ana).status_code == status  # next request


# ------------------------------------------------------------------ rate limit


def test_rate_limit_per_ip_and_email(client: TestClient) -> None:
    for _ in range(10):
        assert ana(client, "wrong-token").status_code == 401
    limited = ana(client)  # even the right token
    assert (limited.status_code, code(limited)) == (429, "rate_limited")
    other = login(client, {"email": "bob@example.com", "token": TOKEN})
    assert other.status_code == 401  # another email has its own budget
    # Without AGENTOS_TRUST_PROXY, proxy headers don't change the key.
    for header in ("X-Forwarded-For", "CF-Connecting-IP"):
        spoofed = login(
            client, {"email": "ana@example.com", "token": TOKEN}, **{header: "10.9.9.9"}
        )
        assert spoofed.status_code == 429


def test_rate_limit_trusts_proxy_headers_only_when_configured(
    client: TestClient, ctx: AppContext
) -> None:
    ctx.settings = ctx.settings.model_copy(update={"trust_proxy": True})
    for _ in range(10):
        login(
            client, {"email": "ana@example.com", "token": "bad"}, **{"CF-Connecting-IP": "1.1.1.1"}
        )
    blocked = login(
        client, {"email": "ana@example.com", "token": TOKEN}, **{"CF-Connecting-IP": "1.1.1.1"}
    )
    assert blocked.status_code == 429
    other_ip = login(
        client,
        {"email": "ana@example.com", "token": TOKEN},
        **{"X-Forwarded-For": "6.6.6.6, 2.2.2.2"},
    )
    assert other_ip.status_code == 200


# ------------------------------------------------------------------ Data Center


def test_datacenter_uses_a_bearer_pat_and_the_user_key(
    settings: Settings, fake_jira: FakeJira, launcher: FakeLauncher
) -> None:
    dc = settings.model_copy(update={"jira_deployment": "datacenter"})
    fake_jira.add(f"Bearer {TOKEN}", "jsmith", email="j@example.com")
    app = create_app(dc, launcher=launcher, run_loops=False)
    with TestClient(app, base_url=BASE_URL) as client:
        ctx = app.state.ctx
        ctx.jira = JiraClient(JIRA, "datacenter", transport=httpx.MockTransport(fake_jira.handler))
        set_space(ctx, jira_projects=("SCRUM",))
        assert (r := login(client, {"email": "ignored", "token": TOKEN})).status_code == 200, r.text
        assert login(client, {}).status_code == 400
        assert rows(ctx, "SELECT subject FROM user_identities") == [{"subject": "jsmith"}]
