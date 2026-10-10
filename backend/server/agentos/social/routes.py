"""Chat routes (comments arrive in M1)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from agentos.auth.deps import CurrentUser
from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.models import ChatMessage, ChatPost
from agentos.social import service

router = APIRouter(prefix="/api/tickets/{key}", tags=["chat"], responses=ERROR_RESPONSES)


@router.get("/chat")
def list_chat(
    key: str,
    _: CurrentUser,
    ctx: Ctx,
    after_id: int | None = None,
    before_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ChatMessage]:
    return service.list_chat(ctx.db, key, after_id=after_id, before_id=before_id, limit=limit)


@router.post("/chat", status_code=201)
def post_chat(key: str, body: ChatPost, user: CurrentUser, ctx: Ctx) -> ChatMessage:
    return service.post_chat(ctx.db, key, user, body.body)
