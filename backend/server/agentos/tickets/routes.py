"""Ticket and pipeline routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from agentos.auth.deps import CurrentUser, Writer
from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.models import (
    PipelineUpdate,
    Priority,
    TicketCreate,
    TicketDetail,
    TicketPatch,
    TicketSummary,
    TicketType,
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
        user.id,
        q=q,
        ticket_type=type,
        priority=priority,
        include_closed=include_closed,
    )


@router.post("", status_code=201)
def create_ticket(body: TicketCreate, user: Writer, ctx: Ctx) -> TicketDetail:
    return service.create_ticket(ctx.db, ctx.settings, user, body)


@router.get("/{key}")
def get_ticket(key: str, user: CurrentUser, ctx: Ctx) -> TicketDetail:
    return service.get_ticket(ctx.db, key, user.id)


@router.patch("/{key}")
def patch_ticket(key: str, body: TicketPatch, user: Writer, ctx: Ctx) -> TicketDetail:
    return service.patch_ticket(ctx.db, key, user, body)


@router.put("/{key}/pipeline")
def put_pipeline(key: str, body: PipelineUpdate, user: Writer, ctx: Ctx) -> TicketDetail:
    return service.put_pipeline(ctx.db, ctx.runtimes, key, user, body)
