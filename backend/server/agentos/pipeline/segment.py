"""Segments, run plans, completion mapping and pipeline validation (SPEC §6, §7).

Pure. `next_stage` lives in `tickets/board.py` (it needs run history).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from agentos.errors import ApiError
from agentos.models import Loop, Pipeline, Stage, WorkspaceMode


def is_check(stage: Stage) -> bool:
    """Inside a loop block: a shell stage, or an agent stage with `check`. Other stages make."""
    return stage.kind == "shell" or stage.check


@dataclass(frozen=True, slots=True)
class RunPlan:
    """What a run executes. `stages`, in order: round 1 starts at `start` (the run's from_stage);
    stages before it are the start of the loop block it is in, run from round 2 on. `loops`: the
    blocks over `stages` that loop (first and last enabled stage). `warnings`: blocks that run
    once instead, because their enabled stages have no check or nothing that makes."""

    stages: tuple[Stage, ...]
    start: str
    loops: tuple[Loop, ...] = ()
    warnings: tuple[str, ...] = ()

    def time_budget_s(self) -> int:
        """Every call the run may make at its timeout: a loop stage once per round, an agent
        check's verdict repairs on top (the reaper's budget, so a long loop isn't killed)."""
        in_loop = loops_by_stage(self.stages, self.loops)
        total = 0
        for stage in self.stages:
            calls = 1
            if (loop := in_loop.get(stage.id)) is not None:
                repairs = stage.verdict_repairs if stage.kind == "agent" and stage.check else 0
                calls = loop.max_rounds * (1 + repairs)
            total += stage.timeout_s * calls
        return total


def loops_by_stage(stages: Sequence[Stage], loops: Sequence[Loop]) -> dict[str, Loop]:
    """The loop block each stage of `stages` is in (blocks over `stages`, by id)."""
    ids = [s.id for s in stages]
    return {
        stage_id: loop
        for loop in loops
        for stage_id in ids[ids.index(loop.start) : ids.index(loop.end) + 1]
    }


def plan_run(pipeline: Pipeline, from_stage: str) -> RunPlan:
    """The `segment` from `from_stage`, plus (when it starts inside a loop block) the enabled
    block stages before it, so a failed round can go back to the block's start. A block whose
    enabled stages have no check, or no maker, runs once with a warning. 422 `invalid_stage` as
    `segment`. Expects a pipeline that passed `validate_pipeline`."""
    stages = segment(pipeline, from_stage)
    pos = {s.id: i for i, s in enumerate(pipeline.stages)}
    here = pos[from_stage]
    for loop in pipeline.loops:
        start, end = pos[loop.start], pos[loop.end]
        if start < here <= end:
            stages = [s for s in pipeline.stages[start:here] if s.enabled] + stages
    ids = {s.id for s in stages}
    loops: list[Loop] = []
    warnings: list[str] = []
    for loop in pipeline.loops:
        block = [s for s in pipeline.stages[pos[loop.start] : pos[loop.end] + 1] if s.id in ids]
        if not block:
            continue
        name = f"The loop {loop.start} → {loop.end}"
        if not any(is_check(s) for s in block):
            warnings.append(f"{name} runs once: none of its enabled stages is a check")
        elif all(is_check(s) for s in block):
            warnings.append(f"{name} runs once: its enabled stages are all checks, none fixes")
        else:
            loops.append(Loop(start=block[0].id, end=block[-1].id, max_rounds=loop.max_rounds))
    return RunPlan(tuple(stages), from_stage, tuple(loops), tuple(warnings))


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

    Enforced: unique stage ids; an enabled shell stage has a command; an agent stage's `runtime`,
    if set, is a configured runtime; the loop blocks (see `_check_loops`). Ids are the identity of
    a stage (run history refers to them), so "immutable" needs no extra check: an edit may add,
    remove or reorder stages, and a changed id is a remove plus an add. The `Stage` model already
    enforces the id/name/agent/model formats, prompt size and timeout range.
    """
    seen: set[str] = set()
    for stage in pipeline.stages:
        if stage.id in seen:
            raise _invalid_pipeline(f"stage {stage.id}: duplicate stage id")
        seen.add(stage.id)
        if stage.kind == "shell" and stage.enabled and not stage.command.strip():
            raise _invalid_pipeline(f"stage {stage.id}: an enabled shell stage needs a command")
        if stage.kind == "agent" and stage.runtime is not None and stage.runtime not in runtimes:
            raise _invalid_pipeline(f"stage {stage.id}: unknown runtime {stage.runtime!r}")
    _check_loops(pipeline)


def _invalid_pipeline(message: str) -> ApiError:
    return ApiError(422, "invalid_pipeline", message)


def _check_loops(pipeline: Pipeline) -> None:
    """Each block's ids exist with `start` at or before `end`; it holds a check and a maker
    (counting disabled stages: disabling one on a ticket mustn't block saving, and dispatch
    copes); only its `end` may have `gate_after`; blocks don't overlap."""
    pos = {s.id: i for i, s in enumerate(pipeline.stages)}
    spans: list[tuple[int, int, int]] = []
    for n, loop in enumerate(pipeline.loops):
        where = f"loops.{n}"
        for stage_id in (loop.start, loop.end):
            if stage_id not in pos:
                raise _invalid_pipeline(f"{where}: no stage {stage_id!r}")
        start, end = pos[loop.start], pos[loop.end]
        if start > end:
            raise _invalid_pipeline(f"{where}: {loop.start} comes after {loop.end}")
        block = pipeline.stages[start : end + 1]
        if not any(is_check(s) for s in block):
            raise _invalid_pipeline(
                f"{where}: the block needs a check (a shell stage, or an agent stage with check)"
            )
        if all(is_check(s) for s in block):
            raise _invalid_pipeline(
                f"{where}: the block needs an agent stage that isn't a check, to fix what the "
                "checks find"
            )
        gated = next((s.id for s in block[:-1] if s.gate_after), None)
        if gated is not None:
            raise _invalid_pipeline(
                f"stage {gated}: only the last stage of a loop block ({loop.end}) may have a gate"
            )
        spans.append((start, end, n))
    spans.sort()
    for (_, end, first), (start, _, second) in pairwise(spans):
        if start <= end:
            raise _invalid_pipeline(f"loops.{second}: overlaps loops.{first}")
