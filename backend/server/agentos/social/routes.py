"""Chat routes (comments arrive in M1)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from agentos import stubs
from agentos.auth.deps import CurrentUser
from agentos.errors import ERROR_RESPONSES
from agentos.models import ChatMessage, ChatPost

router = APIRouter(prefix="/api/tickets/{key}", tags=["chat"], responses=ERROR_RESPONSES)


@router.get("/chat")
def list_chat(
    key: str,
    _: CurrentUser,
    after_id: int | None = None,
    before_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ChatMessage]:
    return stubs.STORE.list_chat(key, after_id, before_id, limit)


@router.post("/chat", status_code=201)
def post_chat(key: str, body: ChatPost, _: CurrentUser) -> ChatMessage:
    return stubs.STORE.post_chat(key, body.body)
