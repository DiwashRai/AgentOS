"""Login, session and user management routes."""

from __future__ import annotations

from fastapi import APIRouter, Response

from agentos import stubs
from agentos.auth.deps import SESSION_COOKIE, CurrentUser
from agentos.errors import ERROR_RESPONSES, ApiError
from agentos.models import LoginRequest, Me, User, UserPatch

router = APIRouter(prefix="/api", tags=["auth"], responses=ERROR_RESPONSES)


@router.post("/auth/login")
def login(body: LoginRequest, response: Response) -> Me:
    # STUB: accepts any username. Real: dev auth or Jira /myself + board check (SPEC §11).
    if not (body.username or body.token):
        raise ApiError(400, "missing_credentials", "Provide a username or a token")
    response.set_cookie(SESSION_COOKIE, "stub", httponly=True, samesite="lax")
    return stubs.ME


@router.post("/auth/logout", status_code=204)
def logout(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)


@router.get("/me")
def me(user: CurrentUser) -> Me:
    return user


@router.get("/users")
def list_users(_: CurrentUser) -> list[User]:
    return stubs.USERS


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, _: CurrentUser) -> User:
    for i, u in enumerate(stubs.USERS):
        if u.id == user_id:
            stubs.USERS[i] = u.model_copy(update=body.model_dump(exclude_unset=True))
            return stubs.USERS[i]
    raise ApiError(404, "user_not_found", f"No user {user_id}")
