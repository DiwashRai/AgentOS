"""Workflow routes (request 23B): any approved user lists them (the New ticket form and the
Pipeline tab pick one); admins create, edit and delete them and pick the default."""

from __future__ import annotations

from fastapi import APIRouter

from agentos.auth.deps import Admin, CurrentUser
from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.models import Workflow, WorkflowCreate, WorkflowUpdate
from agentos.workflows import service

router = APIRouter(prefix="/api/workflows", tags=["workflows"], responses=ERROR_RESPONSES)


@router.get("")
def list_workflows(_: CurrentUser, ctx: Ctx) -> list[Workflow]:
    """Every workflow, by name, with its full pipeline."""
    return service.list_workflows(ctx.db)


@router.post("", status_code=201)
def create_workflow(body: WorkflowCreate, user: Admin, ctx: Ctx) -> Workflow:
    return service.create_workflow(ctx.db, user, body)


@router.put("/{workflow_id}")
def update_workflow(workflow_id: int, body: WorkflowUpdate, user: Admin, ctx: Ctx) -> Workflow:
    return service.update_workflow(ctx.db, user, workflow_id, body)


@router.post("/{workflow_id}/default")
def make_default(workflow_id: int, _: Admin, ctx: Ctx) -> Workflow:
    """Make this workflow the one a new ticket gets when it names none (`/api/config`
    `default_workflow_id`); the previous default stops being it. Answers the workflow, now
    `default`. 404 `not_found` for an unknown id."""
    return service.make_default(ctx.db, workflow_id)


@router.delete("/{workflow_id}", status_code=204)
def delete_workflow(workflow_id: int, _: Admin, ctx: Ctx) -> None:
    """The default workflow can't be deleted (409 `in_use`). Tickets copied from it keep their
    pipeline and their `workflow` record (its `id` becomes null)."""
    service.delete_workflow(ctx.db, workflow_id)
