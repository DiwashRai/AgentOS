"""Run dispatch, inspection and cancellation routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from agentos import stubs
from agentos.auth.deps import CurrentUser
from agentos.errors import ERROR_RESPONSES
from agentos.models import DispatchRequest, Manifest, RunDetail, RunEvent, RunSummary

router = APIRouter(prefix="/api", tags=["runs"], responses=ERROR_RESPONSES)


@router.post("/tickets/{key}/runs", status_code=201)
def dispatch(key: str, body: DispatchRequest, _: CurrentUser) -> RunSummary:
    return stubs.STORE.dispatch(key, body.from_stage)


@router.get("/tickets/{key}/runs")
def list_runs(key: str, _: CurrentUser) -> list[RunSummary]:
    return stubs.STORE.list_runs(key)


@router.get("/runs/{run_id}")
def get_run(run_id: int, _: CurrentUser) -> RunDetail:
    return stubs.STORE.get_run(run_id)


@router.get("/runs/{run_id}/manifest")
def get_manifest(run_id: int, _: CurrentUser) -> Manifest:
    """Exactly what was sent to the worker (prompts + files). Can be large; fetch on demand."""
    return stubs.STORE.get_manifest(run_id)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int, _: CurrentUser) -> RunSummary:
    return stubs.STORE.cancel(run_id)


@router.get("/runs/{run_id}/events")
def run_events(
    run_id: int,
    _: CurrentUser,
    after_seq: int | None = None,
    before_seq: int | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> list[RunEvent]:
    return stubs.STORE.run_events(run_id, after_seq, before_seq, limit)
