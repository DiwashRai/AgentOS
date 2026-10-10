"""Request authentication dependencies: the session cookie → the signed-in user, plus role gates.

Use the narrowest annotation a route needs (SPEC §10 role column):
    `_: CurrentUser`  any signed-in user (viewer and up)
    `_: Writer`       writer or admin
    `_: Admin`        admin only
Role dependencies run before body validation, so a too-low role gets 403 even with a bad body.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from agentos.auth import service
from agentos.errors import ApiError
from agentos.http_deps import Ctx
from agentos.models import Me, Role

SESSION_COOKIE = "agentos_session"


def current_user(request: Request, ctx: Ctx) -> Me:
    """401 `unauthorized` unless the cookie names a live session of an enabled user."""
    return service.user_for_session(ctx.db, request.cookies.get(SESSION_COOKIE))


CurrentUser = Annotated[Me, Depends(current_user)]


def _require(minimum: Role, user: Me) -> Me:
    if not service.has_role(user, minimum):
        raise ApiError(403, "forbidden", f"Requires the {minimum} role")
    return user


def require_writer(user: CurrentUser) -> Me:
    return _require("writer", user)


def require_admin(user: CurrentUser) -> Me:
    return _require("admin", user)


Writer = Annotated[Me, Depends(require_writer)]
Admin = Annotated[Me, Depends(require_admin)]
