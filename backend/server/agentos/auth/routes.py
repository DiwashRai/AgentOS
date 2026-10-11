"""Login, session and user management routes (SPEC §10, §11)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from agentos.auth import service
from agentos.auth.deps import SESSION_COOKIE, Admin, CurrentUser, SignedIn
from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.models import LoginRequest, Me, Person, User, UserPatch

router = APIRouter(prefix="/api", tags=["auth"], responses=ERROR_RESPONSES)


def _client_ip(request: Request, trust_proxy: bool) -> str:
    """The address to rate-limit by. Proxy headers count only with AGENTOS_TRUST_PROXY=1:
    `CF-Connecting-IP`, else the last `X-Forwarded-For` hop (the one our proxy appended)."""
    if trust_proxy:
        if cf := request.headers.get("cf-connecting-ip", "").strip():
            return cf
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",")]
        if hops[-1]:
            return hops[-1]
    return request.client.host if request.client else ""


@router.post("/auth/login")
def login(body: LoginRequest, request: Request, response: Response, ctx: Ctx) -> Me:
    session = service.login(
        ctx.db,
        ctx.settings,
        body,
        host=request.headers.get("host"),
        proxied=any(h in request.headers for h in service.PROXY_HEADERS),
        client_ip=_client_ip(request, ctx.settings.trust_proxy),
        jira=ctx.jira,
        limiter=ctx.login_limiter,
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
def me(user: SignedIn) -> Me:
    """Works for a `pending` user too, so the UI can show the waiting-for-approval page."""
    return user


@router.get("/people")
def list_people(_: CurrentUser, ctx: Ctx) -> list[Person]:
    """Everyone who can be a ticket owner or be mentioned (any signed-in user may list)."""
    return service.list_people(ctx.db)


@router.get("/users")
def list_users(_: Admin, ctx: Ctx) -> list[User]:
    return service.list_users(ctx.db, ctx.settings)


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, user: Admin, ctx: Ctx) -> User:
    return service.patch_user(ctx.db, ctx.settings, user, user_id, body)
