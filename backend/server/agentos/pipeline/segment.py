"""Segments, completion mapping and pipeline validation (SPEC §6, §7).

Pure. `next_stage` lives in `tickets/board.py` (it needs run history).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Literal

from agentos.errors import ApiError
from agentos.models import Pipeline, Stage, WorkspaceMode


def segment(pipeline: Pipeline, from_stage: str) -> list[Stage]:
    """Enabled stages from `from_stage` through the first `gate_after` (inclusive), else to the
    end. Disabled stages are skipped, and so is their `gate_after`.

    `from_stage` must exist and be enabled → else 422 `invalid_stage`.
    """
    ids = [s.id for s in pipeline.stages]
    if from_stage not in ids:
        raise ApiError(422, "invalid_stage", f"stage {from_stage}: no such stage")
    start = ids.index(from_stage)
    if not pipeline.stages[start].enabled:
        raise ApiError(422, "invalid_stage", f"stage {from_stage}: stage is disabled")
    out: list[Stage] = []
    for stage in pipeline.stages[start:]:
        if not stage.enabled:
            continue
        out.append(stage)
        if stage.gate_after:
            break
    return out


def check_segment(stages: Sequence[Stage], workspace_mode: WorkspaceMode) -> None:
    """Dispatch-time checks (SPEC §8 step 2) → 422 `invalid_stage`."""
    if not stages:
        raise ApiError(422, "invalid_stage", "segment is empty")
    for stage in stages:
        if stage.requires_repo and workspace_mode == "none":
            raise ApiError(
                422, "invalid_stage", f"stage {stage.id}: requires a repo; this ticket has none"
            )
        if stage.kind == "shell" and not stage.command.strip():
            raise ApiError(422, "invalid_stage", f"stage {stage.id}: shell stage has no command")


def completion_status(
    pipeline: Pipeline, stage_ids: Sequence[str]
) -> Literal["gated", "succeeded"]:
    """A worker `completed` → `gated` if the segment's last stage has `gate_after` and an enabled
    stage follows it in the (current) pipeline; otherwise `succeeded`. A last stage that no longer
    exists counts as not gated."""
    if not stage_ids:
        raise ValueError("a run has at least one stage")
    ids = [s.id for s in pipeline.stages]
    last = stage_ids[-1]
    if last not in ids:
        return "succeeded"
    i = ids.index(last)
    if pipeline.stages[i].gate_after and any(s.enabled for s in pipeline.stages[i + 1 :]):
        return "gated"
    return "succeeded"


def validate_pipeline(pipeline: Pipeline, *, runtimes: Collection[str]) -> None:
    """Pipeline/template write checks → 422 `invalid_pipeline`.

    Enforced: unique stage ids; `library_sets` empty (until M1); an enabled shell stage has a
    command; an agent stage's `runtime`, if set, is a configured runtime. Ids are the identity of
    a stage (run history refers to them), so "immutable" needs no extra check: an edit may add,
    remove or reorder stages, and a changed id is a remove plus an add. The `Stage` model already
    enforces the id/name/agent/model formats, prompt size and timeout range.
    """
    seen: set[str] = set()
    for stage in pipeline.stages:
        if stage.id in seen:
            raise ApiError(422, "invalid_pipeline", f"stage {stage.id}: duplicate stage id")
        seen.add(stage.id)
        if stage.kind == "shell" and stage.enabled and not stage.command.strip():
            raise ApiError(
                422, "invalid_pipeline", f"stage {stage.id}: an enabled shell stage needs a command"
            )
        if stage.kind == "agent" and stage.runtime is not None and stage.runtime not in runtimes:
            raise ApiError(
                422, "invalid_pipeline", f"stage {stage.id}: unknown runtime {stage.runtime!r}"
            )
    if pipeline.library_sets:
        raise ApiError(422, "invalid_pipeline", "library_sets: not supported yet (M1)")
