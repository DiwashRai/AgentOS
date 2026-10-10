"""Ticket and pipeline routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from agentos import stubs
from agentos.auth.deps import CurrentUser
from agentos.errors import ERROR_RESPONSES
from agentos.models import (
    PipelineUpdate,
    Priority,
    TicketCreate,
    TicketDetail,
    TicketPatch,
    TicketSummary,
    TicketType,
)

router = APIRouter(prefix="/api/tickets", tags=["tickets"], responses=ERROR_RESPONSES)


@router.get("")
def list_tickets(
    _: CurrentUser,
    q: Annotated[str | None, Query(max_length=200)] = None,
    type: TicketType | None = None,
    priority: Priority | None = None,
    include_closed: bool = False,
) -> list[TicketSummary]:
    tickets = stubs.STORE.list_tickets(q, include_closed)
    return [
        t
        for t in tickets
        if (type is None or t.type == type) and (priority is None or t.priority == priority)
    ]


@router.post("", status_code=201)
def create_ticket(body: TicketCreate, _: CurrentUser) -> TicketDetail:
    return stubs.STORE.create(body)


@router.get("/{key}")
def get_ticket(key: str, _: CurrentUser) -> TicketDetail:
    return stubs.STORE.get(key)


@router.patch("/{key}")
def patch_ticket(key: str, body: TicketPatch, _: CurrentUser) -> TicketDetail:
    return stubs.STORE.patch(key, body)


@router.put("/{key}/pipeline")
def put_pipeline(key: str, body: PipelineUpdate, _: CurrentUser) -> TicketDetail:
    return stubs.STORE.put_pipeline(key, body.pipeline, body.base_version)
