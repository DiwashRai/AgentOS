"""Intake candidates (contract request 14) against a mocked Jira: the JQL search and its mapping,
the already-imported filter, cap and total, per-query errors, the cache and auth."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, TypeAdapter

from agentos.config import Settings
from agentos.context import AppContext
from agentos.intake import service
from agentos.models import IntakeQuery, Me
from agentos.pipeline.space import IntakeQuerySpec
from agentos.sources.jira import JiraBadQuery, JiraClient, JiraCredentials, JiraSource
from agentos.ttlcache import TtlCache
from tests.settings_support import set_queries, set_space

CSRF = {"X-AgentOS": "1"}
JIRA = "https://jira.example.com"
SERVICE_EMAIL = "bot@example.com"
SERVICE_TOKEN = "svc-token-0123456789"  # noqa: S105 - a fake token for the mock
ORDER = " ORDER BY updated DESC"
T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
LoginFn = Callable[..., Me]


def issue(key: str, minutes_ago: int = 0, **fields: Any) -> dict[str, Any]:
    updated = (T0 - timedelta(minutes=minutes_ago)).isoformat(timespec="milliseconds")
    return {
        "id": "1",
        "key": key,
        "fields": {
            "summary": f"Summary of {key}",
            "description": "h2. Steps",  # served by the issue GET (import), ignored by search
            "issuetype": {"name": "Story"},
            "priority": {"name": "Medium"},
            "status": {"name": "To Do", "statusCategory": {"key": "new"}},
            "assignee": None,
            "updated": updated.replace("+00:00", "+0000"),  # Jira's offset style
            "labels": [],
            **fields,
        },
    }


@dataclass
class FakeJira:
    """Search results by JQL (as configured, without the appended ORDER BY), the issue GET for
    imports, failures by JQL, and the JQL parse check's errors by JQL. Answers 401 unless the
    service account is used."""

    results: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    failures: dict[str, Exception | httpx.Response] = field(default_factory=dict)
    invalid: dict[str, str] = field(default_factory=dict)
    approximate: dict[str, int] = field(default_factory=dict)
    calls: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        expected = JiraClient(JIRA, "cloud").credentials(token=SERVICE_TOKEN, email=SERVICE_EMAIL)
        if request.headers.get("authorization") != expected.authorization():
            return httpx.Response(401)
        path = request.url.path
        if request.method == "GET" and path.startswith("/rest/api/2/issue/"):
            key = path.removeprefix("/rest/api/2/issue/")
            found = [i for issues in self.results.values() for i in issues if i["key"] == key]
            return httpx.Response(200, json=found[0]) if found else httpx.Response(404)
        body = json.loads(request.content)
        if path == "/rest/api/3/jql/parse":
            assert request.url.params["validation"] == "strict"
            [query] = body["queries"]
            error = self.invalid.get(query.removesuffix(ORDER))
            return httpx.Response(
                200, json={"queries": [{"query": query, **({"errors": [error]} if error else {})}]}
            )
        jql = body["jql"].removesuffix(ORDER)
        failure = self.failures.get(jql)
        if isinstance(failure, Exception):
            raise failure
        if failure is not None:
            return failure
        matches = self.results.get(jql, [])
        if path == "/rest/api/3/search/approximate-count":
            return httpx.Response(200, json={"count": self.approximate.get(jql, len(matches))})
        assert path == "/rest/api/3/search/jql", path
        page = matches[: body["maxResults"]]
        last = len(page) == len(matches)
        return httpx.Response(
            200,
            json={"issues": page, "isLast": last, **({} if last else {"nextPageToken": "t"})},
        )

    def searches(self) -> list[dict[str, Any]]:
        return [json.loads(c.content) for c in self.calls if c.url.path == "/rest/api/3/search/jql"]

    def client(self) -> JiraClient:
        return JiraClient(JIRA, "cloud", transport=httpx.MockTransport(self.handler))


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "jira_url": JIRA,
            "jira_service_email": SERVICE_EMAIL,
            "jira_service_token": SecretStr(SERVICE_TOKEN),
        }
    )


@pytest.fixture
def clock(ctx: AppContext) -> Clock:
    fake = Clock()
    ctx.intake_cache = TtlCache(45.0, clock=fake)
    return fake


@pytest.fixture
def jira(ctx: AppContext, clock: Clock) -> FakeJira:
    del clock  # every test gets a fake-clock cache
    set_space(ctx, jira_projects=("SCRUM",))
    fake = FakeJira()
    ctx.jira = fake.client()
    return fake


def configure(ctx: AppContext, *queries: tuple[str, str]) -> None:
    """Set the Space's intake queries: (id, jql) pairs, named after their id."""
    set_queries(ctx, [IntakeQuerySpec(id=i, name=f"Query {i}", jql=jql) for i, jql in queries])


def get_intake(client: TestClient, *, fresh: bool = False) -> list[IntakeQuery]:
    response = client.get("/api/intake", params={"fresh": "1"} if fresh else None)
    assert response.status_code == 200, response.text
    assert SERVICE_TOKEN not in response.text
    return TypeAdapter(list[IntakeQuery]).validate_python(response.json())


# ------------------------------------------------------------------ the endpoint


def test_candidates_are_mapped_and_newest_first(
    authed: TestClient, ctx: AppContext, jira: FakeJira
) -> None:
    configure(ctx, ("todo", "project = SCRUM"))
    jira.results["project = SCRUM"] = [
        issue("SCRUM-2", 30),
        issue(
            "SCRUM-6",
            5,
            issuetype={"name": "Bug"},
            priority={"name": "Highest"},
            status={"name": "In Progress"},
            assignee={"displayName": "Ana Lee", "accountId": "abc"},
            summary="  Fix login  ",
        ),
        issue("SCRUM-3", 10, priority=None, status=None, summary=""),
    ]
    (query,) = get_intake(authed)
    assert (query.id, query.name, query.jql, query.total, query.error) == (
        "todo",
        "Query todo",
        "project = SCRUM",
        3,
        None,
    )
    assert [i.key for i in query.items] == ["SCRUM-6", "SCRUM-3", "SCRUM-2"]
    first = query.items[0]
    assert first.model_dump(exclude={"updated_at"}) == {
        "key": "SCRUM-6",
        "title": "Fix login",
        "type": "bug",
        "priority": "high",
        "status": "In Progress",
        "assignee": {"display_name": "Ana Lee"},
        "url": f"{JIRA}/browse/SCRUM-6",
    }
    assert first.updated_at == T0 - timedelta(minutes=5)
    third = query.items[1]
    assert (third.title, third.type, third.priority, third.status, third.assignee) == (
        "SCRUM-3",
        "feature",
        "normal",
        "",
        None,
    )
    (search,) = jira.searches()
    assert search["jql"] == "project = SCRUM" + ORDER
    assert search["maxResults"] == service.FETCH_LIMIT
    assert search["fields"] == ["summary", "issuetype", "priority", "status", "assignee", "updated"]


def test_own_order_by_is_kept(authed: TestClient, ctx: AppContext, jira: FakeJira) -> None:
    configure(ctx, ("p", "project = SCRUM order  by priority"))
    get_intake(authed)
    assert jira.searches()[0]["jql"] == "project = SCRUM order  by priority"


def test_imported_and_foreign_issues_are_not_candidates(
    authed: TestClient, ctx: AppContext, jira: FakeJira
) -> None:
    configure(ctx, ("todo", "project = SCRUM"))
    jira.results["project = SCRUM"] = [
        issue("SCRUM-2", 1),
        issue("SCRUM-3", 2),
        issue("OPS-1", 3),  # outside the Space's projects: import would refuse it
    ]
    assert [i.key for i in get_intake(authed)[0].items] == ["SCRUM-2", "SCRUM-3"]
    # Take in SCRUM-2: it leaves the list at once, although Jira's answer is cached.
    taken = authed.post("/api/tickets/import", json={"ref": "SCRUM-2"}, headers=CSRF)
    assert taken.status_code == 201, taken.text
    calls = len(jira.searches())
    (query,) = get_intake(authed)
    assert len(jira.searches()) == calls
    assert ([i.key for i in query.items], query.total) == (["SCRUM-3"], 1)


def test_a_ticket_with_the_key_hides_the_issue(
    authed: TestClient, ctx: AppContext, jira: FakeJira, create_ticket: Callable[..., Any]
) -> None:
    configure(ctx, ("todo", "project = SCRUM"))
    jira.results["project = SCRUM"] = [issue("SCRUM-4")]
    native = create_ticket("Native")
    with ctx.db.tx() as conn:  # e.g. imported from another source under the same key
        conn.execute("UPDATE tickets SET key = 'SCRUM-4' WHERE key = ?", (native.key,))
    assert get_intake(authed)[0].items == []


def test_cap_and_total(authed: TestClient, ctx: AppContext, jira: FakeJira) -> None:
    """130 matches: the newest 100 are read, 3 of them are imported; 50 shown, total estimated
    from Jira's approximate count minus the 3."""
    configure(ctx, ("all", "project = SCRUM"))
    jira.results["project = SCRUM"] = [issue(f"SCRUM-{n}", n) for n in range(1, 131)]
    for n in (1, 2, 99):
        taken = authed.post("/api/tickets/import", json={"ref": f"SCRUM-{n}"}, headers=CSRF)
        assert taken.status_code == 201, taken.text
    (query,) = get_intake(authed)
    assert len(query.items) == service.ITEM_CAP
    assert query.items[0].key == "SCRUM-3"
    assert query.total == 127
    jira.approximate["project = SCRUM"] = 90  # a lagging estimate never undercounts the window
    ctx.intake_cache.clear()
    assert get_intake(authed)[0].total == 97


def test_a_failing_query_does_not_affect_the_others(
    authed: TestClient, ctx: AppContext, jira: FakeJira
) -> None:
    configure(
        ctx,
        ("good", "project = SCRUM"),
        ("bad", "project = = SCRUM"),
        ("down", "down"),
        ("slow", "slow"),
        ("auth", "auth"),
    )
    jira.results["project = SCRUM"] = [issue("SCRUM-2")]
    message = "Error in JQL Query: Expecting either a value, list or function but got '='."
    jira.failures["project = = SCRUM"] = httpx.Response(
        400, json={"errorMessages": [message], "errors": {}}
    )
    jira.failures["down"] = httpx.Response(503)
    jira.failures["slow"] = httpx.ReadTimeout("slow")
    jira.failures["auth"] = httpx.Response(401)
    good, bad, down, slow, auth = get_intake(authed)
    assert ([i.key for i in good.items], good.error) == (["SCRUM-2"], None)
    for failed in (bad, down, slow, auth):
        assert (failed.items, failed.total) == ([], 0)
    assert bad.error == f"Jira rejected the query: {message}"
    assert down.error == slow.error == service.UNAVAILABLE
    assert auth.error == service.UNAUTHORIZED


def test_cloud_explains_an_empty_answer_with_the_jql_check(
    authed: TestClient, ctx: AppContext, jira: FakeJira
) -> None:
    """Cloud's search answers an unknown field, project or value with no issues, not a 400."""
    configure(ctx, ("bad", "project = SCRUM AND nosuchfield = 1"), ("empty", "project = SCRUM"))
    message = "Field 'nosuchfield' does not exist or you do not have permission to view it."
    jira.invalid["project = SCRUM AND nosuchfield = 1"] = message
    bad, empty = get_intake(authed)
    assert (bad.items, bad.total, bad.error) == ([], 0, f"Jira rejected the query: {message}")
    assert (empty.items, empty.total, empty.error) == ([], 0, None)
    parsed = [c for c in jira.calls if c.url.path == "/rest/api/3/jql/parse"]
    assert len(parsed) == 2  # only for the empty answers


def test_jql_check_is_best_effort() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/api/3/jql/parse":
            return httpx.Response(503)
        return httpx.Response(200, json={"issues": [], "isLast": True})

    client = JiraClient(JIRA, "cloud", transport=httpx.MockTransport(handler))
    creds = client.credentials(token=SERVICE_TOKEN, email=SERVICE_EMAIL)
    page = client.search(creds, "q", ("summary",), 1)
    assert (page.issues, page.count, page.complete) == ([], 0, True)


def test_fresh_refetches_unless_just_fetched(
    authed: TestClient, ctx: AppContext, jira: FakeJira, clock: Clock
) -> None:
    configure(ctx, ("a", "project = SCRUM"))
    jira.results["project = SCRUM"] = [issue("SCRUM-2")]
    get_intake(authed)
    assert len(jira.searches()) == 1
    jira.results["project = SCRUM"] = [issue("SCRUM-2"), issue("SCRUM-3")]
    clock.now += 4
    [a] = get_intake(authed, fresh=True)
    assert len(jira.searches()) == 1  # fetched under FRESH_MIN_AGE_S ago: reused
    clock.now += 2
    [a] = get_intake(authed)
    assert len(jira.searches()) == 1  # a plain GET keeps the 45 s cache
    [a] = get_intake(authed, fresh=True)
    assert len(jira.searches()) == 2
    assert [i.key for i in a.items] == ["SCRUM-2", "SCRUM-3"]
    clock.now += 1
    get_intake(authed)
    assert len(jira.searches()) == 2  # the fresh answer is cached as usual


def test_cache_within_ttl_and_refresh_after(
    authed: TestClient, ctx: AppContext, jira: FakeJira, clock: Clock
) -> None:
    configure(ctx, ("a", "project = SCRUM"), ("b", "bad"))
    jira.results["project = SCRUM"] = [issue("SCRUM-2")]
    jira.failures["bad"] = httpx.Response(400, json={"errorMessages": ["nope"]})
    get_intake(authed)
    assert len(jira.calls) == 2
    jira.results["project = SCRUM"] = [issue("SCRUM-2"), issue("SCRUM-3")]
    clock.now += 44
    a, b = get_intake(authed)
    assert len(jira.calls) == 2  # both answers (the error too) came from the cache
    assert ([i.key for i in a.items], b.error) == (["SCRUM-2"], "Jira rejected the query: nope")
    clock.now += 2
    a, _ = get_intake(authed)
    assert len(jira.calls) == 4
    assert [i.key for i in a.items] == ["SCRUM-2", "SCRUM-3"]


def test_not_configured(authed: TestClient, ctx: AppContext, jira: FakeJira) -> None:
    assert get_intake(authed) == []  # no queries
    configure(ctx, ("a", "project = SCRUM"), ("b", "type = Bug"))
    ctx.settings = ctx.settings.model_copy(update={"jira_service_token": None})
    queries = get_intake(authed)
    assert [(q.id, q.items, q.total, q.error) for q in queries] == [
        ("a", [], 0, service.NOT_CONFIGURED),
        ("b", [], 0, service.NOT_CONFIGURED),
    ]
    assert jira.calls == []


def test_auth(client: TestClient, ctx: AppContext, login: LoginFn, jira: FakeJira) -> None:
    configure(ctx, ("a", "project = SCRUM"))
    assert client.get("/api/intake").status_code == 401
    login("pat", "pending")
    response = client.get("/api/intake")
    assert (response.status_code, response.json()["error"]["code"]) == (403, "pending_approval")
    assert jira.calls == []
    login("vic", "viewer")
    assert client.get("/api/intake").status_code == 200


# ------------------------------------------------------------------ the client


def test_cloud_search_skips_the_count_when_jira_says_last_page() -> None:
    jira = FakeJira()
    jira.results["q"] = [issue("SCRUM-2"), {"key": "nope"}, issue("SCRUM-3", updated="soon")]
    creds = JiraCredentials(token=SERVICE_TOKEN, email=SERVICE_EMAIL)
    found = JiraSource(jira.client(), creds).search("q", 10)
    assert ([c.key for c in found.issues], found.count, found.complete) == (["SCRUM-2"], 1, True)
    assert [c.url.path for c in jira.calls] == ["/rest/api/3/search/jql"]


def test_datacenter_search_uses_v2_and_its_total() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/api/2/search"
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"total": 7, "issues": [issue("OPS-1")]})

    client = JiraClient(JIRA, "datacenter", transport=httpx.MockTransport(handler))
    page = client.search(client.credentials(token=SERVICE_TOKEN, email=None), "q", ("summary",), 1)
    assert (len(page.issues), page.count, page.complete) == (1, 7, False)
    assert seen == [{"jql": "q", "maxResults": 1, "fields": ["summary"], "startAt": 0}]


def test_bad_query_message_is_jiras_cut_short() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(400, json={"errorMessages": ["x" * 500], "errors": {"jql": "bad"}})

    client = JiraClient(JIRA, "cloud", transport=httpx.MockTransport(handler))
    with pytest.raises(JiraBadQuery) as caught:
        client.search(
            client.credentials(token=SERVICE_TOKEN, email=SERVICE_EMAIL), "q", ("summary",), 1
        )
    assert len(str(caught.value)) == 300
    assert str(caught.value).endswith("…")
