"""Request authentication dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from agentos import stubs
from agentos.errors import ApiError
from agentos.models import Me

SESSION_COOKIE = "agentos_session"


def current_user(request: Request) -> Me:
    # STUB: any session cookie is the stub admin. Real: sha256 lookup in `sessions`, role checks.
    if not request.cookies.get(SESSION_COOKIE):
        raise ApiError(401, "unauthorized", "Not signed in")
    return stubs.ME


CurrentUser = Annotated[Me, Depends(current_user)]
