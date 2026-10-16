"""Settings > My client: the signed-in user's own client tokens (cookie session + CSRF)."""

from __future__ import annotations

from fastapi import APIRouter

from agentos.auth.deps import CurrentUser, Writer
from agentos.clients import service
from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.models import ClientToken, ClientTokenCreate, ClientTokenCreated

router = APIRouter(prefix="/api/me/clients", tags=["clients"], responses=ERROR_RESPONSES)


@router.get("")
def list_clients(user: CurrentUser, ctx: Ctx) -> list[ClientToken]:
    return service.list_own(ctx.db, user.id)


@router.post("", status_code=201)
def create_client(body: ClientTokenCreate, user: Writer, ctx: Ctx) -> ClientTokenCreated:
    """The token is in this answer only; store it on the client machine."""
    return service.create(ctx.db, user, body, ctx.settings.public_url)


@router.delete("/{client_id}", status_code=204)
def revoke_client(client_id: int, user: CurrentUser, ctx: Ctx) -> None:
    """Its next lease gets 401. Runs it already started end through their own job tokens."""
    service.revoke_own(ctx.db, user.id, client_id)
