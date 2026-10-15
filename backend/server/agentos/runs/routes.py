"""Run dispatch, inspection and cancellation routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from agentos.auth.deps import CurrentUser, Writer
from agentos.errors import ERROR_RESPONSES
from agentos.http_deps import Ctx
from agentos.models import DispatchRequest, Manifest, RunDetail, RunEvent, RunSummary
from agentos.runs import service

router = APIRouter(prefix="/api", tags=["runs"], responses=ERROR_RESPONSES)


@router.post("/tickets/{key}/runs", status_code=201)
def dispatch(key: str, body: DispatchRequest, user: Writer, ctx: Ctx) -> RunSummary:
    snapshot = ctx.snapshot()
    return service.dispatch(
        ctx.db,
        snapshot.runtimes,
        snapshot.space,
        ctx.launcher.name,
        key,
        user,
        body.from_stage,
        body.target,
    )


@router.get("/tickets/{key}/runs")
def list_runs(key: str, _: CurrentUser, ctx: Ctx) -> list[RunSummary]:
    return service.list_runs(ctx.db, key)


@router.get("/runs/{run_id}")
def get_run(run_id: int, _: CurrentUser, ctx: Ctx) -> RunDetail:
    return service.get_run(ctx.db, run_id)


@router.get("/runs/{run_id}/manifest")
def get_manifest(run_id: int, _: CurrentUser, ctx: Ctx) -> Manifest:
    """Exactly what was sent to the worker (prompts + files). Can be large; fetch on demand."""
    return service.get_manifest(ctx.db, run_id)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int, _: Writer, ctx: Ctx) -> RunSummary:
    return service.cancel(ctx.db, run_id)


@router.get("/runs/{run_id}/events")
def run_events(
    run_id: int,
    _: CurrentUser,
    ctx: Ctx,
    after_seq: int | None = None,
    before_seq: int | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> list[RunEvent]:
    return service.run_events(ctx.db, run_id, after_seq, before_seq, limit)
