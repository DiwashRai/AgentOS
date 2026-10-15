"""Segments, completion mapping and pipeline validation (SPEC §6, §7)."""

from __future__ import annotations

import pytest

from agentos.errors import ApiError
from agentos.models import Pipeline, Stage, StageKind, WorkspaceMode
from agentos.pipeline.scaffold import default_pipeline
from agentos.pipeline.segment import (
    check_segment,
    completion_status,
    segment,
    validate_pipeline,
)


def st(
    sid: str,
    *,
    enabled: bool = True,
    gate: bool = False,
    kind: StageKind = "agent",
    command: str = "",
    repo: bool = False,
    runtime: str | None = None,
) -> Stage:
    return Stage(
        id=sid,
        name=sid,
        kind=kind,
        phase="build",
        enabled=enabled,
        gate_after=gate,
        command=command,
        requires_repo=repo,
        runtime=runtime,
    )


def pipe(*stages: Stage) -> Pipeline:
    return Pipeline(stages=list(stages))


# Stage c is disabled but has gate_after; e is the last stage and gated.
P = pipe(
    st("a"), st("b", gate=True), st("c", enabled=False, gate=True), st("d"), st("e", gate=True)
)


@pytest.mark.parametrize(
    ("pipeline", "start", "expected"),
    [
        (P, "a", ["a", "b"]),
        (P, "b", ["b"]),
        (P, "d", ["d", "e"]),  # disabled c's gate is ignored; stops at e's gate
        (P, "e", ["e"]),  # gate on the last stage
        (pipe(st("a"), st("b", enabled=False), st("c")), "a", ["a", "c"]),  # to the end
        (pipe(st("a")), "a", ["a"]),
        (default_pipeline(), "plan", ["plan"]),
        (default_pipeline(), "implement", ["implement", "review", "open-pr"]),
        (default_pipeline(lint=True), "implement", ["implement", "lint", "review", "open-pr"]),
    ],
)
def test_segment(pipeline: Pipeline, start: str, expected: list[str]) -> None:
    assert [s.id for s in segment(pipeline, start)] == expected


@pytest.mark.parametrize(("start", "why"), [("c", "disabled"), ("zzz", "no such stage")])
def test_segment_rejects_disabled_or_unknown_start(start: str, why: str) -> None:
    with pytest.raises(ApiError) as e:
        segment(P, start)
    assert (e.value.status, e.value.code) == (422, "invalid_stage")
    assert e.value.message.startswith(f"stage {start}: ")
    assert why in e.value.message


def test_check_segment_ok() -> None:
    check_segment([st("a"), st("b", repo=True)], "repo")
    check_segment([st("a"), st("lint", kind="shell", command="make lint")], "none")


@pytest.mark.parametrize(
    ("stages", "mode", "needle"),
    [
        ([], "repo", "empty"),
        ([st("a"), st("pr", repo=True)], "none", "stage pr: requires a repo"),
        (
            [st("lint", kind="shell", command="  ")],
            "repo",
            "stage lint: shell stage has no command",
        ),
    ],
)
def test_check_segment_rejects(stages: list[Stage], mode: WorkspaceMode, needle: str) -> None:
    with pytest.raises(ApiError) as e:
        check_segment(stages, mode)
    assert (e.value.status, e.value.code) == (422, "invalid_stage")
    assert needle in e.value.message


@pytest.mark.parametrize(
    ("pipeline", "stage_ids", "status"),
    [
        (P, ["a", "b"], "gated"),
        (P, ["d", "e"], "succeeded"),  # gate on the last stage
        (P, ["a"], "succeeded"),  # segment ended without a gate (to the end at dispatch time)
        # gate followed only by disabled stages → succeeded
        (pipe(st("a", gate=True), st("b", enabled=False)), ["a"], "succeeded"),
        # last stage removed from the pipeline since → succeeded
        (pipe(st("x")), ["a", "gone"], "succeeded"),
        (default_pipeline(), ["plan"], "gated"),
        (default_pipeline(), ["implement", "review", "open-pr"], "succeeded"),
    ],
)
def test_completion_status(pipeline: Pipeline, stage_ids: list[str], status: str) -> None:
    assert completion_status(pipeline, stage_ids) == status


def test_completion_status_needs_a_stage() -> None:
    with pytest.raises(ValueError, match="at least one stage"):
        completion_status(P, [])


RUNTIMES = {"claude", "copilot"}


def test_validate_pipeline_ok() -> None:
    validate_pipeline(default_pipeline(), runtimes=RUNTIMES)
    validate_pipeline(default_pipeline(lint=True), runtimes=RUNTIMES)
    # a disabled shell stage may have an empty command; a shell stage's runtime is ignored
    validate_pipeline(
        pipe(st("a", runtime="copilot"), st("l", kind="shell", enabled=False)), runtimes=RUNTIMES
    )


@pytest.mark.parametrize(
    ("pipeline", "needle"),
    [
        (pipe(st("a"), st("b"), st("a")), "stage a: duplicate stage id"),
        (pipe(st("l", kind="shell")), "stage l: an enabled shell stage needs a command"),
        (pipe(st("a", runtime="gemini")), "stage a: unknown runtime 'gemini'"),
    ],
)
def test_validate_pipeline_rejects(pipeline: Pipeline, needle: str) -> None:
    with pytest.raises(ApiError) as e:
        validate_pipeline(pipeline, runtimes=RUNTIMES)
    assert (e.value.status, e.value.code) == (422, "invalid_pipeline")
    assert needle in e.value.message
