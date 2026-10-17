"""The board model: every derived ticket field the UI renders as-is (SPEC §13).

Pure: plain facts in, plain values out (import-linter keeps db, web and config out).

Two steps, so a list endpoint batch-loads facts once (a few queries, never per ticket) and only
the cheap part depends on who is looking:

    board = derive(facts)                  # phase, phase_track, attention_since, next_stage, ...
    view = for_viewer(board, viewer_id)    # attention, rail (gate you/waiting) and now.person

Position is computed from run history, never stored (SPEC §16). The latest run plus the set of
stages that ever succeeded in any run of the ticket is enough for every field.

A run is in one of five states (`RunState`): `Queued` (maybe with the reason it waits),
`Dispatched`, `Running` (since when), `Finished` (gated or succeeded: a human decides next) or
`Failed` (failed or cancelled, maybe without ever starting). It always has at least one stage and
one stage_run. `tickets.service.run_fact` decodes a run row into one. `derive` branches once, on
whether the ticket ran and on its state (open, done or closed); the helpers match on the run's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, get_args

from agentos.models import (
    Attention,
    DispatchBlock,
    DispatchTarget,
    GateInfo,
    GateState,
    NowInfo,
    Person,
    Phase,
    PhaseState,
    PhaseStep,
    Pipeline,
    RailState,
    RailStep,
    RunStatus,
    Stage,
    StageRunStatus,
    TaskProgress,
    TicketState,
    WorkspaceMode,
)

PHASES: tuple[Phase, ...] = get_args(Phase)
ACTIVE: frozenset[RunStatus] = frozenset({"queued", "dispatched", "running"})

# Viewer-independent attention. `for_viewer` turns `awaiting` (the latest run is gated or
# succeeded, so a human decides what happens next) into `needs_you` or `waiting`.
Standing = Literal["done", "running", "queued", "blocked", "awaiting", "inbox"]


# ------------------------------------------------------------------ facts: the inputs


@dataclass(frozen=True, slots=True)
class Queued:
    """Waiting for a runner or a client. `reason`: why it waits, when something holds it."""

    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Dispatched:
    """Handed to a runner or a client, not started yet."""


@dataclass(frozen=True, slots=True)
class Running:
    started_at: datetime


@dataclass(frozen=True, slots=True)
class Finished:
    """Gated or succeeded: the ticket waits for a human to decide what happens next."""

    status: Literal["gated", "succeeded"]
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class Failed:
    """Failed or cancelled, maybe before it ever started."""

    status: Literal["failed", "cancelled"]
    finished_at: datetime


RunState = Queued | Dispatched | Running | Finished | Failed


@dataclass(frozen=True, slots=True)
class StageRunFact:
    idx: int
    stage_id: str
    status: StageRunStatus
    round: int = 1  # its round in a loop block
    started: bool = False


@dataclass(frozen=True, slots=True)
class RunFact:
    """The ticket's latest run (by id): its planned stages, and its stage_runs in idx order.
    `target`: who serves it; a client run waits for the client of its creator (`creator_name`)."""

    id: int
    state: RunState
    from_stage: str
    stage_ids: tuple[str, *tuple[str, ...]]
    stage_runs: tuple[StageRunFact, *tuple[StageRunFact, ...]]
    created_at: datetime
    target: DispatchTarget = "server"
    creator_name: str | None = None


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
    # The resolved workspace (`SpaceConfig.resolve`): a repo ticket needs a base branch to run.
    workspace_mode: WorkspaceMode = "none"
    base_branch: str | None = None


# ------------------------------------------------------------------ outputs


@dataclass(frozen=True, slots=True)
class TicketBoard:
    """The viewer-independent board fields. `now.person` is always None here.

    `parked`: the stage whose gate holds the ticket (an open ticket whose latest run is gated or
    succeeded and ended on an enabled `gate_after` stage), else None. Its gate reads `pending` in
    `rail` until `for_viewer` resolves it to `you`/`waiting`: render `ViewerBoard.rail`.
    """

    standing: Standing
    owner: Person | None
    phase: Phase
    phase_track: list[PhaseStep]
    rail: list[RailStep]
    parked: str | None
    attention_since: datetime
    next_stage: str | None
    now: NowInfo
    dispatch_block: DispatchBlock | None


@dataclass(frozen=True, slots=True)
class ViewerBoard:
    attention: Attention
    rail: list[RailStep]
    now: NowInfo


@dataclass(frozen=True, slots=True)
class _Where:
    """The board fields that depend on whether the ticket ran and on its state."""

    standing: Standing
    rail: list[RailStep]
    phase: Phase
    since: datetime
    now: NowInfo
    parked: str | None = None


# ------------------------------------------------------------------ public API


def derive(facts: TicketFacts) -> TicketBoard:
    run = facts.latest_run
    nxt = next_stage(facts.pipeline, run)
    where = _never_ran(facts) if run is None else _ran(facts, run, nxt)
    return TicketBoard(
        standing=where.standing,
        owner=facts.owner,
        phase=where.phase,
        phase_track=_phase_track(where.standing, facts.state, where.rail, where.phase),
        rail=where.rail,
        parked=where.parked,
        attention_since=where.since,
        next_stage=nxt,
        now=where.now,
        dispatch_block=dispatch_block(facts.state, facts.workspace_mode, facts.base_branch),
    )


BASE_BRANCH_MISSING = DispatchBlock(code="base_branch_missing", text="Set a base branch first")


def dispatch_block(
    state: TicketState, mode: WorkspaceMode, base_branch: str | None
) -> DispatchBlock | None:
    """Why the dispatch endpoint would refuse this ticket now, for the UI to disable Dispatch.
    Only for open tickets (a done/closed one must be reopened first anyway). The dispatch guard
    (`tickets.service.run_ticket_ref`) refuses on the same rule with the same code."""
    if state == "open" and mode == "repo" and base_branch is None:
        return BASE_BRANCH_MISSING
    return None


def for_viewer(board: TicketBoard, viewer_id: int) -> ViewerBoard:
    """`awaiting` → `needs_you` if the ticket is unowned or the viewer owns it, else `waiting`
    (then `now.person` is the owner: who the ticket waits on). The parked gate follows the same
    rule: `you`, or `waiting` with `person` = the owner."""
    if board.standing != "awaiting":
        return ViewerBoard(attention=board.standing, rail=board.rail, now=board.now)
    mine = board.owner is None or board.owner.id == viewer_id
    person = None if mine else board.owner
    gate_state: GateState = "you" if mine else "waiting"
    rail = [
        _resolve_gate(step, gate_state, person) if step.stage_id == board.parked else step
        for step in board.rail
    ]
    now = board.now
    return ViewerBoard(
        attention="needs_you" if mine else "waiting",
        rail=rail,
        now=NowInfo(
            actor=now.actor, person=person, text=now.text, run_id=now.run_id, progress=now.progress
        ),
    )


def next_stage(pipeline: Pipeline, latest: RunFact | None) -> str | None:
    """The default stage for Dispatch / Continue / Retry (SPEC §7), in the current pipeline order.

    - no enabled stage → None; no runs → the first enabled stage.
    - an active run → None (dispatch is blocked anyway).
    - anchor = the finished run's last stage, or the failed run's failing stage (see
      `_failure_anchor`). An anchor that no longer exists → the first enabled stage, except
      after a succeeded run → None (finished work is not restarted by default; "Run from…").
    - failed/cancelled → the anchor if it is still enabled; otherwise like gated:
    - gated/succeeded → the first enabled stage after the anchor; None if nothing follows
      ("complete" = no enabled stage after the last executed one, SPEC §16, not the status).
    """
    enabled = [s.id for s in pipeline.stages if s.enabled]
    first = enabled[0] if enabled else None
    if latest is None:
        return first
    match latest.state:
        case Queued() | Dispatched() | Running():
            return None
        case Failed():
            anchor = _failure_anchor(latest)
            return anchor if anchor in enabled else _after(pipeline, anchor, first)
        case Finished(status="succeeded"):
            return _after(pipeline, latest.stage_ids[-1], None)
        case Finished():
            return _after(pipeline, latest.stage_ids[-1], first)


def _after(pipeline: Pipeline, anchor: str, gone: str | None) -> str | None:
    """The first enabled stage after `anchor` in the current order, None if nothing follows;
    `gone` if the anchor is no longer in the pipeline."""
    ids = [s.id for s in pipeline.stages]
    if anchor not in ids:
        return gone
    return next((s.id for s in pipeline.stages[ids.index(anchor) + 1 :] if s.enabled), None)


# ------------------------------------------------------------------ the cases


def _never_ran(facts: TicketFacts) -> _Where:
    """In the inbox while open, else done. The rail marks the stages that ever succeeded."""
    rail = _settled_rail(facts.pipeline, facts.ever_succeeded, {})
    match facts.state:
        case "open":
            inbox = _line("add context, then dispatch", None)
            return _Where("inbox", rail, "inbox", facts.created_at, inbox)
        case "closed":
            return _Where("done", rail, "inbox", facts.updated_at, _line("done", None, facts.tasks))
        case "done":
            return _Where("done", rail, "done", facts.updated_at, _line("done", None, facts.tasks))


def _ran(facts: TicketFacts, run: RunFact, nxt: str | None) -> _Where:
    """An open ticket stands where its run is. A closed one stands `done` on its run's rail; a
    done one's rail settles on the stages that ever succeeded."""
    pipeline = facts.pipeline
    ever = facts.ever_succeeded | {sr.stage_id for sr in run.stage_runs if sr.status == "succeeded"}
    rounds = _rounds(pipeline, run)
    done = _line("done", run.id, facts.tasks)
    match facts.state:
        case "open":
            parked = _parked(pipeline, run)
            rail = _rail(pipeline, run, ever, parked, rounds)
            now = _now(facts, run, nxt)
            return _Where(_standing(run), rail, _phase(rail), _since(run), now, parked)
        case "closed":
            rail = _rail(pipeline, run, ever, None, rounds)
            return _Where("done", rail, _phase(rail), facts.updated_at, done)
        case "done":
            rail = _settled_rail(pipeline, ever, rounds)
            return _Where("done", rail, "done", facts.updated_at, done)


# ------------------------------------------------------------------ run helpers


def _first(run: RunFact, status: StageRunStatus) -> StageRunFact | None:
    return next((sr for sr in run.stage_runs if sr.status == status), None)


def _culprit(run: RunFact) -> StageRunFact | None:
    """The first stage_run that failed, or was cancelled after it started. Terminal side effects
    (SPEC §8) mark every unfinished stage_run `cancelled`, so a cancelled stage that never started
    is not to blame."""
    blamed = (
        sr
        for sr in run.stage_runs
        if sr.status == "failed" or (sr.status == "cancelled" and sr.started)
    )
    return next(blamed, None)


def _failure_anchor(run: RunFact) -> str:
    """Where a failed run stopped: its culprit's stage. With no culprit (the worker never
    connected, the clone failed), the first non-succeeded stage of the segment, else its last."""
    srs = run.stage_runs
    sr = _culprit(run) or next((sr for sr in srs if sr.status != "succeeded"), srs[-1])
    return sr.stage_id


def _focus(run: RunFact) -> tuple[str | None, RailState]:
    """The latest segment's stage the ticket is at: `current` (running, about to run, or a gated
    stage awaiting review) or `failed`. None for a succeeded run."""
    match run.state:
        case Queued() | Dispatched() | Running():
            sr = _first(run, "running") or _first(run, "pending")
            return (sr.stage_id if sr is not None else None), "current"
        case Finished(status="gated"):
            return run.stage_ids[-1], "current"
        case Finished():
            return None, "current"
        case Failed():
            return _failure_anchor(run), "failed"


# ------------------------------------------------------------------ derivations


def _standing(run: RunFact) -> Standing:
    """An open ticket's standing, from its latest run."""
    match run.state:
        case Queued() | Dispatched():
            return "queued"
        case Running():
            return "running"
        case Finished():
            return "awaiting"
        case Failed():
            return "blocked"


def _since(run: RunFact) -> datetime:
    """When an open ticket entered its current attention (the board's age column): queued or
    dispatched → the run's `created_at`; running → its start; ended → its end (how long it has
    waited for a human, or been blocked). A done ticket's is its `updated_at`, one that never
    ran its `created_at`."""
    match run.state:
        case Queued() | Dispatched():
            return run.created_at
        case Running(started_at=at):
            return at
        case Finished(finished_at=at) | Failed(finished_at=at):
            return at


def _parked(pipeline: Pipeline, run: RunFact) -> str | None:
    """The stage whose gate holds an open ticket: a finished run parks at its last stage if that
    stage is enabled and has `gate_after`. A gate on the final enabled stage parks a succeeded
    run until "complete"."""
    match run.state:
        case Finished():
            stage = next((s for s in pipeline.stages if s.id == run.stage_ids[-1]), None)
            return stage.id if stage is not None and stage.enabled and stage.gate_after else None
        case Queued() | Dispatched() | Running() | Failed():
            return None


def _step(
    stage: Stage, state: RailState, parked: str | None, rounds: tuple[int, int] | None
) -> RailStep:
    """A disabled stage is `skipped` with no gate and no round. A `gate_after` gate is `passed`
    once its stage is done and the ticket is not parked at it; otherwise `pending` (a parked gate
    is resolved per viewer by `for_viewer`). `rounds`: (round, max_rounds) or None."""
    if not stage.enabled:
        return RailStep(
            stage_id=stage.id,
            name=stage.name,
            phase=stage.phase,
            state="skipped",
            gate=None,
            round=None,
            max_rounds=None,
        )
    gate: GateInfo | None = None
    if stage.gate_after:
        gate_state: GateState = "passed" if state == "done" and stage.id != parked else "pending"
        gate = GateInfo(
            label=f"{stage.name} sign-off", short="Sign-off", state=gate_state, person=None
        )
    return RailStep(
        stage_id=stage.id,
        name=stage.name,
        phase=stage.phase,
        state=state,
        gate=gate,
        round=rounds[0] if rounds else None,
        max_rounds=rounds[1] if rounds else None,
    )


def _resolve_gate(step: RailStep, state: GateState, person: Person | None) -> RailStep:
    """The parked step, its gate resolved for one viewer."""
    if step.gate is None:
        return step
    return RailStep(
        stage_id=step.stage_id,
        name=step.name,
        phase=step.phase,
        state=step.state,
        gate=GateInfo(label=step.gate.label, short=step.gate.short, state=state, person=person),
        round=step.round,
        max_rounds=step.max_rounds,
    )


def _rounds(pipeline: Pipeline, run: RunFact) -> dict[str, tuple[int, int]]:
    """(round, max_rounds) for each stage in a loop block of the pipeline that has a stage_run in
    the latest run: the round of its latest one (pending included: about to run)."""
    if not pipeline.loops:
        return {}
    pos = {s.id: i for i, s in enumerate(pipeline.stages)}
    cap = {
        s.id: loop.max_rounds
        for loop in pipeline.loops
        for s in pipeline.stages[pos[loop.start] : pos[loop.end] + 1]
    }
    latest = {sr.stage_id: sr.round for sr in run.stage_runs}  # by idx: the last one wins
    return {sid: (latest[sid], cap[sid]) for sid in cap if sid in latest}


def _settled_rail(
    pipeline: Pipeline, ever: frozenset[str], rounds: dict[str, tuple[int, int]]
) -> list[RailStep]:
    """Nothing in progress (a done ticket, or one that never ran): `done` if the stage ever
    succeeded, else `pending`."""
    return [
        _step(s, "done" if s.id in ever else "pending", None, rounds.get(s.id))
        for s in pipeline.stages
    ]


def _rail(
    pipeline: Pipeline,
    run: RunFact,
    ever: frozenset[str],
    parked: str | None,
    rounds: dict[str, tuple[int, int]],
) -> list[RailStep]:
    """One step per stage of the current pipeline, in order; disabled stages are `skipped`.

    - Stages in the latest segment: the focus stage is `current`/`failed`; the others are `done`
      if their stage_run succeeded, else `pending` (incl. stages cancelled before they started).
    - Stages before the segment (by current position): `done` if they ever succeeded, else
      `pending`. Stages after it: `stale` if they ever succeeded, else `pending`.
    - Run stages that are no longer in the pipeline don't appear. If none of the segment's stages
      exist any more, every other stage counts as "before".
    - A stage a loop block ran several times: its latest stage_run counts.
    """
    segment: dict[str, RailState] = {  # by idx: the last stage_run of a repeated stage wins
        sr.stage_id: "done" if sr.status == "succeeded" else "pending" for sr in run.stage_runs
    }
    starts = (i for i, s in enumerate(pipeline.stages) if s.id in segment)
    first = next(starts, len(pipeline.stages))  # where the segment starts in the current order
    focus, focus_state = _focus(run)
    if focus is not None:
        segment[focus] = focus_state
    steps: list[RailStep] = []
    for i, s in enumerate(pipeline.stages):
        st: RailState = "pending"
        if s.id in segment:
            st = segment[s.id]
        elif s.id in ever:
            st = "done" if i < first else "stale"
        steps.append(_step(s, st, parked, rounds.get(s.id)))
    return steps


def _phase(rail: list[RailStep]) -> Phase:
    """The focus stage's phase, else the last done stage's, else the inbox. Skipped steps never
    count: they are neither `current`/`failed` nor `done`."""
    focus = next((r for r in rail if r.state in ("current", "failed")), None)
    if focus is not None:
        return focus.phase
    done = [r for r in rail if r.state == "done"]
    return done[-1].phase if done else "inbox"


def _phase_track(
    standing: Standing, state: TicketState, rail: list[RailStep], phase: Phase
) -> list[PhaseStep]:
    """Every phase in order. Its two ends are the ticket's: the inbox is current until the first
    run, done once the ticket is. The phases between are their stages'."""
    first, *stages, last = PHASES
    return [
        PhaseStep(phase=first, state="current" if standing == "inbox" else "done"),
        *(PhaseStep(phase=p, state=_phase_state(p, state, rail, phase)) for p in stages),
        PhaseStep(phase=last, state="done" if state == "done" else "pending"),
    ]


def _phase_state(p: Phase, state: TicketState, rail: list[RailStep], phase: Phase) -> PhaseState:
    """A stage phase: `skipped` without an enabled stage, `done` once all of them are (or the
    ticket is done), else `failed`, `current` (also the ticket's own phase), `stale` or
    `pending`: the first that any of them is."""
    states = {r.state for r in rail if r.phase == p} - {"skipped"}
    if not states:
        return "skipped"
    if state == "done" or states == {"done"}:
        return "done"
    if "failed" in states:
        return "failed"
    if "current" in states or phase == p:
        return "current"
    return "stale" if "stale" in states else "pending"


def _line(
    text: str, run_id: int | None, progress: TaskProgress | None = None, *, actor: str | None = None
) -> NowInfo:
    return NowInfo(actor=actor, person=None, text=text, run_id=run_id, progress=progress)


def _now(facts: TicketFacts, run: RunFact, nxt: str | None) -> NowInfo:
    """An open ticket's now line: what its run's state asks for. A finished run reads "continue"
    while an enabled stage follows it (SPEC §16)."""
    pipeline = facts.pipeline
    match run.state:
        case Queued(reason=reason):
            return _line(reason or _waiting(run, "waiting for"), run.id)
        case Dispatched():
            return _line(_waiting(run, "starting on"), run.id)
        case Running():
            sr = _first(run, "running")
            actor = _stage_name(pipeline, sr.stage_id) if sr is not None else None
            return _line(facts.next_task or "working", run.id, facts.tasks, actor=actor)
        case Failed(status=status):
            culprit = _culprit(run)
            what = _stage_name(pipeline, culprit.stage_id) if culprit is not None else "run"
            return _line(f"{what} {status}: retry or adjust", run.id)
        case Finished():
            if nxt is None:
                return _line("review the result and complete it", run.id, facts.tasks)
            last = _stage_name(pipeline, run.stage_ids[-1]).lower()
            return _line(f"review {last} and continue", run.id, facts.tasks)


def _stage_name(pipeline: Pipeline, stage_id: str) -> str:
    """Its name in the current pipeline; a stage no longer there by its id."""
    return next((s.name for s in pipeline.stages if s.id == stage_id), stage_id)


def _waiting(run: RunFact, verb: str) -> str:
    """Who a run that hasn't started waits for: a runner, or (`verb`) its creator's client."""
    if run.target == "server":
        return "queued for a runner"
    whose = f"{run.creator_name}'s" if run.creator_name else "a"
    return f"{verb} {whose} client"
