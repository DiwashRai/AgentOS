"""Workflows (request 23B): the "Standard" seed, the admin endpoints, the Space default, copying a
workflow into new tickets, and switching a ticket's workflow (also after runs)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agentos.context import AppContext
from agentos.models import AppConfig, TicketDetail, Workflow
from agentos.pipeline.scaffold import default_pipeline
from agentos.space_store import read_area, write_area
from agentos.workflows.service import seed_standard
from tests.conftest import AddRun
from tests.run_support import CSRF, RunDriver
from tests.test_runs import put_pipeline, stage_of

CreateTicket = Callable[..., TicketDetail]
LoginFn = Callable[..., Any]


def error(response: httpx.Response) -> tuple[int, str, str]:
    body = response.json()["error"]
    return response.status_code, body["code"], body["message"]


def workflows(client: TestClient) -> list[Workflow]:
    response = client.get("/api/workflows")
    assert response.status_code == 200, response.text
    return [Workflow.model_validate(w) for w in response.json()]


def standard(client: TestClient) -> Workflow:
    return next(w for w in workflows(client) if w.name == "Standard")


def stage(stage_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": stage_id,
        "name": stage_id.title(),
        "kind": "agent",
        "phase": "build",
        "prompt": "Work on {{ticket.key}}.",
        **fields,
    }


def create(client: TestClient, name: str, stages: list[dict[str, Any]], **fields: Any) -> Any:
    body = {"name": name, "pipeline": {"stages": stages}, **fields}
    return client.post("/api/workflows", json=body, headers=CSRF)


def created(client: TestClient, name: str, stages: list[dict[str, Any]], **fields: Any) -> Workflow:
    response = create(client, name, stages, **fields)
    assert response.status_code == 201, response.text
    return Workflow.model_validate(response.json())


def update(client: TestClient, workflow: Workflow, **changes: Any) -> Any:
    body = {
        "name": workflow.name,
        "description": workflow.description,
        "pipeline": workflow.pipeline.model_dump(),
        "base_version": workflow.version,
        **changes,
    }
    return client.put(f"/api/workflows/{workflow.id}", json=body, headers=CSRF)


def switch(client: TestClient, key: str, workflow_id: int, base: int | None = None) -> Any:
    if base is None:
        base = client.get(f"/api/tickets/{key}").json()["pipeline_version"]
    body = {"workflow_id": workflow_id, "base_version": base}
    return client.post(f"/api/tickets/{key}/workflow", json=body, headers=CSRF)


def detail(response: httpx.Response) -> TicketDetail:
    assert response.status_code == 200, response.text
    return TicketDetail.model_validate(response.json())


def put_space(client: TestClient, **changes: Any) -> httpx.Response:
    current = client.get("/api/settings/space").json()
    body = {k: v for k, v in current.items() if k not in ("version", "updated_at", "updated_by")}
    body |= {"base_version": current["version"], **changes}
    response: httpx.Response = client.put("/api/settings/space", json=body, headers=CSRF)
    return response


def rail(client: TestClient, key: str) -> list[tuple[str, str]]:
    return [(s["stage_id"], s["state"]) for s in client.get(f"/api/tickets/{key}").json()["rail"]]


# ------------------------------------------------------------------ the seed


def test_standard_is_seeded_from_the_template_and_is_the_default(authed: TestClient) -> None:
    (only,) = workflows(authed)
    assert (only.name, only.default, only.version, only.updated_by) == ("Standard", True, 1, None)
    assert only.pipeline == default_pipeline()
    config = AppConfig.model_validate(authed.get("/api/config").json())
    assert config.default_workflow_id == only.id
    assert authed.get("/api/settings/space").json()["default_workflow_id"] == only.id


def test_the_seed_runs_once(authed: TestClient, ctx: AppContext) -> None:
    assert seed_standard(ctx.db) is False
    assert len(workflows(authed)) == 1


def test_any_approved_user_lists_workflows(client: TestClient, login: LoginFn) -> None:
    login("vic", "viewer")
    assert [w.name for w in workflows(client)] == ["Standard"]


# ------------------------------------------------------------------ admin endpoints


def test_create_lists_by_name(authed: TestClient) -> None:
    me = authed.get("/api/me").json()
    made = created(authed, "  helper script ", [stage("build")], description="  One shot. ")
    assert (made.name, made.description, made.version, made.default) == (
        "helper script",
        "One shot.",
        1,
        False,
    )
    assert made.updated_by is not None
    assert made.updated_by.id == me["id"]
    created(authed, "Research only", [stage("research")])
    assert [w.name for w in workflows(authed)] == ["helper script", "Research only", "Standard"]


@pytest.mark.parametrize(
    ("fields", "where"),
    [
        pytest.param({"name": "   "}, "name", id="blank"),
        pytest.param({"name": "x" * 61}, "name", id="long-name"),
        pytest.param({"name": "STANDARD"}, "name: a workflow named", id="taken-any-case"),
        pytest.param({"description": "d" * 501}, "description", id="long-description"),
    ],
)
def test_create_validates_fields(authed: TestClient, fields: dict[str, Any], where: str) -> None:
    status, code, message = error(create(authed, **{"name": "W", "stages": [stage("a")]} | fields))
    assert (status, code) == (422, "validation_error")
    assert where in message, message


def test_names_are_unique_ignoring_unicode_case(authed: TestClient) -> None:
    created(authed, "Straße", [stage("a")])
    status, code, _ = error(create(authed, "STRASSE", [stage("a")]))
    assert (status, code) == (422, "validation_error")


@pytest.mark.parametrize(
    ("stages", "code"),
    [
        pytest.param([stage("a"), stage("a")], "invalid_pipeline", id="duplicate-id"),
        pytest.param([stage("a", runtime="gemini")], "invalid_pipeline", id="unknown-runtime"),
        pytest.param([stage("a", kind="shell", command=" ")], "invalid_pipeline", id="no-command"),
        pytest.param([stage("a", prompt="{{ ticket.nope.x }}")], "render_error", id="prompt"),
    ],
)
def test_create_checks_the_pipeline(
    authed: TestClient, stages: list[dict[str, Any]], code: str
) -> None:
    assert error(create(authed, "W", stages))[:2] == (422, code)
    assert len(workflows(authed)) == 1


def test_update_bumps_the_version_and_refuses_a_stale_base(authed: TestClient) -> None:
    made = created(authed, "Quick", [stage("a")])
    response = update(authed, made, name="quick", pipeline={"stages": [stage("b")]})
    assert response.status_code == 200, response.text
    saved = Workflow.model_validate(response.json())
    assert (saved.name, saved.version, [s.id for s in saved.pipeline.stages]) == (
        "quick",  # its own name in another case is fine
        2,
        ["b"],
    )
    assert error(update(authed, made))[:2] == (409, "stale_version")
    assert error(update(authed, saved, name="Standard"))[:2] == (422, "validation_error")
    missing = saved.model_copy(update={"id": 999})
    assert error(update(authed, missing))[:2] == (404, "not_found")


def test_delete_keeps_the_record_on_tickets_but_not_the_default(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    made = created(authed, "Quick", [stage("a")])
    key = create_ticket(workflow_id=made.id).key
    assert authed.delete(f"/api/workflows/{made.id}", headers=CSRF).status_code == 204
    ticket = TicketDetail.model_validate(authed.get(f"/api/tickets/{key}").json())
    assert ticket.workflow is not None
    assert (ticket.workflow.id, ticket.workflow.name, ticket.workflow.version) == (None, "Quick", 1)
    assert [s.id for s in ticket.pipeline.stages] == ["a"]
    assert error(authed.delete(f"/api/workflows/{made.id}", headers=CSRF))[:2] == (404, "not_found")
    status, code, message = error(
        authed.delete(f"/api/workflows/{standard(authed).id}", headers=CSRF)
    )
    assert (status, code) == (409, "in_use")
    assert "default" in message


def test_writes_are_admin_only(client: TestClient, login: LoginFn) -> None:
    login("wanda", "writer")
    assert create(client, "W", [stage("a")]).status_code == 403
    assert update(client, standard(client)).status_code == 403
    assert client.delete(f"/api/workflows/{standard(client).id}", headers=CSRF).status_code == 403


# ------------------------------------------------------------------ the Space default


def test_the_space_settings_set_the_default(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    made = created(authed, "Quick", [stage("a")])
    response = put_space(authed, default_workflow_id=made.id)
    assert response.status_code == 200, response.text
    assert response.json()["default_workflow_id"] == made.id
    assert [w.name for w in workflows(authed) if w.default] == ["Quick"]
    assert AppConfig.model_validate(authed.get("/api/config").json()).default_workflow_id == made.id
    assert [s.id for s in create_ticket().pipeline.stages] == ["a"]
    # omitted keeps the default; an unknown one is refused
    space = authed.get("/api/settings/space").json()
    body = {k: space[k] for k in ("name", "default_repo", "key_prefix", "jira_projects")}
    body |= {"claim_timeout_s": space["claim_timeout_s"], "base_version": space["version"]}
    assert authed.put("/api/settings/space", json=body, headers=CSRF).status_code == 200
    assert authed.get("/api/settings/space").json()["default_workflow_id"] == made.id
    status, code, message = error(put_space(authed, default_workflow_id=999))
    assert (status, code) == (422, "validation_error")
    assert "default_workflow_id" in message
    # the old default can be deleted now
    old = standard(authed).id
    assert authed.delete(f"/api/workflows/{old}", headers=CSRF).status_code == 204


# ------------------------------------------------------------------ new tickets


def test_a_new_ticket_copies_its_workflow(authed: TestClient, create_ticket: CreateTicket) -> None:
    made = created(authed, "Quick", [stage("a", effort="low")])
    plain = create_ticket("Default")
    assert plain.workflow is not None
    assert (plain.workflow.name, plain.workflow.version) == ("Standard", 1)
    assert plain.workflow.id == standard(authed).id
    picked = create_ticket("Picked", workflow_id=made.id)
    assert picked.workflow is not None
    assert (picked.workflow.id, picked.workflow.name) == (made.id, "Quick")
    assert picked.pipeline == made.pipeline
    # a later edit of the workflow doesn't reach the ticket
    assert update(authed, made, pipeline={"stages": [stage("b")]}).status_code == 200
    again = TicketDetail.model_validate(authed.get(f"/api/tickets/{picked.key}").json())
    assert [s.id for s in again.pipeline.stages] == ["a"]
    assert again.workflow == picked.workflow


def test_an_unknown_workflow_is_refused_at_creation(authed: TestClient) -> None:
    body = {"title": "T", "workflow_id": 999}
    status, code, message = error(authed.post("/api/tickets", json=body, headers=CSRF))
    assert (status, code) == (422, "validation_error")
    assert "workflow_id" in message
    assert authed.get("/api/tickets").json() == []


def test_a_no_repo_ticket_gets_requires_repo_stages_disabled(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    made = created(authed, "Ship", [stage("a"), stage("pr", requires_repo=True)])
    ticket = create_ticket(repo=None, workflow_id=made.id)
    assert [(s.id, s.enabled) for s in ticket.pipeline.stages] == [("a", True), ("pr", False)]


def test_tickets_from_before_workflows_have_none(
    authed: TestClient, ctx: AppContext, create_ticket: CreateTicket
) -> None:
    key = create_ticket().key
    with ctx.db.tx() as conn:
        conn.execute(
            "UPDATE tickets SET workflow_id = NULL, workflow_name = NULL, workflow_version = NULL"
        )
    assert authed.get(f"/api/tickets/{key}").json()["workflow"] is None


# ------------------------------------------------------------------ switching


def test_switch_replaces_the_pipeline_and_records_the_workflow(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    made = created(authed, "Quick", [stage("a")])
    ticket = create_ticket()
    switched = detail(switch(authed, ticket.key, made.id, ticket.pipeline_version))
    assert switched.pipeline == made.pipeline
    assert switched.pipeline_version == ticket.pipeline_version + 1
    assert switched.workflow is not None
    assert (switched.workflow.id, switched.workflow.name) == (made.id, "Quick")
    assert switched.next_stage == "a"
    # editing the ticket's pipeline afterwards keeps the record
    put_pipeline(authed, ticket.key, lambda p: stage_of(p, "a").update(effort="high"))
    edited = TicketDetail.model_validate(authed.get(f"/api/tickets/{ticket.key}").json())
    assert edited.workflow == switched.workflow


def test_switch_conflicts_and_unknowns(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    made = created(authed, "Quick", [stage("a")])
    ticket = create_ticket()
    assert error(switch(authed, ticket.key, made.id, base=99))[:2] == (409, "stale_version")
    assert error(switch(authed, ticket.key, 999))[:2] == (404, "not_found")
    assert error(switch(authed, "NOPE-1", made.id, base=1))[:2] == (404, "ticket_not_found")
    runs.start(ticket.key)
    assert error(switch(authed, ticket.key, made.id))[:2] == (409, "run_active")


def test_switch_disables_requires_repo_stages_on_a_no_repo_ticket(
    authed: TestClient, create_ticket: CreateTicket
) -> None:
    made = created(authed, "Ship", [stage("a"), stage("pr", requires_repo=True)])
    ticket = create_ticket(repo=None)
    switched = detail(switch(authed, ticket.key, made.id))
    assert [(s.id, s.enabled) for s in switched.pipeline.stages] == [("a", True), ("pr", False)]


def test_switch_rechecks_the_copy(
    authed: TestClient, ctx: AppContext, create_ticket: CreateTicket
) -> None:
    """A workflow pinned to a runtime that is gone can't be applied (like a pipeline save). The
    runtimes save refuses that removal, so the runtime is removed behind its back here."""
    made = created(authed, "Pinned", [stage("a", runtime="claude")])
    ticket = create_ticket()
    runtimes = authed.get("/api/settings/runtimes").json()
    body = {"runtimes": runtimes["runtimes"][:1], "base_version": runtimes["version"]}
    blocked = authed.put("/api/settings/runtimes", json=body, headers=CSRF)
    status, code, message = error(blocked)
    assert (status, code) == (409, "in_use")
    assert "the workflow Pinned uses it" in message
    with ctx.db.tx() as conn:
        row = read_area(conn, "runtimes")
        assert row is not None
        value = {"runtimes": [r for r in row.value["runtimes"] if r["name"] != "claude"]}
        write_area(conn, "runtimes", value, version=row.version + 1, actor_id=None)
    ctx.space_store.invalidate()
    status, code, message = error(switch(authed, ticket.key, made.id))
    assert (status, code) == (422, "invalid_pipeline")
    assert "unknown runtime 'claude'" in message
    assert authed.get(f"/api/tickets/{ticket.key}").json()["workflow"]["name"] == "Standard"


def test_after_runs_the_rail_keeps_shared_stages_by_id(
    authed: TestClient, create_ticket: CreateTicket, add_run: AddRun
) -> None:
    """Stages the new pipeline shares keep their run history; new ones are not started."""
    key = create_ticket().key
    add_run(key, "succeeded", [("plan", "succeeded"), ("implement", "succeeded")])
    pipeline = copy.deepcopy(standard(authed).pipeline.model_dump())
    plan, implement = pipeline["stages"][0], pipeline["stages"][1]
    verify = stage("verify", phase="verify")
    made = created(authed, "Verification heavy", [plan, implement, verify])
    switched = detail(switch(authed, key, made.id))
    assert rail(authed, key) == [("plan", "done"), ("implement", "done"), ("verify", "pending")]
    assert switched.next_stage == "verify"


def test_after_runs_a_pipeline_with_no_shared_stage_starts_over(
    authed: TestClient, create_ticket: CreateTicket, add_run: AddRun
) -> None:
    key = create_ticket().key
    add_run(key, "gated", [("plan", "succeeded")])
    made = created(authed, "Research only", [stage("research"), stage("write-up")])
    switched = detail(switch(authed, key, made.id))
    assert rail(authed, key) == [("research", "pending"), ("write-up", "pending")]
    assert switched.next_stage == "research"
