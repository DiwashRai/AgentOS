"""Ticket and pipeline routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response

from agentos.auth.deps import CurrentUser, Writer
from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.models import (
    ImportRequest,
    ImportResult,
    PipelineUpdate,
    Priority,
    ResolveRequest,
    TicketCreate,
    TicketDetail,
    TicketPatch,
    TicketSummary,
    TicketType,
    WorkflowSwitch,
)
from agentos.tickets import service

router = APIRouter(prefix="/api/tickets", tags=["tickets"], responses=ERROR_RESPONSES)


@router.get("")
def list_tickets(
    user: CurrentUser,
    ctx: Ctx,
    q: Annotated[str | None, Query(max_length=200)] = None,
    type: TicketType | None = None,
    priority: Priority | None = None,
    include_closed: bool = False,
) -> list[TicketSummary]:
    return service.list_tickets(
        ctx.db,
        ctx.space,
        user.id,
        q=q,
        ticket_type=type,
        priority=priority,
        include_closed=include_closed,
    )


@router.post("", status_code=201)
def create_ticket(body: TicketCreate, user: Writer, ctx: Ctx) -> TicketDetail:
    return service.create_ticket(ctx.db, ctx.broker, ctx.space, user, body)


@router.post("/import", responses={201: {"model": ImportResult, "description": "Imported"}})
def import_ticket(body: ImportRequest, user: Writer, ctx: Ctx, response: Response) -> ImportResult:
    """Import a Jira issue by key or browse link: 201 with `created: true`, or 200 with
    `created: false` and the ticket it was imported as before. A `base_branch` field mapping
    (Settings > Field mappings) sets a repo ticket's base branch from its Jira field."""
    result = service.import_ticket(
        ctx.db, ctx.broker, ctx.space, ctx.jira_source, user, body, ctx.mappings
    )
    response.status_code = 201 if result.created else 200
    return result


@router.get("/{key}")
def get_ticket(key: str, user: CurrentUser, ctx: Ctx) -> TicketDetail:
    return service.get_ticket(ctx.db, ctx.space, key, user.id)


@router.patch("/{key}")
def patch_ticket(key: str, body: TicketPatch, user: Writer, ctx: Ctx) -> TicketDetail:
    return service.patch_ticket(ctx.db, ctx.broker, ctx.space, key, user, body)


@router.post("/{key}/resolve")
def resolve_ticket(key: str, body: ResolveRequest, user: Writer, ctx: Ctx) -> TicketDetail:
    """Close the ticket: `completed` → state done, `abandoned` → state closed (both stay on the
    board as `done`). 409 `run_active` during a run. Reopen with PATCH `state: open`."""
    return service.resolve_ticket(ctx.db, ctx.broker, ctx.space, key, user, body)


@router.put("/{key}/pipeline")
def put_pipeline(key: str, body: PipelineUpdate, user: Writer, ctx: Ctx) -> TicketDetail:
    return service.put_pipeline(ctx.db, ctx.broker, ctx.runtimes, ctx.space, key, user, body)


@router.post("/{key}/workflow")
def switch_workflow(key: str, body: WorkflowSwitch, user: Writer, ctx: Ctx) -> TicketDetail:
    """Replace the ticket's pipeline with a copy of a workflow's (allowed after runs; 409
    `run_active` during one, `stale_version` on a stale `base_version`; 404 `not_found` for an
    unknown workflow). The rail keeps the run history of stages the new pipeline shares by id;
    new stages start pending."""
    return service.switch_workflow(ctx.db, ctx.broker, ctx.runtimes, ctx.space, key, user, body)
