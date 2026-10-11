"""Intake routes (request 14)."""

from __future__ import annotations

from fastapi import APIRouter

from agentos.auth.deps import CurrentUser
from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.intake import service
from agentos.models import IntakeQuery

router = APIRouter(prefix="/api/intake", tags=["intake"], responses=ERROR_RESPONSES)


@router.get("")
def list_intake(_: CurrentUser, ctx: Ctx) -> list[IntakeQuery]:
    """Each configured intake query with the Jira issues it matches that aren't in AgentOS yet
    (newest-updated first, capped). "Take in" one with POST /api/tickets/import."""
    return service.list_intake(
        ctx.db,
        ctx.space.intake.queries,
        ctx.jira_source,
        ctx.settings.jira_projects,
        ctx.intake_cache,
    )
