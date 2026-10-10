"""The board model: every derived ticket field the UI renders as-is (SPEC §13).

Pure: plain facts in, plain values out (import-linter keeps db, web and config out).

Two steps, so a list endpoint batch-loads facts once (a few queries, never per ticket) and only
the cheap part depends on who is looking:

    board = derive(facts)                  # phase, phase_since, phase_track, rail, next_stage, now
    view = for_viewer(board, viewer_id)    # attention (needs_you vs waiting) and now.person

Position is computed from run history, never stored (SPEC §16). The latest run plus the set of
stages that ever succeeded in any run of the ticket is enough for every field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, get_args

from agentos.models import (
    Attention,
    NowInfo,
    Person,
    Phase,
    PhaseState,
    PhaseStep,
    Pipeline,
    RailState,
    RailStep,
    RunStatus,
    StageRunStatus,
    TaskProgress,
    TicketState,
)

PHASES: tuple[Phase, ...] = get_args(Phase)
ACTIVE: frozenset[RunStatus] = frozenset({"queued", "dispatched", "running"})
FAILED: frozenset[RunStatus] = frozenset({"failed", "cancelled"})

# Viewer-independent attention. `for_viewer` turns `awaiting` (the latest run is gated or
# succeeded, so a human decides what happens next) into `needs_you` or `waiting`.
Standing = Literal["done", "moving", "stuck", "awaiting", "up_next"]


# ------------------------------------------------------------------ facts: the inputs


@dataclass(frozen=True, slots=True)
class StageRunFact:
    idx: int
    stage_id: str
    status: StageRunStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RunFact:
    """The ticket's latest run (by id)."""

    id: int
    status: RunStatus
    from_stage: str
    stage_ids: tuple[str, ...]
    stage_runs: tuple[StageRunFact, ...]
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TicketFacts:
    """Everything the board needs about one ticket.

    `ever_succeeded`: stage ids with a succeeded stage_run in any run of the ticket (one GROUP BY
    for the whole board). The latest run's succeeded stages are added here anyway.
    `tasks` / `next_task`: TASKS.md progress and its first unticked item (`parse_plan`).
    """

    state: TicketState
    owner: Person | None
    pipeline: Pipeline
    created_at: datetime
    updated_at: datetime
    latest_run: RunFact | None = None
    ever_succeeded: frozenset[str] = field(default_factory=frozenset)
    tasks: TaskProgress | None = None
    next_task: str | None = None


# ------------------------------------------------------------------ outputs


@dataclass(frozen=True, slots=True)
class TicketBoard:
    """The viewer-independent board fields. `now.person` is always None here."""

    standing: Standing
    owner: Person | None
    phase: Phase
    phase_since: datetime
    phase_track: list[PhaseStep]
    rail: list[RailStep]
    next_stage: str | None
    now: NowInfo


@dataclass(frozen=True, slots=True)
class ViewerBoard:
    attention: Attention
    now: NowInfo


# ------------------------------------------------------------------ public API


def derive(facts: TicketFacts) -> TicketBoard:
    run = facts.latest_run
    ever = facts.ever_succeeded | _succeeded_in(run)
    rail = _rail(facts.state, facts.pipeline, run, ever)
    phase = _phase(facts.state, run, rail)
    nxt = next_stage(facts.pipeline, run)
    standing = _standing(facts.state, run)
    return TicketBoard(
        standing=standing,
        owner=facts.owner,
        phase=phase,
        phase_since=_phase_since(facts, phase),
        phase_track=[
            PhaseStep(phase=p, state=_phase_state(p, facts.state, run, rail, phase)) for p in PHASES
        ],
        rail=rail,
        next_stage=nxt,
        now=_now(standing, facts, nxt),
    )


def for_viewer(board: TicketBoard, viewer_id: int) -> ViewerBoard:
    """`awaiting` → `needs_you` if the ticket is unowned or the viewer owns it, else `waiting`
    (then `now.person` is the owner: who the ticket waits on)."""
    if board.standing != "awaiting":
        return ViewerBoard(attention=board.standing, now=board.now)
    if board.owner is None or board.owner.id == viewer_id:
        return ViewerBoard(attention="needs_you", now=board.now)
    return ViewerBoard(
        attention="waiting", now=board.now.model_copy(update={"person": board.owner})
    )


def next_stage(pipeline: Pipeline, latest: RunFact | None) -> str | None:
    """The default stage for Dispatch / Continue / Retry (SPEC §7), in the current pipeline order.

    - no enabled stage → None; no runs → the first enabled stage.
    - an active run → None (dispatch is blocked anyway).
    - anchor = the gated/succeeded run's last stage, or the failed/cancelled run's failing stage
      (see `_failure_anchor`). An anchor that no longer exists → the first enabled stage, except
      after a succeeded run → None (finished work is not restarted by default; "Run from…").
    - failed/cancelled → the anchor if it is still enabled; otherwise like gated:
    - gated/succeeded → the first enabled stage after the anchor; None if nothing follows
      ("complete" = no enabled stage after the last executed one, SPEC §16, not the status).
    """
    enabled = [s.id for s in pipeline.stages if s.enabled]
    if not enabled:
        return None
    if latest is None:
        return enabled[0]
    if latest.status in ACTIVE:
        return None
    anchor = _failure_anchor(latest)[0] if latest.status in FAILED else _last_stage(latest)
    ids = [s.id for s in pipeline.stages]
    if anchor is None or anchor not in ids:
        return None if latest.status == "succeeded" else enabled[0]
    if latest.status in FAILED and anchor in enabled:
        return anchor
    return next((s.id for s in pipeline.stages[ids.index(anchor) + 1 :] if s.enabled), None)


# ------------------------------------------------------------------ run helpers


def _segment(run: RunFact) -> list[StageRunFact]:
    """The run's stage_runs in order; a run without any (never expected) counts as all pending."""
    if run.stage_runs:
        return sorted(run.stage_runs, key=lambda sr: sr.idx)
    return [StageRunFact(idx=i, stage_id=s, status="pending") for i, s in enumerate(run.stage_ids)]


def _last_stage(run: RunFact) -> str | None:
    if run.stage_ids:
        return run.stage_ids[-1]
    srs = _segment(run)
    return srs[-1].stage_id if srs else None


def _succeeded_in(run: RunFact | None) -> frozenset[str]:
    if run is None:
        return frozenset()
    return frozenset(sr.stage_id for sr in run.stage_runs if sr.status == "succeeded")


def _failure_anchor(run: RunFact) -> tuple[str | None, StageRunFact | None]:
    """Where a failed/cancelled run stopped: `(stage_id, culprit)`.

    The culprit is the first stage_run that failed, or was cancelled after it started. Terminal
    side effects (SPEC §8) mark every unfinished stage_run `cancelled`, so a cancelled stage that
    never started is not to blame. With no culprit (the worker never connected, the clone failed)
    the anchor is the first non-succeeded stage of the segment, else its last stage.
    """
    srs = _segment(run)
    for sr in srs:
        if sr.status == "failed" or (sr.status == "cancelled" and sr.started_at is not None):
            return sr.stage_id, sr
    for sr in srs:
        if sr.status != "succeeded":
            return sr.stage_id, None
    return (srs[-1].stage_id if srs else None), None


def _focus(run: RunFact | None) -> tuple[str | None, RailState]:
    """The latest segment's stage the ticket is at: `current` (running, about to run, or a gated
    stage awaiting review) or `failed`. None for a succeeded run or no run."""
    if run is None:
        return None, "current"
    if run.status in ACTIVE:
        srs = _segment(run)
        sr = next((sr for sr in srs if sr.status == "running"), None)
        sr = sr or next((sr for sr in srs if sr.status == "pending"), None)
        return (sr.stage_id if sr else None), "current"
    if run.status == "gated":
        return _last_stage(run), "current"
    if run.status in FAILED:
        return _failure_anchor(run)[0], "failed"
    return None, "current"


# ------------------------------------------------------------------ derivations


def _standing(state: TicketState, run: RunFact | None) -> Standing:
    if state in ("done", "closed"):
        return "done"
    if run is None:
        return "up_next"
    if run.status in ACTIVE:
        return "moving"
    if run.status in FAILED:
        return "stuck"
    return "awaiting"


def _rail(
    state: TicketState, pipeline: Pipeline, run: RunFact | None, ever: frozenset[str]
) -> list[RailStep]:
    """One step per enabled stage of the current pipeline, in order.

    - A done ticket: `done` if the stage ever succeeded, else `pending` (nothing is in progress).
    - Stages in the latest segment: the focus stage is `current`/`failed`; the others are `done`
      if their stage_run succeeded, else `pending` (incl. stages cancelled before they started).
    - Stages before the segment (by current position): `done` if they ever succeeded, else
      `pending`. Stages after it: `stale` if they ever succeeded, else `pending`.
    - Run stages that are no longer in the pipeline (or disabled) don't appear. If none of the
      segment's stages exist any more, every other stage counts as "before".
    """
    enabled = [s for s in pipeline.stages if s.enabled]
    if state == "done":
        return [
            RailStep(
                stage_id=s.id,
                name=s.name,
                phase=s.phase,
                state="done" if s.id in ever else "pending",
            )
            for s in enabled
        ]
    status: dict[str, StageRunStatus] = {}
    if run is not None:
        for sr in _segment(run):
            status[sr.stage_id] = sr.status  # a repeated stage (future loops): the last one wins
    focus, focus_state = _focus(run)
    pos = {s.id: i for i, s in enumerate(pipeline.stages)}
    first = min((pos[sid] for sid in status if sid in pos), default=len(pipeline.stages))
    steps: list[RailStep] = []
    for s in enabled:
        st: RailState
        if s.id == focus:
            st = focus_state
        elif s.id in status:
            st = "done" if status[s.id] == "succeeded" else "pending"
        elif pos[s.id] < first:
            st = "done" if s.id in ever else "pending"
        else:
            st = "stale" if s.id in ever else "pending"
        steps.append(RailStep(stage_id=s.id, name=s.name, phase=s.phase, state=st))
    return steps


def _phase(state: TicketState, run: RunFact | None, rail: list[RailStep]) -> Phase:
    if state == "done":
        return "done"
    if run is None:
        return "inbox"
    focus = next((r for r in rail if r.state in ("current", "failed")), None)
    if focus is not None:
        return focus.phase
    done = [r for r in rail if r.state == "done"]
    return done[-1].phase if done else "inbox"


def _phase_since(facts: TicketFacts, phase: Phase) -> datetime:
    """When the ticket entered its current phase (the board's "in stage" column).

    - done ticket → `updated_at`; no runs → `created_at`.
    - active run: while a stage is running, the `started_at` of the first stage in the run's
      contiguous streak of stages in that same phase (e.g. implement → fix, both build); else
      (queued, dispatched, between stages) the run's `started_at`, falling back to `created_at`.
    - finished run → its `finished_at` (gated/succeeded: how long it has waited for a human;
      failed/cancelled: how long it has been stuck), falling back to `created_at`.
    """
    run = facts.latest_run
    if facts.state == "done":
        return facts.updated_at
    if run is None:
        return facts.created_at
    if run.status in ACTIVE:
        return _streak_start(facts.pipeline, run, phase) or run.started_at or run.created_at
    return run.finished_at or run.created_at


def _streak_start(pipeline: Pipeline, run: RunFact, phase: Phase) -> datetime | None:
    srs = _segment(run)
    k = next((i for i, sr in enumerate(srs) if sr.status == "running"), None)
    phases = {s.id: s.phase for s in pipeline.stages}
    if k is None or phases.get(srs[k].stage_id) != phase:
        return None
    start = srs[k].started_at
    for sr in reversed(srs[:k]):
        if phases.get(sr.stage_id) != phase or sr.started_at is None:
            break
        start = sr.started_at
    return start


def _phase_state(
    p: Phase, state: TicketState, run: RunFact | None, rail: list[RailStep], phase: Phase
) -> PhaseState:
    if p == "inbox":
        return "current" if run is None and state == "open" else "done"
    if p == "done":
        return "done" if state == "done" else "pending"
    states = [r.state for r in rail if r.phase == p]
    if not states:
        return "skipped"
    if state == "done" or all(s == "done" for s in states):
        return "done"
    if "failed" in states:
        return "failed"
    if "current" in states or phase == p:
        return "current"
    if "stale" in states:
        return "stale"
    return "pending"


def _now(standing: Standing, facts: TicketFacts, nxt: str | None) -> NowInfo:
    run = facts.latest_run
    names = {s.id: s.name for s in facts.pipeline.stages}

    def name(stage_id: str) -> str:
        return names.get(stage_id, stage_id)

    def info(
        text: str, *, actor: str | None = None, progress: TaskProgress | None = None
    ) -> NowInfo:
        return NowInfo(
            actor=actor, person=None, text=text, run_id=run.id if run else None, progress=progress
        )

    if standing == "done":
        return info("done", progress=facts.tasks)
    if standing == "up_next" or run is None:
        return NowInfo(
            actor=None, person=None, text="add context, then dispatch", run_id=None, progress=None
        )
    if standing == "moving":
        if run.status != "running":
            return info("queued for a runner")
        sr = next((sr for sr in _segment(run) if sr.status == "running"), None)
        return info(
            facts.next_task or "working",
            actor=name(sr.stage_id) if sr else None,
            progress=facts.tasks,
        )
    if standing == "stuck":
        culprit = _failure_anchor(run)[1]
        what = f"{name(culprit.stage_id)} {run.status}" if culprit else f"run {run.status}"
        return info(f"{what}: retry or adjust")
    # gated or succeeded: "continue" while an enabled stage follows the last one run (SPEC §16).
    last = _last_stage(run)
    if nxt is not None and last is not None:
        return info(f"review {name(last).lower()} and continue", progress=facts.tasks)
    return info("review the result and mark done", progress=facts.tasks)
