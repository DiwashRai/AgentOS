"""Worker protocol routes (SPEC §8, §10). Authenticated by job or worker tokens (the lease: by a
client token), not cookies."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import PlainTextResponse

from agentos.errors import ERROR_RESPONSES, ApiError
from agentos.http_deps import Ctx
from agentos.launchers.local import WORKER_PATH
from agentos.models import (
    ClaimRequest,
    ClaimResponse,
    EventsRequest,
    EventsResponse,
    JobPackage,
    JournalRequest,
    JournalResponse,
    LeaseRequest,
    LeaseResponse,
    ResultData,
    SyncRequest,
    SyncResponse,
)
from agentos.worker_api import service
from agentos.worker_api.deps import ClientBearer, Instance, RunToken

router = APIRouter(tags=["worker"], responses=ERROR_RESPONSES)

LEASE_RETRY_S = 1.0  # how often a waiting lease looks for a run


@router.get("/api/worker/worker.py", response_class=PlainTextResponse)
def worker_source() -> str:
    return WORKER_PATH.read_text(encoding="utf-8")


# Bearer: the job token. Every other worker route: the worker token bound at claim.
@router.post("/api/worker/claim")
def claim(body: ClaimRequest, token: RunToken, ctx: Ctx) -> ClaimResponse:
    return ClaimResponse(manifest=service.claim(ctx.db, token, body.worker_token))


@router.post("/api/worker/events")
def events(body: EventsRequest, token: RunToken, ctx: Ctx) -> EventsResponse:
    return EventsResponse(cancel=service.post_events(ctx.db, token, body.events))


@router.post("/api/worker/sync")
def sync(body: SyncRequest, token: RunToken, ctx: Ctx) -> SyncResponse:
    return service.sync(ctx.db, token, body.stage_id, body.files)


@router.put("/api/worker/sync-back", status_code=204)
async def sync_back(
    sha256: str, files: Annotated[int, Query(ge=1)], token: RunToken, ctx: Ctx, request: Request
) -> None:
    run_id = await asyncio.to_thread(service.check_live_run, ctx.db, token)
    await _store_sync_back(ctx, request, run_id, sha256, files)


async def _store_sync_back(
    ctx: Ctx, request: Request, run_id: int, sha256: str, files: int
) -> None:
    """The run's sync_back zip as the raw body, once the caller checked the credentials. A
    declared length over the cap is refused unread, and reading stops past the cap, so an
    oversize upload is refused (413) without being held whole; the store runs in a thread."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > service.MAX_SYNC_BACK_BYTES:
        raise ApiError(413, "too_large", f"sync_back: over {service.MAX_SYNC_BACK_BYTES} bytes")

    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > service.MAX_SYNC_BACK_BYTES:
            break
    await asyncio.to_thread(service.store_sync_back, ctx.db, run_id, bytes(data), sha256, files)


# Bearer: the job token; once the job is bound, X-AgentOS-Instance too.
@router.get("/api/worker/job", response_model=JobPackage)
def job(token: RunToken, instance: Instance, ctx: Ctx) -> Response:
    """The job's package, exactly as stored when the run was dispatched."""
    package = service.job_package(ctx.db, token, instance)
    return Response(package, media_type="application/json")


@router.post("/api/worker/journal")
def journal(body: JournalRequest, token: RunToken, instance: Instance, ctx: Ctx) -> JournalResponse:
    return service.post_journal(ctx.db, token, instance, body.records)


@router.put("/api/worker/jobs/{job_id}/sync-back", status_code=204)
async def job_sync_back(
    job_id: str,
    sha256: str,
    files: Annotated[int, Query(ge=1)],
    token: RunToken,
    instance: Instance,
    ctx: Ctx,
    request: Request,
) -> None:
    """The job's sync_back zip as the raw body; a `sync_back` record describing it follows. The
    token and instance are checked before anything is read."""
    run_id = await asyncio.to_thread(service.check_upload, ctx.db, token, instance, job_id)
    await _store_sync_back(ctx, request, run_id, sha256, files)


@router.post("/api/worker/complete", status_code=204)
def complete(body: ResultData, token: RunToken, ctx: Ctx) -> None:
    service.complete(ctx.db, token, body)


@router.post("/api/worker/lease")
async def lease(
    body: LeaseRequest, token: ClientBearer, ctx: Ctx, request: Request
) -> LeaseResponse:
    """A client's long poll: the job of the oldest run its user dispatched to "My client",
    waiting up to `wait_s` for one; `take: false` (a busy client's heartbeat) only records that it
    and its `alive` jobs were seen. 401/403 at once for a bad token. Async, and each db step runs
    in a thread only while it runs, so idle leases hold no threadpool thread while they wait."""
    answer = await asyncio.to_thread(service.lease_client, ctx.db, token, body.alive)
    if not body.take:
        return answer

    loop = asyncio.get_running_loop()
    deadline = loop.time() + body.wait_s
    # A client that goes away ends its lease at once, so a run queued afterwards isn't handed to
    # nobody. The body is read, so the next ASGI message is the disconnect; waiting for it sees
    # it (`Request.is_disconnected()` never does behind the CSRF `BaseHTTPMiddleware`).
    gone = asyncio.ensure_future(request.receive())
    try:
        while True:
            job = await asyncio.to_thread(
                service.take_client_run, ctx.db, token, ctx.settings.public_url
            )
            remaining = deadline - loop.time()
            if job is not None or remaining <= 0:
                return answer.model_copy(update={"job": job})
            await asyncio.wait({gone}, timeout=min(LEASE_RETRY_S, remaining))
            if gone.done():
                return answer
    finally:
        gone.cancel()
