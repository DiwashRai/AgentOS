"""GET /api/stream: server-sent notifications (`StreamEvent` JSON per `data:` line).

Each event comes from the in-process broker (SPEC §10). The stream ends when the client
disconnects (Starlette cancels it), when the subscriber falls too far behind or the server shuts
down (the broker ends the subscription); the browser's EventSource then reconnects and the client
refetches everything.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from agentos.auth.deps import CurrentUser
from agentos.broker import Broker
from agentos.http_deps import Ctx

router = APIRouter(prefix="/api", tags=["stream"])

PING_INTERVAL_S = 15.0


async def events(broker: Broker) -> AsyncGenerator[str, None]:
    """The SSE body: `: connected`, then one `data:` line per event and a `: ping` comment after
    every PING_INTERVAL_S without one, until the subscription ends."""
    sub = broker.subscribe()
    try:
        yield ": connected\n\n"
        while True:
            try:
                event = await asyncio.wait_for(sub.get(), PING_INTERVAL_S)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if event is None:
                return
            yield f"data: {event.model_dump_json()}\n\n"
    finally:
        broker.unsubscribe(sub)


@router.get("/stream", response_class=StreamingResponse)
def stream(_: CurrentUser, ctx: Ctx) -> StreamingResponse:
    return StreamingResponse(
        events(ctx.broker), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )
