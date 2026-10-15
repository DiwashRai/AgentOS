"""Worker protocol routes (SPEC §8, §10). Authenticated by run tokens (the poll: by a client
token), not cookies."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.launchers.local import WORKER_PATH
from agentos.models import (
    ClaimRequest,
    ClaimResponse,
    ClientPollRequest,
    ClientPollResponse,
    CompleteRequest,
    EventsRequest,
    EventsResponse,
    SyncRequest,
    SyncResponse,
)
from agentos.worker_api import service
from agentos.worker_api.deps import ClientBearer, RunToken

router = APIRouter(tags=["worker"], responses=ERROR_RESPONSES)

POLL_RETRY_S = 1.0  # how often a waiting poll looks for a run


@router.get("/api/worker/worker.py", response_class=PlainTextResponse)
def worker_source() -> str:
    return WORKER_PATH.read_text(encoding="utf-8")


# Bearer: the dispatch token. Every other worker route: the worker token bound at claim.
@router.post("/api/worker/claim")
def claim(body: ClaimRequest, token: RunToken, ctx: Ctx) -> ClaimResponse:
    return ClaimResponse(
        manifest=service.claim(
            ctx.db, ctx.broker, ctx.runtimes, ctx.space, token, body.worker_token
        )
    )


@router.post("/api/worker/events")
def events(body: EventsRequest, token: RunToken, ctx: Ctx) -> EventsResponse:
    return EventsResponse(cancel=service.post_events(ctx.db, ctx.broker, token, body.events))


@router.post("/api/worker/sync")
def sync(body: SyncRequest, token: RunToken, ctx: Ctx) -> SyncResponse:
    return SyncResponse(versions=service.sync(ctx.db, ctx.broker, token, body.stage_id, body.files))


@router.post("/api/worker/complete", status_code=204)
def complete(body: CompleteRequest, token: RunToken, ctx: Ctx) -> None:
    service.complete(ctx.db, ctx.broker, token, body)


@router.post("/api/worker/poll")
async def poll(
    body: ClientPollRequest, token: ClientBearer, ctx: Ctx, request: Request
) -> ClientPollResponse:
    """A client's long poll: the oldest run its user dispatched to "My client", waiting up to
    `wait_s` for one. 401/403 at once for a bad token. Async, and each db step runs in a thread
    only while it runs, so idle polls hold no threadpool thread while they wait."""
    await asyncio.to_thread(service.poll_client, ctx.db, token)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + body.wait_s
    # A client that goes away ends its poll at once, so a run queued afterwards isn't handed to
    # nobody. The body is read, so the next ASGI message is the disconnect; waiting for it sees
    # it (`Request.is_disconnected()` never does behind the CSRF `BaseHTTPMiddleware`).
    gone = asyncio.ensure_future(request.receive())
    try:
        while True:
            run = await asyncio.to_thread(
                service.take_client_run, ctx.db, ctx.broker, token, ctx.settings.public_url
            )
            remaining = deadline - loop.time()
            if run is not None or remaining <= 0:
                return ClientPollResponse(run=run)
            await asyncio.wait({gone}, timeout=min(POLL_RETRY_S, remaining))
            if gone.done():
                return ClientPollResponse(run=None)
    finally:
        gone.cancel()
