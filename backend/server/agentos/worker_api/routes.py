"""Worker protocol routes (SPEC §8, §10). Authenticated by run tokens, not cookies."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from agentos.errors import ERROR_RESPONSES, ApiError
from agentos.models import (
    ClaimRequest,
    ClaimResponse,
    CompleteRequest,
    EventsRequest,
    EventsResponse,
    SyncRequest,
    SyncResponse,
)

router = APIRouter(tags=["worker"], responses=ERROR_RESPONSES)

WORKER_SOURCE = Path(__file__).resolve().parents[3] / "worker" / "worker.py"


def _not_yet() -> ApiError:
    return ApiError(501, "not_implemented", "Worker API is not implemented yet")


@router.get("/api/worker/worker.py", response_class=PlainTextResponse)
def worker_source() -> str:
    return WORKER_SOURCE.read_text(encoding="utf-8")


@router.post("/api/worker/claim")
def claim(body: ClaimRequest) -> ClaimResponse:
    raise _not_yet()


@router.post("/api/worker/events")
def events(body: EventsRequest) -> EventsResponse:
    raise _not_yet()


@router.post("/api/worker/sync")
def sync(body: SyncRequest) -> SyncResponse:
    raise _not_yet()


@router.post("/api/worker/complete", status_code=204)
def complete(body: CompleteRequest) -> None:
    raise _not_yet()
