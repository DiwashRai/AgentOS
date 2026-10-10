"""GET /api/stream: server-sent notifications (`StreamEvent` JSON per `data:` line)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from agentos.auth.deps import CurrentUser

router = APIRouter(prefix="/api", tags=["stream"])

PING_INTERVAL_S = 15


async def _events() -> AsyncIterator[str]:
    # STUB: pings only. Real: fan-out from the in-process broker (SPEC §10).
    yield ": connected\n\n"
    while True:
        await asyncio.sleep(PING_INTERVAL_S)
        yield ": ping\n\n"


@router.get("/stream", response_class=StreamingResponse)
def stream(_: CurrentUser) -> StreamingResponse:
    return StreamingResponse(
        _events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )
