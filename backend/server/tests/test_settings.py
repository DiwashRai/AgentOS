"""Settings in the app (Settings > Space, Repos, Intake): the endpoints and their rules, and
changes applying without a restart."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agentos.config import Settings
from agentos.context import AppContext
from agentos.intake import service as intake
from agentos.models import (
    AppConfig,
    IntakeSettings,
    Me,
    RepoSettings,
    SpaceSettings,
    TicketDetail,
)
from agentos.pipeline.space import query_id
from agentos.sources.jira import JiraClient

CSRF = {"X-AgentOS": "1"}
JIRA = "https://jira.example.com"
SERVICE_EMAIL = "bot@example.com"
SERVICE_TOKEN = "svc-token-0123456789"  # noqa: S105 - a fake token for the mock
LoginFn = Callable[..., Me]
CreateTicket = Callable[..., TicketDetail]


def code(response: httpx.Response) -> str:
    value: str = response.json()["error"]["code"]
    return value


def message(response: httpx.Response) -> str:
    value: str = response.json()["error"]["message"]
    return value


def locs(response: httpx.Response) -> list[tuple[str | int, ...]]:
    """Where each issue of a 422 is."""
    return [tuple(issue["loc"]) for issue in response.json()["error"]["issues"]]


def space(client: TestClient) -> SpaceSettings:
    response = client.get("/api/settings/space")
    assert response.status_code == 200, response.text
    return SpaceSettings.model_validate(response.json())


def repos(client: TestClient) -> RepoSettings:
    response = client.get("/api/settings/repos")
    assert response.status_code == 200, response.text
    return RepoSettings.model_validate(response.json())


def queries(client: TestClient) -> IntakeSettings:
    response = client.get("/api/settings/intake")
    assert response.status_code == 200, response.text
    return IntakeSettings.model_validate(response.json())


def config(client: TestClient) -> AppConfig:
    return AppConfig.model_validate(client.get("/api/config").json())


def put_space(
    client: TestClient, base_version: int | None = None, **changes: Any
) -> httpx.Response:
    """PUT the current Space document with `changes` (base_version: the current version)."""
    current = space(client)
    body = {
        "value": current.value.model_dump() | changes,
        "base_version": current.version if base_version is None else base_version,
    }
    response: httpx.Response = client.put("/api/settings/space", json=body, headers=CSRF)
    return response


def put_repos(
    client: TestClient, entries: list[dict[str, Any]], base_version: int | None = None
) -> httpx.Response:
    if base_version is None:
        base_version = repos(client).version
    body = {"value": {"repos": entries}, "base_version": base_version}
    response: httpx.Response = client.put("/api/settings/repos", json=body, headers=CSRF)
    return response


def put_queries(
    client: TestClient, entries: list[dict[str, Any]], base_version: int | None = None
) -> httpx.Response:
    if base_version is None:
        base_version = queries(client).version
    body = {"value": {"queries": entries}, "base_version": base_version}
    response: httpx.Response = client.put("/api/settings/intake", json=body, headers=CSRF)
    return response


R = {"name": "r", "url": "https://github.com/o/r.git", "default_branch": "main"}
DOCS = {"name": "docs", "url": "git@github.com:o/docs.git", "default_branch": "develop"}


# ------------------------------------------------------------------ space


def test_seeded_space_settings(authed: TestClient) -> None:
    got = space(authed)
    assert got.model_dump(exclude={"updated_at"}) == {
        "value": {
            "name": "Test Space",
            "default_repo": "r",
            "key_prefix": "AOS",
            "jira_projects": [],
            "claim_timeout_s": 3600,
        },
        "version": 1,
        "updated_by": None,  # seeded, not saved by anyone
    }


def test_put_space_applies_without_a_restart(authed: TestClient) -> None:
    me = Me.model_validate(authed.get("/api/me").json())
    before = space(authed)
    response = put_space(
        authed,
        name="  Renamed  ",
        default_repo="docs",
        key_prefix="ZED",
        jira_projects=["SCRUM", "OPS_2"],
        claim_timeout_s=60,
    )
    assert response.status_code == 200, response.text
    saved = SpaceSettings.model_validate(response.json())
    got = saved.value
    assert (got.name, got.default_repo, got.key_prefix) == ("Renamed", "docs", "ZED")
    assert (got.jira_projects, got.claim_timeout_s) == (["SCRUM", "OPS_2"], 60)
    assert saved.version == before.version + 1
    assert saved.updated_by is not None
    assert (saved.updated_by.id, saved.updated_at >= before.updated_at) == (me.id, True)
    assert space(authed) == saved
    cfg = config(authed)
    assert (cfg.space_name, cfg.key_prefix, cfg.default_repo) == ("Renamed", "ZED", "docs")
    ticket = authed.post("/api/tickets", json={"title": "T"}, headers=CSRF).json()
    assert (ticket["key"], ticket["repo_name"]) == ("ZED-1", "docs")


@pytest.mark.parametrize(
    ("changes", "where"),
    [
        pytest.param({"key_prefix": "aos"}, ("key_prefix",), id="prefix-lowercase"),
        pytest.param({"key_prefix": "1AB"}, ("key_prefix",), id="prefix-digit-first"),
        pytest.param({"key_prefix": "ABCDEFGHIJK"}, ("key_prefix",), id="prefix-too-long"),
        pytest.param({"key_prefix": ""}, ("key_prefix",), id="prefix-empty"),
        pytest.param({"jira_projects": ["scrum"]}, ("jira_projects", 0), id="project-lowercase"),
        pytest.param({"jira_projects": ["S"]}, ("jira_projects", 0), id="project-too-short"),
        pytest.param({"jira_projects": ["OK", "SC-1"]}, ("jira_projects", 1), id="project-dash"),
        pytest.param({"jira_projects": ["OPS", "OPS"]}, ("jira_projects",), id="project-twice"),
        pytest.param(
            {"key_prefix": "SCRUM", "jira_projects": ["SCRUM"]},
            ("key_prefix",),
            id="prefix-is-a-project",
        ),
        pytest.param({"claim_timeout_s": 59}, ("claim_timeout_s",), id="timeout-low"),
        pytest.param({"claim_timeout_s": 86401}, ("claim_timeout_s",), id="timeout-high"),
        pytest.param({"name": "   "}, ("name",), id="name-blank"),
        pytest.param({"name": "x" * 101}, ("name",), id="name-long"),
        pytest.param({"jira_projects": ["OPS"] * 51}, ("jira_projects",), id="projects-many"),
        pytest.param({"default_repo": "nope"}, ("default_repo",), id="unknown-repo"),
        pytest.param({"default_repo": None, "version": 1}, ("version",), id="read-only-field"),
        pytest.param({"claim_timeout_s": None}, ("claim_timeout_s",), id="null"),
    ],
)
def test_put_space_validates(
    authed: TestClient, changes: dict[str, Any], where: tuple[str | int, ...]
) -> None:
    response = put_space(authed, **changes)
    assert (response.status_code, code(response)) == (422, "validation_error"), response.text
    assert ("value", *where) in locs(response)
    assert space(authed).version == 1  # nothing saved


def test_a_project_key_can_not_become_the_prefix(authed: TestClient) -> None:
    """Checked both ways: adding the prefix as a project is refused too."""
    assert put_space(authed, jira_projects=["SCRUM"]).status_code == 200
    response = put_space(authed, key_prefix="SCRUM")
    assert (response.status_code, code(response)) == (422, "validation_error")
    assert locs(response) == [("value", "key_prefix")]
    response = put_space(authed, jira_projects=["SCRUM", "AOS"])
    assert (response.status_code, code(response)) == (422, "validation_error")
    assert locs(response) == [("value", "key_prefix")]


def test_projects_are_required_while_sign_in_is_jira(authed: TestClient, ctx: AppContext) -> None:
    assert put_space(authed, jira_projects=[]).status_code == 200  # dev auth: optional
    ctx.settings = ctx.settings.model_copy(update={"dev_auth": False})
    response = put_space(authed, jira_projects=[])
    assert (response.status_code, code(response)) == (422, "validation_error")
    assert locs(response) == [("value", "jira_projects")]
    assert put_space(authed, jira_projects=["SCRUM"]).status_code == 200


def test_the_sign_in_rule_comes_with_the_field_rules(authed: TestClient, ctx: AppContext) -> None:
    """The rule from the server's config (Jira sign-in needs projects) is reported in the same
    422 as a field rule."""
    ctx.settings = ctx.settings.model_copy(update={"dev_auth": False})

    response = put_space(authed, jira_projects=[], key_prefix="1")

    assert (response.status_code, code(response)) == (422, "validation_error")
    assert locs(response) == [("value", "key_prefix"), ("value", "jira_projects")]


def test_settings_are_admin_only(client: TestClient, login: LoginFn) -> None:
    login("wanda", "writer")
    for area in ("space", "repos", "intake"):
        response = client.get(f"/api/settings/{area}")
        assert (response.status_code, code(response)) == (403, "forbidden")


# ------------------------------------------------------------------ repos


def test_seeded_repos_count_open_tickets(authed: TestClient, create_ticket: CreateTicket) -> None:
    create_ticket("open on r")
    create_ticket("open on docs", repo="docs")
    done = create_ticket("done on docs", repo="docs")
    authed.post(f"/api/tickets/{done.key}/resolve", json={"outcome": "completed"}, headers=CSRF)
    create_ticket("no repo", repo=None)
    got = repos(authed)
    assert [(e.name, e.url, e.default_branch) for e in got.value.repos] == [
        ("r", "https://github.com/o/r.git", "main"),
        ("docs", "git@github.com:o/docs.git", "develop"),
    ]
    assert got.open_ticket_counts == {"r": 1, "docs": 1}
    assert (got.version, got.updated_by) == (1, None)


def test_put_repos_applies_without_a_restart(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    ticket = create_ticket("on docs", repo="docs")
    new = {"name": "new.repo", "url": "https://example.com/new.git", "default_branch": "trunk"}
    moved = {**DOCS, "url": "https://example.com/docs.git", "default_branch": "main"}
    response = put_repos(authed, [new, moved, R])  # add, change, reorder
    assert response.status_code == 200, response.text
    saved = RepoSettings.model_validate(response.json())
    assert [e.name for e in saved.value.repos] == ["new.repo", "docs", "r"]
    assert saved.open_ticket_counts == {"new.repo": 0, "docs": 1, "r": 0}
    assert (saved.version, saved.updated_by is not None) == (2, True)
    assert [r.name for r in config(authed).repos] == ["new.repo", "docs", "r"]
    t = TicketDetail.model_validate(authed.get(f"/api/tickets/{ticket.key}").json())
    assert (t.repo_url, t.base_branch) == ("https://example.com/docs.git", "main")
    created = authed.post("/api/tickets", json={"title": "x", "repo": "new.repo"}, headers=CSRF)
    assert created.status_code == 201, created.text


def test_removing_a_repo_open_tickets_use_is_in_use(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    create_ticket("a", repo="docs")
    create_ticket("b", repo="docs")
    response = put_repos(authed, [R])
    assert (response.status_code, code(response)) == (409, "in_use")
    assert "'docs' (2 open tickets use it)" in message(response)
    assert len(repos(authed).value.repos) == 2


def test_done_and_closed_tickets_dont_block_a_removal(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    done = create_ticket("done", repo="docs")
    closed = create_ticket("closed", repo="docs")
    authed.post(f"/api/tickets/{done.key}/resolve", json={"outcome": "completed"}, headers=CSRF)
    authed.patch(f"/api/tickets/{closed.key}", json={"state": "closed"}, headers=CSRF)
    assert put_repos(authed, [R]).status_code == 200
    t = TicketDetail.model_validate(authed.get(f"/api/tickets/{done.key}").json())
    assert (t.repo_name, t.repo_url) == ("docs", None)  # kept as a plain name


def test_removing_the_default_repo_is_in_use(authed: TestClient) -> None:
    response = put_repos(authed, [DOCS])
    assert (response.status_code, code(response)) == (409, "in_use")
    assert "'r' (it is the Space's default repo)" in message(response)
    assert put_space(authed, default_repo=None).status_code == 200
    assert put_repos(authed, [DOCS]).status_code == 200
    assert config(authed).default_repo is None


@pytest.mark.parametrize(
    ("entries", "where"),
    [
        pytest.param([{**R, "name": "Bad Name"}], ("repos", 0, "name"), id="bad-name"),
        pytest.param([R, {**DOCS, "url": "file:///tmp/r"}], ("repos", 1, "url"), id="file-url"),
        pytest.param(
            [{**R, "url": "https://user:pw@example.com/r.git"}],
            ("repos", 0, "url"),
            id="credentials",
        ),
        pytest.param(
            [{**R, "default_branch": "a..b"}], ("repos", 0, "default_branch"), id="branch"
        ),
        pytest.param([R, {**DOCS, "name": "r"}], ("repos", 1, "name"), id="duplicate"),
        pytest.param(
            [{"name": "r", "url": R["url"]}], ("repos", 0, "default_branch"), id="missing-field"
        ),
        pytest.param(
            [{**R, "open_ticket_count": 0}], ("repos", 0, "open_ticket_count"), id="read-only-field"
        ),
        pytest.param(
            [{**R, "url": "https://x.example/" + "r" * 500}], ("repos", 0, "url"), id="long"
        ),
        pytest.param([{**R, "name": f"r{i}"} for i in range(101)], ("repos",), id="too-many"),
    ],
)
def test_put_repos_validates(
    authed: TestClient, entries: list[dict[str, Any]], where: tuple[str | int, ...]
) -> None:
    response = put_repos(authed, entries)
    assert (response.status_code, code(response)) == (422, "validation_error"), response.text
    assert ("value", *where) in locs(response)
    assert repos(authed).version == 1


# ------------------------------------------------------------------ intake


def test_query_ids() -> None:
    assert query_id("SCRUM: to do", set()) == "scrum-to-do"
    assert query_id("SCRUM: to do", {"scrum-to-do"}) == "scrum-to-do-2"
    assert query_id("SCRUM: to do", {"scrum-to-do", "scrum-to-do-2"}) == "scrum-to-do-3"
    assert query_id("  ---  ", set()) == "query"
    assert query_id("Ünïcode!", set()) == "n-code"
    long = query_id("x" * 80, set())
    assert long == "x" * 64
    assert query_id("x" * 80, {long}) == "x" * 62 + "-2"


def test_put_intake_makes_ids_and_keeps_sent_ones(authed: TestClient) -> None:
    assert queries(authed).model_dump(exclude={"updated_at"}) == {
        "value": {"queries": []},
        "version": 1,
        "updated_by": None,
    }
    response = put_queries(
        authed,
        [
            {"id": None, "name": "To do", "jql": "project = SCRUM"},
            {"id": "to-do", "name": "Kept", "jql": " a "},
            {"id": None, "name": "To do", "jql": "b"},
            {"id": None, "name": "!!!", "jql": "c"},
        ],
    )
    assert response.status_code == 200, response.text
    saved = IntakeSettings.model_validate(response.json())
    assert [(q.id, q.name, q.jql) for q in saved.value.queries] == [
        ("to-do-2", "To do", "project = SCRUM"),
        ("to-do", "Kept", "a"),
        ("to-do-3", "To do", "b"),
        ("query", "!!!", "c"),
    ]
    assert (saved.version, saved.updated_by is not None) == (2, True)
    # Sent back as-is (ids included), they stay; order = tab order.
    again = put_queries(authed, [q.model_dump() for q in reversed(saved.value.queries)])
    assert [q["id"] for q in again.json()["value"]["queries"]] == [
        "query",
        "to-do-3",
        "to-do",
        "to-do-2",
    ]


@pytest.mark.parametrize(
    ("entries", "where"),
    [
        pytest.param(
            [{"id": "Bad Id", "name": "x", "jql": "a"}], ("queries", 0, "id"), id="bad-id"
        ),
        pytest.param(
            [{"id": "a", "name": "x", "jql": "a"}, {"id": "a", "name": "y", "jql": "b"}],
            ("queries", 1, "id"),
            id="duplicate-id",
        ),
        pytest.param(
            [{"id": None, "name": " ", "jql": "a"}], ("queries", 0, "name"), id="blank-name"
        ),
        pytest.param([{"id": None, "name": "x", "jql": ""}], ("queries", 0, "jql"), id="empty-jql"),
        pytest.param([{"name": "x", "jql": "a"}], ("queries", 0, "id"), id="id-is-required"),
        pytest.param(
            [{"id": None, "name": "x" * 101, "jql": "a"}], ("queries", 0, "name"), id="long-name"
        ),
        pytest.param(
            [{"id": None, "name": "x", "jql": "a" * 4001}], ("queries", 0, "jql"), id="long-jql"
        ),
        pytest.param(
            [{"id": None, "name": f"q{i}", "jql": "a"} for i in range(51)],
            ("queries",),
            id="too-many",
        ),
    ],
)
def test_put_intake_validates(
    authed: TestClient, entries: list[dict[str, Any]], where: tuple[str | int, ...]
) -> None:
    response = put_queries(authed, entries)
    assert (response.status_code, code(response)) == (422, "validation_error"), response.text
    assert ("value", *where) in locs(response)
    assert queries(authed).version == 1


def test_saving_intake_drops_the_cache_and_applies_at_once(
    authed: TestClient, ctx: AppContext, search: FakeSearch
) -> None:
    ctx.intake_cache.put("a", "an old answer")
    response = put_queries(authed, [{"id": "a", "name": "A", "jql": "project = SCRUM"}])
    assert response.status_code == 200
    assert ctx.intake_cache.get("a") is None
    search.results["project = SCRUM"] = [issue("SCRUM-1")]
    listed = authed.get("/api/intake").json()
    assert [(q["id"], q["error"], q["total"]) for q in listed] == [("a", None, 1)]


# ------------------------------------------------------------------ intake preview


def issue(key: str, minutes_ago: int = 0) -> dict[str, Any]:
    return {
        "id": "1",
        "key": key,
        "fields": {
            "summary": f"Summary of {key}",
            "issuetype": {"name": "Story"},
            "priority": {"name": "Medium"},
            "status": {"name": "To Do"},
            "assignee": None,
            "updated": f"2026-10-11T12:{59 - minutes_ago:02d}:00.000+0000",
        },
    }


class FakeSearch:
    """Jira's JQL search: matches by JQL (without the appended ORDER BY), or a failure."""

    def __init__(self) -> None:
        self.results: dict[str, list[dict[str, Any]]] = {}
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        body = json.loads(request.content)
        if request.url.path.endswith("/jql/parse"):
            errors = (
                ["Field 'nosuchfield' does not exist"]
                if "nosuchfield" in body["queries"][0]
                else []
            )
            return httpx.Response(200, json={"queries": [{"errors": errors}]})
        jql = body["jql"].removesuffix(" ORDER BY updated DESC")
        if jql == "bad":
            return httpx.Response(400, json={"errorMessages": ["nope"]})
        matches = self.results.get(jql, [])
        if request.url.path.endswith("/approximate-count"):
            return httpx.Response(200, json={"count": len(matches)})
        return httpx.Response(200, json={"issues": matches, "isLast": True})


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Dev auth plus a Jira service account (for the preview)."""
    return settings.model_copy(
        update={
            "jira_url": JIRA,
            "jira_service_email": SERVICE_EMAIL,
            "jira_service_token": SecretStr(SERVICE_TOKEN),
        }
    )


@pytest.fixture
def search(ctx: AppContext) -> FakeSearch:
    fake = FakeSearch()
    ctx.jira = JiraClient(JIRA, "cloud", transport=httpx.MockTransport(fake.handler))
    return fake


def preview(client: TestClient, jql: str) -> httpx.Response:
    response: httpx.Response = client.post(
        "/api/settings/intake/preview", json={"jql": jql}, headers=CSRF
    )
    return response


def test_preview_is_live_capped_and_filtered(
    authed: TestClient, search: FakeSearch, create_ticket: CreateTicket, ctx: AppContext
) -> None:
    assert put_space(authed, jira_projects=["SCRUM"]).status_code == 200
    search.results["project = SCRUM"] = [
        *(issue(f"SCRUM-{n}", n) for n in range(1, 9)),
        issue("OPS-1"),  # outside the Space's projects
    ]
    native = create_ticket("taken")
    with ctx.db.tx() as conn:  # SCRUM-2 is in AgentOS already
        conn.execute("UPDATE tickets SET key = 'SCRUM-2' WHERE key = ?", (native.key,))
    response = preview(authed, "  project = SCRUM ")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [i["key"] for i in body["items"]] == [
        "SCRUM-1",
        "SCRUM-3",
        "SCRUM-4",
        "SCRUM-5",
        "SCRUM-6",
    ]
    assert (body["total"], body["error"]) == (7, None)
    calls = search.calls
    assert preview(authed, "project = SCRUM").status_code == 200
    assert search.calls > calls  # not cached
    assert SERVICE_TOKEN not in response.text


def test_preview_errors(authed: TestClient, search: FakeSearch, ctx: AppContext) -> None:
    bad = preview(authed, "bad").json()
    assert bad == {"total": 0, "error": "Jira rejected the query: nope", "items": []}
    unknown = preview(authed, "project = SCRUM AND nosuchfield = 1").json()  # Cloud: 200, empty
    assert unknown["error"] == "Jira rejected the query: Field 'nosuchfield' does not exist"
    ctx.settings = ctx.settings.model_copy(update={"jira_service_token": None})
    calls = search.calls
    assert preview(authed, "x").json()["error"] == intake.NOT_CONFIGURED
    assert search.calls == calls
    blank = preview(authed, "   ")
    assert (blank.status_code, code(blank)) == (422, "validation_error")


def test_preview_is_admin_only(client: TestClient, login: LoginFn, search: FakeSearch) -> None:
    login("wanda", "writer")
    assert preview(client, "x").status_code == 403
    assert search.calls == 0


# ------------------------------------------------------------------ server_default_runtime


def test_config_names_the_server_default_runtime(authed: TestClient) -> None:
    assert (config(authed).default_runtime, config(authed).server_default_runtime) == (
        "fake",
        "fake",
    )
    template = authed.get("/api/template").json()
    body = {
        "scaffold": template["scaffold"],
        "defaults": {"runtime": "claude"},
        "base_version": template["version"],
    }
    assert authed.put("/api/template", json=body, headers=CSRF).status_code == 200
    assert (config(authed).default_runtime, config(authed).server_default_runtime) == (
        "claude",
        "fake",
    )
