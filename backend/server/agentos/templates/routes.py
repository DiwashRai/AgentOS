"""Default template routes."""

from __future__ import annotations

from fastapi import APIRouter

from agentos.auth.deps import Admin, CurrentUser
from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.models import Template, TemplateUpdate
from agentos.templates import service

router = APIRouter(prefix="/api/template", tags=["template"], responses=ERROR_RESPONSES)


@router.get("")
def get_template(_: CurrentUser, ctx: Ctx) -> Template:
    return service.get_template(ctx.db)


@router.put("")
def put_template(body: TemplateUpdate, user: Admin, ctx: Ctx) -> Template:
    return service.put_template(ctx.db, ctx.runtimes.runtimes.keys(), user, body)
