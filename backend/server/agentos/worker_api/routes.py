"""Worker protocol routes (SPEC §8, §10). Authenticated by run tokens, not cookies."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.launchers.local import WORKER_PATH
from agentos.models import (
    ClaimRequest,
    ClaimResponse,
    CompleteRequest,
    EventsRequest,
    EventsResponse,
    SyncRequest,
    SyncResponse,
)
from agentos.worker_api import service
from agentos.worker_api.deps import RunToken

router = APIRouter(tags=["worker"], responses=ERROR_RESPONSES)


@router.get("/api/worker/worker.py", response_class=PlainTextResponse)
def worker_source() -> str:
    return WORKER_PATH.read_text(encoding="utf-8")


# Bearer: the dispatch token. Every other worker route: the worker token bound at claim.
@router.post("/api/worker/claim")
def claim(body: ClaimRequest, token: RunToken, ctx: Ctx) -> ClaimResponse:
    snapshot = ctx.snapshot()
    return ClaimResponse(
        manifest=service.claim(ctx.db, snapshot.runtimes, snapshot.space, token, body.worker_token)
    )


@router.post("/api/worker/events")
def events(body: EventsRequest, token: RunToken, ctx: Ctx) -> EventsResponse:
    return EventsResponse(cancel=service.post_events(ctx.db, token, body.events))


@router.post("/api/worker/sync")
def sync(body: SyncRequest, token: RunToken, ctx: Ctx) -> SyncResponse:
    return SyncResponse(versions=service.sync(ctx.db, token, body.stage_id, body.files))


@router.post("/api/worker/complete", status_code=204)
def complete(body: CompleteRequest, token: RunToken, ctx: Ctx) -> None:
    service.complete(ctx.db, token, body)
