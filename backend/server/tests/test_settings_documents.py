"""The settings areas as versioned documents (space, repos, intake, runner, field mappings): the
shared read / save / validate-only behaviour, every problem in one 422, and request schemas
without optional fields. Each area's own rules are in test_settings.py,
test_settings_runners.py and test_field_mappings.py."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentos.models import Me, TicketDetail

CSRF = {"X-AgentOS": "1"}
CreateTicket = Callable[..., TicketDetail]
Loc = tuple[str | int, ...]

R = {"name": "r", "url": "https://github.com/o/r.git", "default_branch": "main"}
MAPPING = {
    "target": "base_branch",
    "source": "jira",
    "field": "fixVersions",
    "field_name": "Fix versions",
    "rules": [{"match": "version_1*", "value": "App-1.Latest"}],
}


@dataclass(frozen=True)
class Area:
    """`edit`: a valid change of the current value (already normalised, so it reads back as
    sent). `invalid`: a value with field and cross-field problems, at `locs`."""

    name: str
    edit: Callable[[dict[str, Any]], dict[str, Any]]
    invalid: Callable[[dict[str, Any]], dict[str, Any]]
    locs: list[Loc]


AREAS = [
    Area(
        "space",
        edit=lambda v: v | {"name": "Renamed", "jira_projects": ["SCRUM"]},
        # A field rule, the prefix vs the projects, and the repo catalog (in the db).
        invalid=lambda v: (
            v
            | {"name": "x" * 101, "key_prefix": "SCRUM", "jira_projects": ["SCRUM"]}
            | {"default_repo": "nope"}
        ),
        locs=[("value", "name"), ("value", "key_prefix"), ("value", "default_repo")],
    ),
    Area(
        "repos",
        edit=lambda v: {
            "repos": [
                *v["repos"],
                {"name": "new", "url": "https://x.example/n.git", "default_branch": "trunk"},
            ]
        },
        # A bad URL and a duplicate name.
        invalid=lambda v: {"repos": [{**R, "url": "file:///tmp/r"}, R]},
        locs=[("value", "repos", 0, "url"), ("value", "repos", 1, "name")],
    ),
    Area(
        "intake",
        edit=lambda v: {"queries": [{"id": "todo", "name": "To do", "jql": "project = X"}]},
        # A blank name and a repeated id.
        invalid=lambda v: {
            "queries": [
                {"id": "a", "name": " ", "jql": "x"},
                {"id": "a", "name": "B", "jql": "y"},
            ]
        },
        locs=[("value", "queries", 0, "name"), ("value", "queries", 1, "id")],
    ),
    Area(
        "runner",
        edit=lambda v: {"github_repo": "o/r", "workflow": "w.yml", "ref": "dev"},
        invalid=lambda v: {"github_repo": "no-owner", "workflow": "w", "ref": " "},
        locs=[("value", "github_repo"), ("value", "workflow"), ("value", "ref")],
    ),
    Area(
        "mappings",
        edit=lambda v: {"mappings": [MAPPING]},
        # A bad rule value, then a second mapping for the same target with a bad field.
        invalid=lambda v: {
            "mappings": [
                {**MAPPING, "rules": [{"match": "x", "value": "a..b"}]},
                {**MAPPING, "field": "a,b"},
            ]
        },
        locs=[
            ("value", "mappings", 0, "rules", 0, "value"),
            ("value", "mappings", 1, "target"),
            ("value", "mappings", 1, "field"),
        ],
    ),
]
EACH_AREA = pytest.mark.parametrize("area", [pytest.param(a, id=a.name) for a in AREAS])


def get(client: TestClient, area: Area) -> dict[str, Any]:
    response = client.get(f"/api/settings/{area.name}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def put(client: TestClient, area: Area, value: dict[str, Any], base_version: int) -> httpx.Response:
    body = {"value": value, "base_version": base_version}
    response: httpx.Response = client.put(f"/api/settings/{area.name}", json=body, headers=CSRF)
    return response


def validate(client: TestClient, area: Area, value: dict[str, Any]) -> httpx.Response:
    response: httpx.Response = client.post(
        f"/api/settings/{area.name}/validate", json={"value": value}, headers=CSRF
    )
    return response


def error(response: httpx.Response) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def locs(response: httpx.Response) -> list[Loc]:
    return [tuple(issue["loc"]) for issue in response.json()["error"]["issues"]]


# ------------------------------------------------------------------ read and save


@EACH_AREA
def test_seeded_at_version_1(authed: TestClient, area: Area) -> None:
    got = get(authed, area)
    assert (got["version"], got["updated_by"]) == (1, None)


@EACH_AREA
def test_a_save_bumps_the_version_and_applies(authed: TestClient, area: Area) -> None:
    me = Me.model_validate(authed.get("/api/me").json())
    value = area.edit(get(authed, area)["value"])

    response = put(authed, area, value, base_version=1)

    assert response.status_code == 200, response.text
    saved = response.json()
    assert (saved["value"], saved["version"], saved["updated_by"]["id"]) == (value, 2, me.id)
    assert get(authed, area) == saved


@EACH_AREA
def test_a_stale_base_is_refused(authed: TestClient, area: Area) -> None:
    seeded = get(authed, area)["value"]
    assert put(authed, area, area.edit(seeded), base_version=1).status_code == 200
    first = get(authed, area)

    response = put(authed, area, seeded, base_version=1)

    assert error(response) == (409, "stale_version")
    assert get(authed, area) == first


@EACH_AREA
def test_every_problem_at_once(authed: TestClient, area: Area) -> None:
    """Field rules and cross-field rules come back together, each at its field."""
    response = put(authed, area, area.invalid(get(authed, area)["value"]), base_version=1)

    assert error(response) == (422, "validation_error")
    assert locs(response) == area.locs
    assert get(authed, area)["version"] == 1


# ------------------------------------------------------------------ validate-only


@EACH_AREA
def test_validate_a_valid_document(authed: TestClient, area: Area) -> None:
    before = get(authed, area)

    response = validate(authed, area, area.edit(before["value"]))

    assert response.status_code == 204, response.text
    assert get(authed, area) == before


@EACH_AREA
def test_validate_gives_the_issues_a_save_would(authed: TestClient, area: Area) -> None:
    before = get(authed, area)
    value = area.invalid(before["value"])

    checked = validate(authed, area, value)
    saved = put(authed, area, value, base_version=1)

    assert error(checked) == (422, "validation_error")
    assert checked.json() == saved.json()
    assert get(authed, area) == before


def test_validate_leaves_a_removal_to_the_save(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    """A repo still in use is refused by the save (409 `in_use`), not by validate-only."""
    create_ticket("on docs", repo="docs")
    repos = Area("repos", edit=lambda v: v, invalid=lambda v: v, locs=[])

    assert validate(authed, repos, {"repos": [R]}).status_code == 204
    assert error(put(authed, repos, {"repos": [R]}, base_version=1)) == (409, "in_use")


# ------------------------------------------------------------------ request schemas


def test_settings_request_bodies_have_no_optional_fields(app: FastAPI) -> None:
    """Every property of a settings document, validate body or preview body is required, so the
    generated client never has to fill in a default."""
    schema = app.openapi()
    components = schema["components"]["schemas"]
    bodies: set[str] = set()
    for path, operations in schema["paths"].items():
        if not path.startswith("/api/settings/") or path.startswith("/api/settings/runtimes"):
            continue
        for operation in operations.values():
            content = operation.get("requestBody", {}).get("content", {})
            if "application/json" in content:
                bodies.add(content["application/json"]["schema"]["$ref"].rsplit("/", 1)[1])

    assert len(bodies) == 12  # five updates, five validate bodies, two previews
    seen: set[str] = set()
    for name in bodies:
        _assert_all_required(components, components[name], name, seen)
    assert {"SpaceDoc", "RepoEntry", "IntakeQueryEntry", "MappingRuleEntry"} <= seen


def _assert_all_required(components: dict[str, Any], node: Any, where: str, seen: set[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _assert_all_required(components, item, where, seen)
        return
    if not isinstance(node, dict):
        return

    if ref := node.get("$ref"):
        name = ref.rsplit("/", 1)[1]
        if name not in seen:
            seen.add(name)
            _assert_all_required(components, components[name], name, seen)
        return
    if "properties" in node:
        optional = set(node["properties"]) - set(node.get("required", []))
        assert not optional, f"{where}: optional {sorted(optional)}"
    for key, child in node.items():
        if key in ("properties", "items", "anyOf", "oneOf", "allOf", "additionalProperties"):
            children = child.values() if key == "properties" else [child]
            for each in children:
                _assert_all_required(components, each, where, seen)
