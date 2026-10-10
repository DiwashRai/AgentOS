"""Login, session and user management routes (SPEC §10, §11)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from agentos.auth import service
from agentos.auth.deps import SESSION_COOKIE, Admin, CurrentUser
from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.models import LoginRequest, Me, Person, User, UserPatch

router = APIRouter(prefix="/api", tags=["auth"], responses=ERROR_RESPONSES)


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response, ctx: Ctx) -> Me:
    session = service.login(
        ctx.db,
        ctx.settings,
        body,
        host=request.headers.get("host"),
        proxied=any(h in request.headers for h in service.PROXY_HEADERS),
    )
    response.set_cookie(
        SESSION_COOKIE,
        session.token,
        max_age=int(service.SESSION_TTL.total_seconds()),
        path="/",
        secure=service.cookie_secure(ctx.settings),
        httponly=True,
        samesite="lax",
    )
    return session.user


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, ctx: Ctx) -> None:
    service.logout(ctx.db, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=service.cookie_secure(ctx.settings),
        httponly=True,
        samesite="lax",
    )


@router.get("/me")
def me(user: CurrentUser) -> Me:
    return user


@router.get("/people")
def list_people(_: CurrentUser, ctx: Ctx) -> list[Person]:
    """Everyone who can be a ticket owner or be mentioned (any signed-in user may list)."""
    return service.list_people(ctx.db)


@router.get("/users")
def list_users(_: Admin, ctx: Ctx) -> list[User]:
    return service.list_users(ctx.db)


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, user: Admin, ctx: Ctx) -> User:
    return service.patch_user(ctx.db, ctx.settings, user, user_id, body)
