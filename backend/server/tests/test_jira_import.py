"""Jira import by key or link (contract request 11, SPEC §12, §17) against a mocked Jira: ref
parsing, the field mapping, created true/false, the Space's projects, Jira failures, config."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agentos.config import Settings
from agentos.context import AppContext
from agentos.models import ImportRequest, ImportResult, Me
from agentos.sources.jira import JiraClient, JiraNotFound, JiraSource
from agentos.tickets import service

CSRF = {"X-AgentOS": "1"}
JIRA = "https://jira.example.com"
SERVICE_EMAIL = "bot@example.com"
SERVICE_TOKEN = "svc-token-0123456789"  # noqa: S105 - a fake token for the mock
LoginFn = Callable[..., Me]


def issue(key: str = "SCRUM-6", **fields: Any) -> dict[str, Any]:
    return {
        "id": "10006",
        "key": key,
        "fields": {
            "summary": "Fix the login page",
            "description": "h2. Steps\n* open {{/login}}\n<!-- agentos:begin X -->",
            "issuetype": {"name": "Bug"},
            "priority": {"name": "High"},
            "labels": ["auth", "web"],
            **fields,
        },
    }


@dataclass
class FakeJira:
    """Issues by key; answers 401 unless the service account's Authorization header is sent."""

    issues: dict[str, dict[str, Any]] = field(default_factory=lambda: {"SCRUM-6": issue()})
    failure: Exception | int | None = None
    calls: list[httpx.Request] = field(default_factory=list)
    on_fetch: Callable[[], None] | None = None  # runs once, during the first issue fetch

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.on_fetch is not None:
            hook, self.on_fetch = self.on_fetch, None
            hook()
        if isinstance(self.failure, Exception):
            raise self.failure
        if isinstance(self.failure, int):
            return httpx.Response(self.failure)
        expected = JiraClient(JIRA, "cloud").credentials(token=SERVICE_TOKEN, email=SERVICE_EMAIL)
        if request.headers.get("authorization") != expected.authorization():
            return httpx.Response(401)
        key = request.url.path.removeprefix("/rest/api/2/issue/")
        if key in self.issues:
            return httpx.Response(200, json=self.issues[key])
        return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})

    def client(self) -> JiraClient:
        return JiraClient(JIRA, "cloud", transport=httpx.MockTransport(self.handler))


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Dev auth plus the Jira URL and service account: import works without Jira login."""
    return settings.model_copy(
        update={
            "jira_url": JIRA,
            "jira_projects": ["SCRUM"],
            "jira_service_email": SERVICE_EMAIL,
            "jira_service_token": SecretStr(SERVICE_TOKEN),
        }
    )


@pytest.fixture
def jira(ctx: AppContext) -> FakeJira:
    fake = FakeJira()
    ctx.jira = fake.client()
    return fake


def post(client: TestClient, ref: str) -> httpx.Response:
    response: httpx.Response = client.post("/api/tickets/import", json={"ref": ref}, headers=CSRF)
    return response


def error(response: httpx.Response) -> tuple[int, str]:
    code: str = response.json()["error"]["code"]
    return response.status_code, code


def source(jira: FakeJira, base: str = JIRA) -> JiraSource:
    client = JiraClient(base, "cloud", transport=httpx.MockTransport(jira.handler))
    return JiraSource(client, client.credentials(token=SERVICE_TOKEN, email=SERVICE_EMAIL))


# ------------------------------------------------------------------ ref parsing


@pytest.mark.parametrize(
    ("ref", "key"),
    [
        ("SCRUM-6", "SCRUM-6"),
        ("  scrum-6\n", "SCRUM-6"),
        (f"{JIRA}/browse/SCRUM-6", "SCRUM-6"),
        (f"{JIRA}/browse/scrum-6/", "SCRUM-6"),
        (f"{JIRA}/browse/SCRUM-6?atlOrigin=abc#comment-1", "SCRUM-6"),
        ("HTTPS://JIRA.EXAMPLE.COM:443/browse/SCRUM-6", "SCRUM-6"),
        (f"{JIRA}/jira/software/projects/SCRUM/boards/1?selectedIssue=SCRUM-6", "SCRUM-6"),
        ("SCRUM", None),
        ("SCRUM-", None),
        ("6", None),
        ("SCRUM-6 SCRUM-7", None),
        (f"{JIRA}/browse/", None),
        (f"{JIRA}/browse/SCRUM-6/extra", None),
        (f"{JIRA}/projects/SCRUM", None),
        ("https://evil.example/browse/SCRUM-6", None),
        ("https://jira.example.com.evil.net/browse/SCRUM-6", None),
        ("https://jira.example.com@evil.net/browse/SCRUM-6", None),
        ("https://jira.example.com:8443/browse/SCRUM-6", None),
        ("ftp://jira.example.com/browse/SCRUM-6", None),
        ("https://[::1/browse/SCRUM-6", None),
        ("https://evil.example/?selectedIssue=SCRUM-6", None),
    ],
)
def test_parse_ref(ref: str, key: str | None) -> None:
    assert source(FakeJira()).parse_ref(ref) == key


def test_parse_ref_respects_a_context_path() -> None:
    dc = source(FakeJira(), "https://jira.corp/jira")
    assert dc.parse_ref("https://jira.corp/jira/browse/OPS-1") == "OPS-1"
    assert dc.parse_ref("https://jira.corp/browse/OPS-1") is None


# ------------------------------------------------------------------ mapping


@pytest.mark.parametrize(
    ("jira_type", "jira_priority", "expected"),
    [
        ("Bug", "Highest", ("bug", "high")),
        ("Story", "Medium", ("feature", "normal")),
        ("Epic", "Blocker", ("feature", "high")),
        ("New Feature", "Critical", ("feature", "high")),
        ("Improvement", "Low", ("improvement", "low")),
        ("Task", "Lowest", ("task", "low")),
        ("Subtask", "Trivial", ("task", "low")),
        ("Sub-task", "Minor", ("task", "low")),
        ("Spike", "Major", ("task", "normal")),
        ("Task", None, ("task", "normal")),
    ],
)
def test_type_and_priority_mapping(
    jira_type: str, jira_priority: str | None, expected: tuple[str, str]
) -> None:
    jira = FakeJira()
    priority = {"name": jira_priority} if jira_priority else None
    jira.issues["SCRUM-6"] = issue(issuetype={"name": jira_type}, priority=priority)
    ticket = source(jira).fetch("SCRUM-6")
    assert (ticket.type, ticket.priority) == expected


def test_fetch_maps_the_issue_and_asks_only_for_its_fields() -> None:
    jira = FakeJira()
    ticket = source(jira).fetch("SCRUM-6")
    assert ticket.title == "Fix the login page"
    assert ticket.labels == ["auth", "web"]
    assert (ticket.external_id, ticket.project, ticket.url) == (
        "SCRUM-6",
        "SCRUM",
        f"{JIRA}/browse/SCRUM-6",
    )
    (request,) = jira.calls
    assert request.url.path == "/rest/api/2/issue/SCRUM-6"
    assert request.url.params["fields"] == "summary,description,issuetype,priority,labels"
    with pytest.raises(JiraNotFound):
        source(jira).fetch("SCRUM-9")


# ------------------------------------------------------------------ the endpoint


def test_import_creates_a_ticket_the_importer_owns(
    client: TestClient, login: LoginFn, jira: FakeJira
) -> None:
    me = login("ana", "writer")
    response = post(client, f"{JIRA}/browse/SCRUM-6")
    assert response.status_code == 201, response.text
    result = ImportResult.model_validate(response.json())
    t = result.ticket
    assert result.created
    assert (t.key, t.title, t.type, t.priority, t.labels) == (
        "SCRUM-6",
        "Fix the login page",
        "bug",
        "high",
        ["auth", "web"],
    )
    # Imported text is stored verbatim, as data (a marker-like line included).
    assert t.description == jira.issues["SCRUM-6"]["fields"]["description"]
    assert t.source is not None
    assert t.source.model_dump() == {
        "source": "jira",
        "external_id": "SCRUM-6",
        "url": f"{JIRA}/browse/SCRUM-6",
    }
    assert t.owner is not None
    assert t.owner.id == me.id
    assert (t.repo_name, t.base_branch, t.attention) == ("r", "main", "inbox")
    assert t.plan is not None  # the template's scaffold is bundle v1, as for a native ticket
    assert client.get("/api/tickets/SCRUM-6/files").json()


def test_reimport_returns_the_ticket_without_calling_jira(
    authed: TestClient, jira: FakeJira
) -> None:
    first = post(authed, "SCRUM-6")
    assert first.status_code == 201
    calls = len(jira.calls)
    again = post(authed, f"{JIRA}/browse/scrum-6?focusedCommentId=1")
    assert again.status_code == 200, again.text
    assert (again.json()["created"], again.json()["ticket"]["key"]) == (False, "SCRUM-6")
    assert len(jira.calls) == calls
    assert [t["key"] for t in authed.get("/api/tickets").json()] == ["SCRUM-6"]


def test_concurrent_imports_end_with_one_ticket(
    authed: TestClient, ctx: AppContext, jira: FakeJira
) -> None:
    """Another import commits while this one waits on Jira (no write transaction is held during
    the fetch, or the inner import would block): this one returns that ticket."""
    me = Me.model_validate(authed.get("/api/me").json())

    def import_meanwhile() -> None:
        body = ImportRequest(ref="SCRUM-6")
        inner = service.import_ticket(ctx.db, ctx.settings, ctx.space, ctx.jira_source, me, body)
        assert inner.created

    jira.on_fetch = import_meanwhile
    response = post(authed, "SCRUM-6")
    assert response.status_code == 200, response.text
    assert response.json()["created"] is False
    with ctx.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1


def test_imported_fields_fit_the_limits(authed: TestClient, jira: FakeJira) -> None:
    labels = ["ok", "ok", " spaced ", "x" * 51, *(f"l{i}" for i in range(30))]
    jira.issues["SCRUM-7"] = issue(
        "SCRUM-7", summary="  " + "T" * 250, description=None, labels=labels
    )
    jira.issues["SCRUM-8"] = issue("SCRUM-8", summary="   ")
    t = post(authed, "SCRUM-7").json()["ticket"]
    assert t["title"] == "T" * 200
    assert t["description"] == ""
    assert t["labels"] == ["ok", "spaced", *(f"l{i}" for i in range(18))]
    assert post(authed, "SCRUM-8").json()["ticket"]["title"] == "SCRUM-8"


@pytest.mark.parametrize(
    "ref", ["", "not a key", "https://evil.example/browse/SCRUM-6", "OPS-1", "x" * 2001]
)
def test_bad_refs_are_rejected_before_calling_jira(
    authed: TestClient, jira: FakeJira, ref: str
) -> None:
    assert error(post(authed, ref)) == (422, "validation_error")
    assert jira.calls == []


def test_a_moved_issue_outside_the_space_is_rejected(authed: TestClient, jira: FakeJira) -> None:
    jira.issues["SCRUM-6"] = issue("OPS-3")  # Jira answers with the issue's new key
    response = post(authed, "SCRUM-6")
    assert error(response) == (422, "validation_error")
    assert "OPS-3" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (404, (404, "ticket_not_found")),
        (403, (404, "ticket_not_found")),
        (401, (502, "upstream_error")),
        (500, (502, "upstream_error")),
        (httpx.ConnectError("refused"), (502, "upstream_error")),
        (httpx.ReadTimeout("slow"), (502, "upstream_error")),
    ],
)
def test_jira_failures(
    authed: TestClient, jira: FakeJira, failure: Exception | int, expected: tuple[int, str]
) -> None:
    jira.failure = failure
    response = post(authed, "SCRUM-6")
    assert error(response) == expected
    assert SERVICE_TOKEN not in response.text


def test_unknown_issue_names_it(authed: TestClient, jira: FakeJira) -> None:
    response = post(authed, "SCRUM-9")
    assert error(response) == (404, "ticket_not_found")
    assert "SCRUM-9" in response.json()["error"]["message"]


def test_rejected_service_account_is_named(
    authed: TestClient, ctx: AppContext, jira: FakeJira
) -> None:
    ctx.settings = ctx.settings.model_copy(update={"jira_service_token": SecretStr("wrong-token")})
    response = post(authed, "SCRUM-6")
    assert error(response) == (502, "upstream_error")
    assert "service account" in response.json()["error"]["message"]
    assert "wrong-token" not in response.text


def test_key_taken_by_another_ticket(
    authed: TestClient, ctx: AppContext, jira: FakeJira, create_ticket: Callable[..., Any]
) -> None:
    native = create_ticket("Native")
    with ctx.db.tx() as conn:  # e.g. an import from another source that used the same key
        conn.execute("UPDATE tickets SET key = 'SCRUM-6' WHERE key = ?", (native.key,))
    assert error(post(authed, "SCRUM-6")) == (409, "key_taken")


def test_not_configured_is_501_and_config_lists_sources(
    authed: TestClient, ctx: AppContext, jira: FakeJira
) -> None:
    assert authed.get("/api/config").json()["sources"] == ["jira"]
    ctx.settings = ctx.settings.model_copy(update={"jira_service_token": None})
    assert authed.get("/api/config").json()["sources"] == []
    assert error(post(authed, "SCRUM-6")) == (501, "not_implemented")
    assert jira.calls == []


def test_viewer_cannot_import(client: TestClient, login: LoginFn, jira: FakeJira) -> None:
    login("viewer", "viewer")
    assert error(post(client, "SCRUM-6")) == (403, "forbidden")
    assert jira.calls == []
