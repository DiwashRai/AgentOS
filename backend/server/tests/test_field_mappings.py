"""Jira field mappings (request 22B), first target `base_branch`: Settings > Field mappings (GET /
PUT, validation, stale version), the mapping applied at import (manual and Intake Take in), the
re-apply on save, the Jira field picker and the preview, against a mocked Jira."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agentos.config import Settings
from agentos.context import AppContext
from agentos.intake import service as intake
from agentos.models import (
    IntakeQuerySpec,
    JiraField,
    MappingPreview,
    MappingSettings,
    Me,
    TicketDetail,
)
from agentos.sources.jira import ISSUE_FIELDS, JiraClient
from tests.run_support import RunDriver
from tests.settings_support import set_queries, set_space

CSRF = {"X-AgentOS": "1"}
JIRA = "https://jira.example.com"
SERVICE_EMAIL = "bot@example.com"
SERVICE_TOKEN = "svc-token-0123456789"  # noqa: S105 - a fake token for the mock
LoginFn = Callable[..., Me]
CreateTicket = Callable[..., TicketDetail]

RELEASES = [
    {"match": "version_1*", "value": "App-1.Latest"},
    {"match": "version_2*", "value": "App-2.Latest"},
]
FIX_VERSIONS = {
    "target": "base_branch",
    "source": "jira",
    "field": "fixVersions",
    "field_name": "Fix versions",
    "rules": RELEASES,
}
JIRA_FIELDS = [
    {"id": "summary", "name": "Summary", "schema": {"type": "string", "system": "summary"}},
    {
        "id": "fixVersions",
        "name": "Fix versions",
        "schema": {"type": "array", "items": "version", "system": "fixVersions"},
    },
    {
        "id": "customfield_10042",
        "name": "Release",
        "schema": {"type": "option", "custom": "com.atlassian...:select", "customId": 10042},
    },
    {"id": "customfield_10050", "name": "Target version", "schema": {"type": "version"}},
    {"id": "duedate", "name": "Due date", "schema": {"type": "date"}},
    {"id": "issuekey", "name": "Key"},  # no schema
    {"id": "", "name": "Broken"},  # skipped
]


def versions(*names: str) -> list[dict[str, Any]]:
    """Cloud's fixVersions: version objects."""
    return [{"self": f"{JIRA}/x", "id": str(i), "name": n} for i, n in enumerate(names)]


def issue(key: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": "1",
        "key": key,
        "fields": {
            "summary": f"Summary of {key}",
            "description": None,
            "issuetype": {"name": "Story"},
            "priority": {"name": "Medium"},
            "labels": [],
            "status": {"name": "To Do"},
            "assignee": None,
            "updated": "2026-10-11T12:00:00.000+0000",
            **fields,
        },
    }


@dataclass
class FakeJira:
    """Issues by key (an issue GET answers only the requested fields, as Jira does), the field
    list, and a search that matches every issue. `fields_failure`: the field list answers it."""

    issues: dict[str, dict[str, Any]] = field(default_factory=dict)
    fields_failure: Exception | int | None = None
    calls: list[httpx.Request] = field(default_factory=list)

    def add(self, key: str, **fields: Any) -> None:
        self.issues[key] = issue(key, **fields)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        expected = JiraClient(JIRA, "cloud").credentials(token=SERVICE_TOKEN, email=SERVICE_EMAIL)
        if request.headers.get("authorization") != expected.authorization():
            return httpx.Response(401)
        path = request.url.path
        if path == "/rest/api/2/field":
            if isinstance(self.fields_failure, Exception):
                raise self.fields_failure
            if isinstance(self.fields_failure, int):
                return httpx.Response(self.fields_failure)
            return httpx.Response(200, json=JIRA_FIELDS)
        if path == "/rest/api/3/search/jql":
            body = json.loads(request.content)
            found = [
                {"key": i["key"], "fields": {f: i["fields"].get(f) for f in body["fields"]}}
                for i in self.issues.values()
            ]
            return httpx.Response(200, json={"issues": found, "isLast": True})
        key = path.removeprefix("/rest/api/2/issue/")
        if key not in self.issues:
            return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})
        data = self.issues[key]
        wanted = request.url.params["fields"].split(",")
        fields = {f: data["fields"][f] for f in wanted if f in data["fields"]}
        return httpx.Response(200, json={**data, "fields": fields})

    def requested_fields(self) -> list[list[str]]:
        return [
            r.url.params["fields"].split(",")
            for r in self.calls
            if r.url.path.startswith("/rest/api/2/issue/")
        ]

    def field_calls(self) -> int:
        return sum(1 for r in self.calls if r.url.path == "/rest/api/2/field")


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
def jira(ctx: AppContext) -> FakeJira:
    set_space(ctx, jira_projects=("SCRUM",))
    fake = FakeJira()
    ctx.jira = JiraClient(JIRA, "cloud", transport=httpx.MockTransport(fake.handler))
    return fake


def code(response: httpx.Response) -> str:
    value: str = response.json()["error"]["code"]
    return value


def message(response: httpx.Response) -> str:
    value: str = response.json()["error"]["message"]
    return value


def locs(response: httpx.Response) -> list[tuple[str | int, ...]]:
    """Where each issue of a 422 is."""
    return [tuple(issue["loc"]) for issue in response.json()["error"]["issues"]]


def get_mappings(client: TestClient) -> MappingSettings:
    response = client.get("/api/settings/mappings")
    assert response.status_code == 200, response.text
    return MappingSettings.model_validate(response.json())


def put_mappings(
    client: TestClient, mappings: list[dict[str, Any]], base_version: int | None = None
) -> httpx.Response:
    if base_version is None:
        base_version = get_mappings(client).version
    body = {"value": {"mappings": mappings}, "base_version": base_version}
    response: httpx.Response = client.put("/api/settings/mappings", json=body, headers=CSRF)
    return response


def save(client: TestClient, *mappings: dict[str, Any]) -> MappingSettings:
    response = put_mappings(client, list(mappings))
    assert response.status_code == 200, response.text
    return MappingSettings.model_validate(response.json())


def take_in(client: TestClient, key: str) -> TicketDetail:
    response = client.post("/api/tickets/import", json={"ref": key}, headers=CSRF)
    assert response.status_code == 201, response.text
    return TicketDetail.model_validate(response.json()["ticket"])


def detail(client: TestClient, key: str) -> TicketDetail:
    response = client.get(f"/api/tickets/{key}")
    assert response.status_code == 200, response.text
    return TicketDetail.model_validate(response.json())


def branch(t: TicketDetail) -> tuple[str | None, str | None, str | None, str | None]:
    """(base_branch, source, source_value, reason)."""
    info = t.base_branch_info
    assert info is not None
    return t.base_branch, info.source, info.source_value, info.reason


# ------------------------------------------------------------------ settings


def test_mappings_start_empty_and_save(authed: TestClient) -> None:
    seeded = get_mappings(authed)
    assert (seeded.value.mappings, seeded.version, seeded.updated_by) == ([], 1, None)
    rules = [{"match": "  version_1* ", "value": " App-1.Latest "}]
    saved = save(authed, {**FIX_VERSIONS, "rules": rules, "field_name": " Fix versions "})
    assert saved.version == 2
    assert saved.updated_by is not None
    (mapping,) = saved.value.mappings
    assert (mapping.field_name, mapping.rules[0].match, mapping.rules[0].value) == (
        "Fix versions",
        "version_1*",
        "App-1.Latest",
    )
    assert get_mappings(authed) == saved
    assert save(authed).value.mappings == []  # removing it is a save like any other


@pytest.mark.parametrize(
    ("change", "where"),
    [
        (
            {"rules": [*RELEASES, {"match": "x", "value": "bad..branch"}]},
            ("mappings", 0, "rules", 2, "value"),
        ),
        ({"rules": [{"match": "x", "value": "has space"}]}, ("mappings", 0, "rules", 0, "value")),
        ({"rules": [{"match": "x" * 201, "value": "b"}]}, ("mappings", 0, "rules", 0, "match")),
        ({"rules": [{"match": "   ", "value": "b"}]}, ("mappings", 0, "rules", 0, "match")),
        ({"rules": [{"match": "", "value": "b"}]}, ("mappings", 0, "rules", 0, "match")),
        (
            {"rules": [{"match": f"v{i}", "value": "b"} for i in range(101)]},
            ("mappings", 0, "rules"),
        ),
        ({"field": ""}, ("mappings", 0, "field")),
        ({"field": "fixVersions,summary"}, ("mappings", 0, "field")),
        ({"field": "x" * 256}, ("mappings", 0, "field")),
        ({"field_name": " "}, ("mappings", 0, "field_name")),
        ({"target": "repo"}, ("mappings", 0, "target")),
        ({"source": "github"}, ("mappings", 0, "source")),
        ({"rules": None}, ("mappings", 0, "rules")),
    ],
)
def test_put_mappings_validates(
    authed: TestClient, change: dict[str, Any], where: tuple[str | int, ...]
) -> None:
    response = put_mappings(authed, [{**FIX_VERSIONS, **change}])
    assert (response.status_code, code(response)) == (422, "validation_error"), response.text
    assert ("value", *where) in locs(response)
    assert get_mappings(authed).version == 1


def test_at_most_twenty_mappings(authed: TestClient) -> None:
    response = put_mappings(authed, [FIX_VERSIONS] * 21)
    assert (response.status_code, code(response)) == (422, "validation_error")
    assert ("value", "mappings") in locs(response)


def test_mappings_are_admin_only(client: TestClient, login: LoginFn, jira: FakeJira) -> None:
    login("wanda", "writer")
    assert client.get("/api/settings/mappings").status_code == 403
    assert put_mappings(client, [], base_version=1).status_code == 403
    assert client.get("/api/settings/mappings/jira-fields").status_code == 403
    preview = client.post(
        "/api/settings/mappings/preview",
        json={"mapping": FIX_VERSIONS, "values": None, "issue": "SCRUM-1"},
        headers=CSRF,
    )
    assert preview.status_code == 403
    assert jira.calls == []


# ------------------------------------------------------------------ import


def test_import_without_a_mapping_is_unchanged(authed: TestClient, jira: FakeJira) -> None:
    jira.add("SCRUM-1", fixVersions=versions("version_1.0"))
    t = take_in(authed, "SCRUM-1")
    assert branch(t) == ("main", "repo_default", None, None)
    assert jira.requested_fields() == [list(ISSUE_FIELDS)]


def test_import_maps_a_matching_field(authed: TestClient, jira: FakeJira) -> None:
    save(authed, FIX_VERSIONS)
    jira.add("SCRUM-1", fixVersions=versions("version_1.0"))
    t = take_in(authed, "SCRUM-1")
    assert branch(t) == ("App-1.Latest", "mapping", "version_1.0", None)
    assert t.dispatch_block is None
    assert jira.requested_fields() == [[*ISSUE_FIELDS, "fixVersions"]]  # one call


@pytest.mark.parametrize(
    ("fix_versions", "expected"),
    [
        (
            versions("version_3.0"),
            (None, "none", "version_3.0", "No rule matches version_3.0"),
        ),
        ([], (None, "none", None, "Fix versions is empty in Jira")),
        (None, (None, "none", None, "Fix versions is empty in Jira")),
        (
            versions("version_1.0", "version_1.2"),
            ("App-1.Latest", "mapping", "version_1.0, version_1.2", None),
        ),
        (
            versions("version_3.0", "version_2.1"),
            ("App-2.Latest", "mapping", "version_3.0, version_2.1", None),
        ),
        (
            versions("version_1.2", "version_2.0"),
            (
                None,
                "none",
                "version_1.2, version_2.0",
                "version_1.2 and version_2.0 map to different branches",
            ),
        ),
    ],
)
def test_import_maps_each_value(
    authed: TestClient,
    jira: FakeJira,
    fix_versions: list[dict[str, Any]] | None,
    expected: tuple[str | None, str, str | None, str | None],
) -> None:
    save(authed, FIX_VERSIONS)
    jira.add("SCRUM-1", fixVersions=fix_versions)
    t = take_in(authed, "SCRUM-1")
    assert branch(t) == expected
    if expected[0] is None:
        assert t.dispatch_block is not None
        assert t.dispatch_block.code == "base_branch_missing"


def test_import_maps_a_missing_field_as_empty(authed: TestClient, jira: FakeJira) -> None:
    save(authed, FIX_VERSIONS)
    jira.add("SCRUM-1")  # Jira leaves out a field the issue doesn't have
    assert branch(take_in(authed, "SCRUM-1")) == (
        None,
        "none",
        None,
        "Fix versions is empty in Jira",
    )


def test_import_maps_an_option_and_a_string_field(authed: TestClient, jira: FakeJira) -> None:
    rules = [{"match": "release 1", "value": "rel-1"}, {"match": "R2*", "value": "rel-2"}]
    option = {**FIX_VERSIONS, "field": "customfield_10042", "field_name": "Release"}
    save(authed, {**option, "rules": rules})
    jira.add("SCRUM-1", customfield_10042={"self": "x", "value": "Release 1", "id": "10"})
    assert branch(take_in(authed, "SCRUM-1")) == ("rel-1", "mapping", "Release 1", None)
    text = {**FIX_VERSIONS, "field": "customfield_10051", "field_name": "Train", "rules": rules}
    save(authed, text)
    jira.add("SCRUM-2", customfield_10051="r2-autumn ")
    assert branch(take_in(authed, "SCRUM-2")) == ("rel-2", "mapping", "r2-autumn", None)


def test_a_mapped_blank_ticket_is_blocked_despite_the_repo_default(
    authed: TestClient, jira: FakeJira, runs: RunDriver, ctx: AppContext
) -> None:
    save(authed, FIX_VERSIONS)
    jira.add("SCRUM-1", fixVersions=versions("version_9"))
    t = take_in(authed, "SCRUM-1")
    assert t.repo_name == "r"  # whose default branch is main
    assert t.base_branch is None
    response = runs.dispatch(t.key)
    assert (response.status_code, code(response)) == (409, "base_branch_missing")
    with ctx.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    # Set by hand, it dispatches.
    patched = authed.patch(f"/api/tickets/{t.key}", json={"base_branch": "rel"}, headers=CSRF)
    assert patched.status_code == 200, patched.text
    assert runs.start(t.key).manifest.workspace.base_branch == "rel"


def test_a_no_repo_import_ignores_the_mapping(
    authed: TestClient, jira: FakeJira, ctx: AppContext
) -> None:
    save(authed, FIX_VERSIONS)
    set_space(ctx, default_repo=None)
    jira.add("SCRUM-1", fixVersions=versions("version_1.0"))
    t = take_in(authed, "SCRUM-1")
    assert (t.workspace_mode, t.base_branch, t.base_branch_info) == ("none", None, None)
    assert jira.requested_fields() == [list(ISSUE_FIELDS)]


def test_intake_take_in_applies_the_mapping(
    authed: TestClient, jira: FakeJira, ctx: AppContext
) -> None:
    save(authed, FIX_VERSIONS)
    set_queries(ctx, [IntakeQuerySpec(id="todo", name="To do", jql="project = SCRUM")])
    jira.add("SCRUM-6", fixVersions=versions("version_2.4"))
    listed = authed.get("/api/intake").json()
    assert [i["key"] for i in listed[0]["items"]] == ["SCRUM-6"]
    t = take_in(authed, "SCRUM-6")  # "Take in" is the import
    assert branch(t) == ("App-2.Latest", "mapping", "version_2.4", None)


def test_reimport_is_a_no_op(authed: TestClient, jira: FakeJira) -> None:
    jira.add("SCRUM-1", fixVersions=versions("version_1.0"))
    take_in(authed, "SCRUM-1")
    save(authed, FIX_VERSIONS)
    again = authed.post("/api/tickets/import", json={"ref": "SCRUM-1"}, headers=CSRF)
    assert again.status_code == 200, again.text
    t = TicketDetail.model_validate(again.json()["ticket"])
    assert branch(t) == ("main", "repo_default", None, None)


# ------------------------------------------------------------------ re-apply on save


def test_saving_re_applies_to_unlocked_mapped_tickets_only(
    authed: TestClient,
    jira: FakeJira,
    runs: RunDriver,
    create_ticket: CreateTicket,
) -> None:
    save(authed, FIX_VERSIONS)
    for key, name in [
        ("SCRUM-1", "version_3.0"),  # blank → set by the new rules
        ("SCRUM-2", "version_1.0"),  # set → another branch
        ("SCRUM-3", "version_1.1"),  # locked by a run: untouched
        ("SCRUM-4", "version_1.2"),  # then set by hand: untouched
        ("SCRUM-5", "version_2.0"),  # a run is queued: untouched
        ("SCRUM-6", "version_4.0"),  # blank before and after, for the same reason: unchanged
    ]:
        jira.add(key, fixVersions=versions(name))
        take_in(authed, key)
    worker = runs.start("SCRUM-3")
    worker.run_stage("plan")
    assert worker.complete().status_code == 204
    patched = authed.patch("/api/tickets/SCRUM-4", json={"base_branch": "hand"}, headers=CSRF)
    assert patched.status_code == 200, patched.text
    runs.queue("SCRUM-5")
    native = create_ticket("Native")  # `default`: the repo default
    before = {k: detail(authed, k) for k in ("SCRUM-3", "SCRUM-4", "SCRUM-5", "SCRUM-6")}
    jira.calls.clear()

    rules = [
        {"match": "version_3*", "value": "App-3.Latest"},
        {"match": "version_1*", "value": "App-1.New"},
        {"match": "version_2*", "value": "App-2.New"},
    ]
    save(authed, {**FIX_VERSIONS, "rules": rules})
    assert jira.calls == []  # from the stored values, no Jira call
    assert branch(detail(authed, "SCRUM-1")) == ("App-3.Latest", "mapping", "version_3.0", None)
    assert branch(detail(authed, "SCRUM-2")) == ("App-1.New", "mapping", "version_1.0", None)
    for key, was in before.items():
        assert detail(authed, key) == was, key
    assert branch(detail(authed, native.key)) == ("main", "repo_default", None, None)

    # Set → blank, with the reason.
    save(authed, {**FIX_VERSIONS, "rules": rules[:1]})
    assert branch(detail(authed, "SCRUM-2")) == (
        None,
        "none",
        "version_1.0",
        "No rule matches version_1.0",
    )
    # Removing the mapping leaves mapped tickets as they are.
    save(authed)
    assert detail(authed, "SCRUM-1").base_branch == "App-3.Latest"
    assert detail(authed, "SCRUM-2").base_branch is None


# ------------------------------------------------------------------ the Jira field picker


def test_jira_fields_kinds_and_cache(authed: TestClient, jira: FakeJira, ctx: AppContext) -> None:
    response = authed.get("/api/settings/mappings/jira-fields")
    assert response.status_code == 200, response.text
    fields = [JiraField.model_validate(f) for f in response.json()]
    assert [(f.name, f.id, f.kind) for f in fields] == [
        ("Due date", "duedate", "other"),
        ("Fix versions", "fixVersions", "array"),
        ("Key", "issuekey", "other"),
        ("Release", "customfield_10042", "option"),
        ("Summary", "summary", "string"),
        ("Target version", "customfield_10050", "version"),
    ]
    assert authed.get("/api/settings/mappings/jira-fields").json() == response.json()
    assert jira.field_calls() == 1  # cached
    ctx.jira_fields_cache.clear()
    assert authed.get("/api/settings/mappings/jira-fields").status_code == 200
    assert jira.field_calls() == 2


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (401, intake.UNAUTHORIZED),
        (403, intake.UNAUTHORIZED),
        (500, intake.UNAVAILABLE),
        (404, intake.UNAVAILABLE),
        (httpx.ConnectError("refused"), intake.UNAVAILABLE),
    ],
)
def test_jira_fields_errors(
    authed: TestClient, jira: FakeJira, failure: Exception | int, expected: str
) -> None:
    jira.fields_failure = failure
    response = authed.get("/api/settings/mappings/jira-fields")
    assert (response.status_code, code(response), message(response)) == (
        502,
        "upstream_error",
        expected,
    )
    assert SERVICE_TOKEN not in response.text
    jira.fields_failure = None  # errors aren't cached
    assert authed.get("/api/settings/mappings/jira-fields").status_code == 200


def test_jira_fields_without_jira_is_501(
    authed: TestClient, jira: FakeJira, ctx: AppContext
) -> None:
    ctx.settings = ctx.settings.model_copy(update={"jira_service_token": None})
    response = authed.get("/api/settings/mappings/jira-fields")
    assert (response.status_code, code(response)) == (501, "not_implemented")
    assert jira.calls == []


# ------------------------------------------------------------------ preview


def preview(client: TestClient, **body: Any) -> httpx.Response:
    """POST FIX_VERSIONS with `body` (`values` and `issue` null unless given)."""
    sent = {"mapping": FIX_VERSIONS, "values": None, "issue": None, **body}
    response: httpx.Response = client.post(
        "/api/settings/mappings/preview", json=sent, headers=CSRF
    )
    return response


def test_preview_with_values(authed: TestClient, jira: FakeJira) -> None:
    response = preview(authed, values=[" version_2.1 ", "", "version_9"])
    assert response.status_code == 200, response.text
    assert MappingPreview.model_validate(response.json()) == MappingPreview(
        values=["version_2.1", "version_9"], value="App-2.Latest", rule=1, reason=None
    )
    empty = MappingPreview.model_validate(preview(authed, values=[]).json())
    assert (empty.value, empty.reason) == (None, "Fix versions is empty in Jira")
    assert jira.calls == []
    assert get_mappings(authed).value.mappings == []  # nothing saved


def test_preview_with_an_issue(authed: TestClient, jira: FakeJira) -> None:
    jira.add("SCRUM-7", fixVersions=versions("version_1.2", "version_2.0"))
    response = preview(authed, issue=f"{JIRA}/browse/scrum-7")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "values": ["version_1.2", "version_2.0"],
        "value": None,
        "rule": None,
        "reason": "version_1.2 and version_2.0 map to different branches",
    }
    assert jira.requested_fields() == [["fixVersions"]]
    # The unsaved rules are what runs.
    one = {**FIX_VERSIONS, "rules": [{"match": "*", "value": "trunk"}]}
    got = preview(authed, mapping=one, issue="SCRUM-7")
    assert (got.json()["value"], got.json()["rule"]) == ("trunk", 0)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"values": ["x"], "issue": "SCRUM-1"},
        {"values": ["x"] * 101},
        {"issue": ""},
    ],
)
def test_preview_needs_exactly_one_input(
    authed: TestClient, jira: FakeJira, body: dict[str, Any]
) -> None:
    response = preview(authed, **body)
    assert (response.status_code, code(response)) == (422, "validation_error")
    assert jira.calls == []


def test_preview_errors(authed: TestClient, jira: FakeJira, ctx: AppContext) -> None:
    mapping = {**FIX_VERSIONS, "field": "a,b", "rules": [{"match": "x", "value": "a..b"}]}
    bad_rule = preview(authed, mapping=mapping, values=["x"])
    assert (bad_rule.status_code, code(bad_rule)) == (422, "validation_error")
    assert locs(bad_rule) == [("mapping", "field"), ("mapping", "rules", 0, "value")]
    not_a_ref = preview(authed, issue="https://evil.example/browse/SCRUM-1")
    assert (not_a_ref.status_code, code(not_a_ref)) == (422, "validation_error")
    unknown = preview(authed, issue="SCRUM-404")
    assert (unknown.status_code, code(unknown)) == (404, "ticket_not_found")
    ctx.settings = ctx.settings.model_copy(update={"jira_service_token": SecretStr("wrong")})
    rejected = preview(authed, issue="SCRUM-1")
    assert (rejected.status_code, code(rejected)) == (502, "upstream_error")
    assert "wrong" not in rejected.text
    ctx.settings = ctx.settings.model_copy(update={"jira_service_token": None})
    missing = preview(authed, issue="SCRUM-1")
    assert (missing.status_code, code(missing)) == (501, "not_implemented")
    assert preview(authed, values=["version_1"]).status_code == 200  # values need no Jira
