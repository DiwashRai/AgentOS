"""tickets/board.py: the SPEC §13 board model, table-driven (SPEC §14 "Pure units")."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agentos.models import (
    DispatchBlock,
    NowInfo,
    Person,
    Pipeline,
    RunStatus,
    Stage,
    StagePhase,
    StageRunStatus,
    TaskProgress,
    TicketState,
    WorkspaceMode,
)
from agentos.pipeline.scaffold import default_pipeline
from agentos.tickets.board import (
    ACTIVE,
    RunFact,
    StageRunFact,
    TicketFacts,
    derive,
    for_viewer,
    next_stage,
)

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
CREATED = T0 - timedelta(days=1)
UPDATED = T0 + timedelta(days=1)
ME = Person(id=1, display_name="Dev", avatar_url=None)
SAM = Person(id=2, display_name="Sam Lee", avatar_url=None)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def stage(
    sid: str, phase: StagePhase, *, enabled: bool = True, gate: bool = False, name: str = ""
) -> Stage:
    return Stage(
        id=sid,
        name=name or sid.capitalize(),
        kind="agent",
        phase=phase,
        enabled=enabled,
        gate_after=gate,
    )


def pipe(*stages: Stage) -> Pipeline:
    return Pipeline(stages=list(stages))


# plan (gate) → implement → [lint: verify, disabled] → review → open-pr. Verify is skipped.
DEFAULT = default_pipeline()
WITH_LINT = default_pipeline(lint=True)
WITHOUT_REVIEW = pipe(*(s for s in DEFAULT.stages if s.id != "review"))
REVIEW_DISABLED = pipe(
    *(s.model_copy(update={"enabled": False}) if s.id == "review" else s for s in DEFAULT.stages)
)
OPEN_PR_DISABLED = pipe(
    *(s.model_copy(update={"enabled": False}) if s.id == "open-pr" else s for s in DEFAULT.stages)
)


def run(
    status: RunStatus,
    stages: list[tuple[str, StageRunStatus]],
    *,
    start: float = 0,
    unstarted: Collection[str] = (),
) -> RunFact:
    """Stage i starts at start+4i and ends 3 minutes later; `pending` and `unstarted` stages never
    started (terminal side effects mark unfinished stage_runs `cancelled` without a start)."""

    def began(sid: str, st: StageRunStatus) -> bool:
        return st != "pending" and sid not in unstarted

    return RunFact(
        id=7,
        status=status,
        from_stage=stages[0][0],
        stage_ids=tuple(s for s, _ in stages),
        stage_runs=tuple(
            StageRunFact(
                idx=i,
                stage_id=s,
                status=st,
                started_at=at(start + 4 * i) if began(s, st) else None,
                finished_at=at(start + 4 * i + 3) if began(s, st) and st != "running" else None,
            )
            for i, (s, st) in enumerate(stages)
        ),
        created_at=at(start - 1),
        started_at=None if status in ("queued", "dispatched") else at(start),
        finished_at=None if status in ACTIVE else at(start + 4 * len(stages)),
    )


def facts(
    latest: RunFact | None = None,
    *,
    state: TicketState = "open",
    owner: Person | None = None,
    pipeline: Pipeline = DEFAULT,
    ever: Collection[str] = (),
    tasks: TaskProgress | None = None,
    next_task: str | None = None,
) -> TicketFacts:
    return TicketFacts(
        state=state,
        owner=owner,
        pipeline=pipeline,
        created_at=CREATED,
        updated_at=UPDATED,
        latest_run=latest,
        ever_succeeded=frozenset(ever),
        tasks=tasks,
        next_task=next_task,
    )


def rail_states(f: TicketFacts) -> str:
    return " ".join(step.state for step in derive(f).rail)


def track_states(f: TicketFacts) -> str:
    return " ".join(step.state for step in derive(f).phase_track)


def now(
    text: str,
    *,
    actor: str | None = None,
    person: Person | None = None,
    run_id: int | None = 7,
    progress: TaskProgress | None = None,
) -> NowInfo:
    return NowInfo(actor=actor, person=person, text=text, run_id=run_id, progress=progress)


GATED_PLAN = run("gated", [("plan", "succeeded")])
FAILED_REVIEW = run("failed", [("implement", "succeeded"), ("review", "failed")])
REST: list[tuple[str, StageRunStatus]] = [
    ("implement", "cancelled"),
    ("review", "cancelled"),
    ("open-pr", "cancelled"),
]
NEVER_CONNECTED = run("failed", REST, unstarted={"implement", "review", "open-pr"})


# ------------------------------------------------------------------ attention


@pytest.mark.parametrize(
    ("state", "status", "owner", "attention", "person"),
    [
        pytest.param("open", None, None, "inbox", None, id="open-no-runs"),
        pytest.param("open", None, SAM, "inbox", None, id="open-no-runs-owned"),
        pytest.param("open", None, ME, "inbox", None, id="open-no-runs-viewer-owns"),
        pytest.param("done", None, ME, "done", None, id="done-no-runs"),
        pytest.param("done", "running", None, "done", None, id="done-beats-active-run"),
        pytest.param("closed", "failed", None, "done", None, id="closed"),
        pytest.param("open", "queued", SAM, "queued", None, id="queued"),
        pytest.param("open", "dispatched", None, "queued", None, id="dispatched"),
        pytest.param("open", "running", SAM, "running", None, id="running"),
        pytest.param("open", "failed", SAM, "blocked", None, id="failed"),
        pytest.param("open", "cancelled", ME, "blocked", None, id="cancelled"),
        pytest.param("open", "gated", None, "needs_you", None, id="gated-unowned"),
        pytest.param("open", "gated", ME, "needs_you", None, id="gated-viewer-owns"),
        pytest.param("open", "gated", SAM, "waiting", SAM, id="gated-someone-else-owns"),
        pytest.param("open", "succeeded", None, "needs_you", None, id="succeeded-unowned"),
        pytest.param("open", "succeeded", ME, "needs_you", None, id="succeeded-viewer-owns"),
        pytest.param("open", "succeeded", SAM, "waiting", SAM, id="succeeded-someone-else"),
    ],
)
def test_attention_per_viewer(
    state: TicketState,
    status: RunStatus | None,
    owner: Person | None,
    attention: str,
    person: Person | None,
) -> None:
    latest = run(status, [("plan", "succeeded")]) if status else None
    view = for_viewer(derive(facts(latest, state=state, owner=owner)), ME.id)
    assert view.attention == attention
    assert view.now.person == person


def test_the_viewer_independent_board_never_names_a_person() -> None:
    board = derive(facts(GATED_PLAN, owner=SAM))
    assert board.standing == "awaiting"
    assert board.now.person is None
    assert for_viewer(board, SAM.id).attention == "needs_you"
    assert for_viewer(board, ME.id).attention == "waiting"


# ------------------------------------------------------------------ rail

PLAN_OK = {"plan"}


@pytest.mark.parametrize(
    ("f", "expected"),
    [
        pytest.param(facts(), "pending pending skipped pending pending", id="no-runs"),
        pytest.param(
            facts(run("queued", [("plan", "pending")])),
            "current pending skipped pending pending",
            id="queued-first-stage-current",
        ),
        pytest.param(
            facts(
                run("dispatched", [("implement", "pending"), ("review", "pending")]), ever=PLAN_OK
            ),
            "done current skipped pending pending",
            id="dispatched",
        ),
        pytest.param(
            facts(
                run("running", [("implement", "succeeded"), ("review", "running")]), ever=PLAN_OK
            ),
            "done done skipped current pending",
            id="running-second-stage",
        ),
        pytest.param(
            facts(
                run("running", [("implement", "succeeded"), ("review", "pending")]), ever=PLAN_OK
            ),
            "done done skipped current pending",
            id="running-between-stages",
        ),
        pytest.param(
            facts(
                run("running", [("implement", "running"), ("review", "pending")]),
                ever={"plan", "implement", "review"},
            ),
            "done current skipped pending pending",
            id="segment-stages-not-yet-rerun-are-pending",
        ),
        pytest.param(
            facts(GATED_PLAN), "current pending skipped pending pending", id="gated-awaits"
        ),
        pytest.param(
            facts(GATED_PLAN, ever={"plan", "implement", "review"}),
            "current stale skipped stale pending",
            id="stale-after-rerunning-an-earlier-stage",
        ),
        pytest.param(
            facts(FAILED_REVIEW, ever=PLAN_OK),
            "done done skipped failed pending",
            id="failed-stage",
        ),
        pytest.param(
            facts(
                run(
                    "failed",
                    [("implement", "succeeded"), ("review", "failed"), ("open-pr", "cancelled")],
                    unstarted={"open-pr"},
                ),
                ever=PLAN_OK,
            ),
            "done done skipped failed pending",
            id="unstarted-stages-cancelled-by-terminal-side-effect",
        ),
        pytest.param(
            facts(run("cancelled", REST, unstarted={"review", "open-pr"}), ever=PLAN_OK),
            "done failed skipped pending pending",
            id="cancelled-mid-stage",
        ),
        pytest.param(
            facts(run("cancelled", [("plan", "cancelled")], unstarted={"plan"})),
            "failed pending skipped pending pending",
            id="cancelled-while-queued",
        ),
        pytest.param(
            facts(NEVER_CONNECTED, ever=PLAN_OK),
            "done failed skipped pending pending",
            id="worker-never-connected",
        ),
        pytest.param(
            facts(replace(NEVER_CONNECTED, stage_runs=()), ever=PLAN_OK),
            "done failed skipped pending pending",
            id="no-stage-runs",
        ),
        pytest.param(
            facts(
                run("succeeded", [("implement", "succeeded"), ("review", "succeeded")]),
                ever=PLAN_OK,
            ),
            "done done skipped done pending",
            id="succeeded",
        ),
        pytest.param(
            facts(run("succeeded", [("implement", "succeeded"), ("review", "succeeded")])),
            "pending done skipped done pending",
            id="earlier-stage-never-run",
        ),
        pytest.param(
            facts(run("running", [("open-pr", "running")]), ever={"plan", "implement"}),
            "done done skipped pending current",
            id="earlier-stage-never-succeeded",
        ),
        pytest.param(
            facts(GATED_PLAN, state="done", ever={"implement"}),
            "done done skipped pending pending",
            id="done-ticket",
        ),
        pytest.param(
            facts(FAILED_REVIEW, state="done", ever=PLAN_OK),
            "done done skipped pending pending",
            id="done-ticket-with-failed-run",
        ),
        pytest.param(
            facts(
                run(
                    "succeeded",
                    [("implement", "succeeded"), ("review", "succeeded"), ("open-pr", "succeeded")],
                ),
                state="done",
                ever=PLAN_OK,
            ),
            "done done skipped done done",
            id="done-ticket-every-stage-run",
        ),
        pytest.param(
            facts(GATED_PLAN, state="closed"),
            "current pending skipped pending pending",
            id="closed-ticket-like-open",
        ),
    ],
)
def test_rail(f: TicketFacts, expected: str) -> None:
    assert rail_states(f) == expected


@pytest.mark.parametrize("pipeline", [DEFAULT, WITH_LINT], ids=["lint-disabled", "lint-enabled"])
def test_rail_lists_every_stage_of_the_current_pipeline(pipeline: Pipeline) -> None:
    board = derive(facts(pipeline=pipeline))
    assert [(r.stage_id, r.name, r.phase) for r in board.rail] == [
        ("plan", "Plan", "plan"),
        ("implement", "Implement", "build"),
        ("lint", "Lint", "verify"),
        ("review", "Review", "review"),
        ("open-pr", "Open PR", "review"),
    ]


@pytest.mark.parametrize(
    ("f", "expected"),
    [
        pytest.param(
            facts(FAILED_REVIEW, pipeline=WITHOUT_REVIEW, ever=PLAN_OK),
            [("plan", "done"), ("implement", "done"), ("lint", "skipped"), ("open-pr", "pending")],
            id="removed-disappears",
        ),
        pytest.param(
            facts(FAILED_REVIEW, pipeline=REVIEW_DISABLED, ever=PLAN_OK),
            [
                ("plan", "done"),
                ("implement", "done"),
                ("lint", "skipped"),
                ("review", "skipped"),
                ("open-pr", "pending"),
            ],
            id="disabled-focus-is-skipped",
        ),
        pytest.param(
            facts(run("failed", [("gone", "failed")]), ever=PLAN_OK),
            [
                ("plan", "done"),
                ("implement", "pending"),
                ("lint", "skipped"),
                ("review", "pending"),
                ("open-pr", "pending"),
            ],
            id="whole-segment-gone",
        ),
    ],
)
def test_run_stages_no_longer_in_the_pipeline(
    f: TicketFacts, expected: list[tuple[str, str]]
) -> None:
    assert [(r.stage_id, r.state) for r in derive(f).rail] == expected


def test_rail_follows_the_current_order() -> None:
    reordered = pipe(*(DEFAULT.stages[i] for i in (1, 0, 2, 3, 4)))  # implement before plan
    f = facts(GATED_PLAN, pipeline=reordered, ever={"implement", "review"})
    assert [(r.stage_id, r.state) for r in derive(f).rail] == [
        ("implement", "done"),
        ("plan", "current"),
        ("lint", "skipped"),
        ("review", "stale"),
        ("open-pr", "pending"),
    ]


# ------------------------------------------------------------------ gates and skipped stages

# implement (gate) → review (gate, last enabled) → open-pr (disabled, gate ignored).
GATES = pipe(
    stage("implement", "build", gate=True),
    stage("review", "review", gate=True),
    stage("open-pr", "review", enabled=False, gate=True, name="Open PR"),
)
IMPL_GATED = run("gated", [("implement", "succeeded")])
REVIEW_SUCCEEDED = run("succeeded", [("review", "succeeded")])


def gates(f: TicketFacts, viewer: Person = ME) -> list[tuple[str, str | None, Person | None]]:
    """(stage, gate state, gate person) per rail step, as the viewer sees it."""
    return [
        (r.stage_id, r.gate.state if r.gate else None, r.gate.person if r.gate else None)
        for r in for_viewer(derive(f), viewer.id).rail
    ]


@pytest.mark.parametrize(
    ("f", "expected"),
    [
        pytest.param(
            facts(pipeline=GATES),
            [("implement", "pending", None), ("review", "pending", None), ("open-pr", None, None)],
            id="no-runs-pending",
        ),
        pytest.param(
            facts(run("running", [("implement", "running")]), pipeline=GATES),
            [("implement", "pending", None), ("review", "pending", None), ("open-pr", None, None)],
            id="running-into-a-gate-pending",
        ),
        pytest.param(
            facts(IMPL_GATED, pipeline=GATES),
            [("implement", "you", None), ("review", "pending", None), ("open-pr", None, None)],
            id="parked-unowned-you",
        ),
        pytest.param(
            facts(IMPL_GATED, pipeline=GATES, owner=ME),
            [("implement", "you", None), ("review", "pending", None), ("open-pr", None, None)],
            id="parked-viewer-owns-you",
        ),
        pytest.param(
            facts(IMPL_GATED, pipeline=GATES, owner=SAM),
            [("implement", "waiting", SAM), ("review", "pending", None), ("open-pr", None, None)],
            id="parked-someone-else-waiting",
        ),
        pytest.param(
            facts(run("running", [("review", "running")]), pipeline=GATES, ever={"implement"}),
            [("implement", "passed", None), ("review", "pending", None), ("open-pr", None, None)],
            id="continued-passed",
        ),
        pytest.param(
            facts(REVIEW_SUCCEEDED, pipeline=GATES, ever={"implement"}, owner=SAM),
            [("implement", "passed", None), ("review", "waiting", SAM), ("open-pr", None, None)],
            id="final-gate-parks-a-succeeded-run",
        ),
        pytest.param(
            facts(REVIEW_SUCCEEDED, pipeline=GATES, ever={"implement"}, state="done"),
            [("implement", "passed", None), ("review", "passed", None), ("open-pr", None, None)],
            id="final-gate-passed-once-done",
        ),
        pytest.param(
            facts(
                run("failed", [("review", "failed")]), pipeline=GATES, ever={"implement", "review"}
            ),
            [("implement", "passed", None), ("review", "pending", None), ("open-pr", None, None)],
            id="failed-at-a-gated-stage-pending",
        ),
        pytest.param(
            facts(IMPL_GATED, pipeline=GATES, state="closed"),
            [("implement", "pending", None), ("review", "pending", None), ("open-pr", None, None)],
            id="closed-never-parks",
        ),
    ],
)
def test_gates(f: TicketFacts, expected: list[tuple[str, str | None, Person | None]]) -> None:
    assert gates(f) == expected


def test_gate_names() -> None:
    gate = derive(facts(pipeline=GATES)).rail[0].gate
    assert gate is not None
    assert (gate.label, gate.short) == ("Implement sign-off", "Sign-off")


def test_the_viewer_independent_rail_leaves_the_parked_gate_pending() -> None:
    board = derive(facts(IMPL_GATED, pipeline=GATES, owner=SAM))
    assert board.parked == "implement"
    assert [r.gate.state for r in board.rail if r.gate] == ["pending", "pending"]
    assert gates(facts(IMPL_GATED, pipeline=GATES, owner=SAM), SAM)[0] == ("implement", "you", None)


@pytest.mark.parametrize(
    "f",
    [
        pytest.param(facts(GATED_PLAN, pipeline=OPEN_PR_DISABLED), id="open"),
        pytest.param(facts(GATED_PLAN, pipeline=REVIEW_DISABLED, state="done"), id="done"),
    ],
)
def test_disabled_stages_are_skipped_without_a_gate(f: TicketFacts) -> None:
    disabled = {s.id for s in f.pipeline.stages if not s.enabled}
    skipped = [r for r in derive(f).rail if r.stage_id in disabled]
    assert [(r.state, r.gate) for r in skipped] == [("skipped", None)] * len(disabled)


@pytest.mark.parametrize(
    "latest",
    [
        pytest.param(None, id="no-runs"),
        pytest.param(GATED_PLAN, id="gated"),
        pytest.param(FAILED_REVIEW, id="failed"),
        pytest.param(run("running", [("implement", "running")]), id="running"),
    ],
)
def test_skipped_stages_never_change_phase_or_phase_track(latest: RunFact | None) -> None:
    """A disabled stage counts like a removed one for phase and phase_track."""
    with_lint_disabled = derive(facts(latest, pipeline=DEFAULT, ever=PLAN_OK))
    lint_removed = derive(
        facts(latest, pipeline=pipe(*(s for s in DEFAULT.stages if s.id != "lint")), ever=PLAN_OK)
    )
    assert with_lint_disabled.phase == lint_removed.phase
    assert with_lint_disabled.phase_track == lint_removed.phase_track


# ------------------------------------------------------------------ phase and phase_track

PHASE_CASES = [
    pytest.param(facts(), "inbox", "current pending pending skipped pending pending", id="no-runs"),
    pytest.param(
        facts(run("queued", [("plan", "pending")])),
        "plan",
        "done current pending skipped pending pending",
        id="queued",
    ),
    pytest.param(
        facts(run("running", [("implement", "running")]), pipeline=WITH_LINT, ever=PLAN_OK),
        "build",
        "done done current pending pending pending",
        id="running-verify-not-skipped",
    ),
    pytest.param(
        facts(GATED_PLAN), "plan", "done current pending skipped pending pending", id="gated"
    ),
    pytest.param(
        facts(GATED_PLAN, ever={"plan", "implement", "review"}),
        "plan",
        "done current stale skipped stale pending",
        id="stale",
    ),
    pytest.param(
        facts(FAILED_REVIEW, ever=PLAN_OK),
        "review",
        "done done done skipped failed pending",
        id="failed",
    ),
    pytest.param(
        facts(run("cancelled", [("plan", "cancelled")], unstarted={"plan"})),
        "plan",
        "done failed pending skipped pending pending",
        id="cancelled-while-queued",
    ),
    pytest.param(
        facts(
            run(
                "succeeded",
                [("implement", "succeeded"), ("review", "succeeded"), ("open-pr", "succeeded")],
            ),
            ever=PLAN_OK,
        ),
        "review",
        "done done done skipped done pending",
        id="succeeded-open",
    ),
    pytest.param(
        facts(
            run("succeeded", [("implement", "succeeded"), ("review", "succeeded")]),
            ever=PLAN_OK,
        ),
        "review",
        "done done done skipped current pending",
        id="ticket-phase-with-a-stage-left-is-current",
    ),
    pytest.param(
        facts(FAILED_REVIEW, pipeline=WITHOUT_REVIEW, ever=PLAN_OK),
        "build",
        "done done done skipped pending pending",
        id="focus-stage-removed-falls-back-to-last-done",
    ),
    pytest.param(
        facts(NEVER_CONNECTED, pipeline=pipe(stage("other", "plan")), ever=()),
        "inbox",
        "done pending skipped skipped skipped pending",
        id="nothing-left-falls-back-to-inbox",
    ),
    pytest.param(
        facts(GATED_PLAN, state="done", ever=PLAN_OK),
        "done",
        "done done done skipped done done",
        id="done",
    ),
    pytest.param(
        facts(state="done"), "done", "done done done skipped done done", id="done-no-runs"
    ),
    pytest.param(
        facts(state="closed"),
        "inbox",
        "done pending pending skipped pending pending",
        id="closed-no-runs",
    ),
]


@pytest.mark.parametrize(("f", "phase", "track"), PHASE_CASES)
def test_phase_and_phase_track(f: TicketFacts, phase: str, track: str) -> None:
    board = derive(f)
    assert board.phase == phase
    assert [s.phase for s in board.phase_track] == [
        "inbox",
        "plan",
        "build",
        "verify",
        "review",
        "done",
    ]
    assert track_states(f) == track


# ------------------------------------------------------------------ attention_since

PLAN_THEN_IMPLEMENT: list[tuple[str, StageRunStatus]] = [
    ("plan", "succeeded"),
    ("implement", "running"),
]


@pytest.mark.parametrize(
    ("f", "since"),
    [
        pytest.param(facts(), CREATED, id="inbox-ticket-created"),
        pytest.param(facts(owner=ME), CREATED, id="inbox-owned-ticket-created"),
        pytest.param(facts(GATED_PLAN, state="done"), UPDATED, id="done-updated"),
        pytest.param(facts(GATED_PLAN, state="closed"), UPDATED, id="closed-updated"),
        pytest.param(facts(run("queued", [("plan", "pending")], start=30)), at(29), id="queued"),
        pytest.param(
            facts(run("dispatched", [("plan", "pending")], start=30)), at(29), id="dispatched"
        ),
        pytest.param(
            facts(run("running", PLAN_THEN_IMPLEMENT, start=30)),
            at(30),
            id="running-run-start-not-stage-start",
        ),
        pytest.param(
            facts(replace(run("running", PLAN_THEN_IMPLEMENT, start=30), started_at=None)),
            at(29),
            id="running-without-start-falls-back-to-created",
        ),
        pytest.param(facts(run("gated", [("plan", "succeeded")], start=30)), at(34), id="gated"),
        pytest.param(
            facts(run("succeeded", [("review", "succeeded")], start=30)), at(34), id="succeeded"
        ),
        pytest.param(
            facts(run("failed", [("implement", "succeeded"), ("review", "failed")], start=30)),
            at(38),
            id="failed",
        ),
        pytest.param(
            facts(run("cancelled", [("plan", "cancelled")], start=30)), at(34), id="cancelled"
        ),
        pytest.param(
            facts(replace(run("failed", [("plan", "failed")], start=30), finished_at=None)),
            at(29),
            id="finished-without-timestamp-falls-back-to-created",
        ),
    ],
)
def test_attention_since(f: TicketFacts, since: datetime) -> None:
    assert derive(f).attention_since == since


# ------------------------------------------------------------------ next_stage


@pytest.mark.parametrize(
    ("pipeline", "latest", "expected"),
    [
        pytest.param(DEFAULT, None, "plan", id="no-runs"),
        pytest.param(
            pipe(stage("plan", "plan", enabled=False), stage("implement", "build")),
            None,
            "implement",
            id="no-runs-first-disabled",
        ),
        pytest.param(pipe(stage("plan", "plan", enabled=False)), None, None, id="none-enabled"),
        pytest.param(DEFAULT, run("queued", [("plan", "pending")]), None, id="queued"),
        pytest.param(DEFAULT, run("running", [("plan", "running")]), None, id="running"),
        pytest.param(
            DEFAULT, run("succeeded", [("open-pr", "succeeded")]), None, id="succeeded-complete"
        ),
        pytest.param(
            DEFAULT,
            run("succeeded", [("implement", "succeeded"), ("review", "succeeded")]),
            "open-pr",
            id="succeeded-with-a-stage-enabled-after-it",
        ),
        pytest.param(
            OPEN_PR_DISABLED,
            run("succeeded", [("review", "succeeded")]),
            None,
            id="succeeded-nothing-enabled-after",
        ),
        pytest.param(
            DEFAULT,
            run("succeeded", [("gone", "succeeded")]),
            None,
            id="succeeded-anchor-removed-not-restarted",
        ),
        pytest.param(
            pipe(*(DEFAULT.stages[i] for i in (0, 3, 4, 1))),
            run("succeeded", [("plan", "succeeded"), ("review", "succeeded")]),
            "open-pr",
            id="succeeded-current-order",
        ),
        pytest.param(DEFAULT, GATED_PLAN, "implement", id="gated"),
        pytest.param(
            DEFAULT,
            run("gated", [("implement", "succeeded")]),
            "review",
            id="gated-skips-disabled-lint",
        ),
        pytest.param(
            DEFAULT, run("gated", [("gone", "succeeded")]), "plan", id="gated-anchor-removed"
        ),
        pytest.param(
            REVIEW_DISABLED,
            run("gated", [("review", "succeeded")]),
            "open-pr",
            id="gated-anchor-disabled",
        ),
        pytest.param(
            OPEN_PR_DISABLED,
            run("gated", [("review", "succeeded")]),
            None,
            id="gate-on-the-last-enabled-stage",
        ),
        pytest.param(
            pipe(*(DEFAULT.stages[i] for i in (1, 0, 2, 3, 4))),
            GATED_PLAN,
            "review",
            id="gated-current-order",
        ),
        pytest.param(DEFAULT, FAILED_REVIEW, "review", id="failed-retries-anchor"),
        pytest.param(REVIEW_DISABLED, FAILED_REVIEW, "open-pr", id="failed-anchor-disabled"),
        pytest.param(WITHOUT_REVIEW, FAILED_REVIEW, "plan", id="failed-anchor-removed"),
        pytest.param(
            OPEN_PR_DISABLED,
            run("failed", [("open-pr", "failed")]),
            None,
            id="failed-anchor-disabled-nothing-follows",
        ),
        pytest.param(
            DEFAULT,
            run("cancelled", REST, unstarted={"review", "open-pr"}),
            "implement",
            id="cancelled-mid-stage",
        ),
        pytest.param(DEFAULT, NEVER_CONNECTED, "implement", id="no-culprit-first-stage"),
        pytest.param(
            DEFAULT,
            run(
                "failed",
                [("implement", "succeeded"), ("review", "cancelled")],
                unstarted={"review"},
            ),
            "review",
            id="no-culprit-first-non-succeeded",
        ),
        pytest.param(
            DEFAULT,
            run("failed", [("implement", "succeeded"), ("review", "succeeded")]),
            "review",
            id="no-culprit-all-succeeded-last-stage",
        ),
        pytest.param(
            DEFAULT,
            replace(FAILED_REVIEW, stage_ids=(), stage_runs=()),
            "plan",
            id="empty-run",
        ),
    ],
)
def test_next_stage(pipeline: Pipeline, latest: RunFact | None, expected: str | None) -> None:
    assert next_stage(pipeline, latest) == expected
    assert derive(facts(latest, pipeline=pipeline)).next_stage == expected


def test_succeeded_run_with_a_stage_following_is_not_complete() -> None:
    """SPEC §16: complete = no enabled stage after the last one run, not the `succeeded` status.
    next_stage, now and phase_track must agree."""
    f = facts(run("succeeded", [("implement", "succeeded"), ("review", "succeeded")]), ever=PLAN_OK)
    board = derive(f)
    assert board.next_stage == "open-pr"
    assert board.now.text == "review review and continue"
    assert board.phase == "review"
    assert track_states(f) == "done done done skipped current pending"
    assert rail_states(f) == "done done skipped done pending"


# ------------------------------------------------------------------ now

TASKS = TaskProgress(done=2, total=5)


@pytest.mark.parametrize(
    ("f", "expected"),
    [
        pytest.param(
            facts(run("running", [("implement", "running")]), tasks=TASKS, next_task="Run tests"),
            now("Run tests", actor="Implement", progress=TASKS),
            id="running",
        ),
        pytest.param(
            facts(run("running", [("implement", "running")]), tasks=TASKS),
            now("working", actor="Implement", progress=TASKS),
            id="running-no-open-task",
        ),
        pytest.param(
            facts(
                run("running", [("implement", "succeeded"), ("review", "pending")]),
                tasks=TASKS,
                next_task="x",
            ),
            now("x", progress=TASKS),
            id="running-between-stages",
        ),
        pytest.param(
            facts(run("queued", [("plan", "pending")]), tasks=TASKS, next_task="x"),
            now("queued for a runner"),
            id="queued",
        ),
        pytest.param(
            facts(run("dispatched", [("plan", "pending")])),
            now("queued for a runner"),
            id="dispatched",
        ),
        pytest.param(
            facts(
                replace(
                    run("queued", [("plan", "pending")]), target="client", creator_name="Sam Lee"
                )
            ),
            now("waiting for Sam Lee's client"),
            id="queued-for-a-client",
        ),
        pytest.param(
            facts(replace(run("queued", [("plan", "pending")]), target="client")),
            now("waiting for a client"),
            id="queued-for-a-client-without-a-creator",
        ),
        pytest.param(
            facts(replace(run("queued", [("plan", "pending")]), queue_reason="held for a reason")),
            now("held for a reason"),
            id="queued-with-a-reason",
        ),
        pytest.param(
            facts(
                replace(run("dispatched", [("plan", "pending")]), target="client", creator_name="S")
            ),
            now("starting on S's client"),
            id="handed-to-a-client",
        ),
        pytest.param(
            facts(GATED_PLAN, owner=ME, tasks=TASKS),
            now("review plan and continue", progress=TASKS),
            id="gated-needs-you",
        ),
        pytest.param(
            facts(GATED_PLAN, owner=SAM),
            now("review plan and continue", person=SAM),
            id="gated-waiting",
        ),
        pytest.param(
            facts(run("gated", [("review", "succeeded")]), pipeline=OPEN_PR_DISABLED),
            now("review the result and complete it"),
            id="gated-with-nothing-left",
        ),
        pytest.param(
            facts(run("succeeded", [("open-pr", "succeeded")]), tasks=TASKS),
            now("review the result and complete it", progress=TASKS),
            id="succeeded-complete",
        ),
        pytest.param(
            facts(run("succeeded", [("plan", "succeeded"), ("implement", "succeeded")]), owner=SAM),
            now("review implement and continue", person=SAM),
            id="succeeded-with-a-stage-following-reads-like-gated",
        ),
        pytest.param(
            facts(run("succeeded", [("gone", "succeeded")])),
            now("review the result and complete it"),
            id="succeeded-anchor-removed",
        ),
        pytest.param(
            facts(FAILED_REVIEW, tasks=TASKS),
            now("Review failed: retry or adjust"),
            id="blocked-failed-stage",
        ),
        pytest.param(
            facts(run("cancelled", REST, unstarted={"review", "open-pr"})),
            now("Implement cancelled: retry or adjust"),
            id="blocked-cancelled-stage",
        ),
        pytest.param(
            facts(run("failed", REST, unstarted={"review", "open-pr"})),
            now("Implement failed: retry or adjust"),
            id="blocked-worker-lost-mid-stage",
        ),
        pytest.param(
            facts(NEVER_CONNECTED), now("run failed: retry or adjust"), id="blocked-no-stage"
        ),
        pytest.param(
            facts(run("cancelled", [("plan", "cancelled")], unstarted={"plan"})),
            now("run cancelled: retry or adjust"),
            id="blocked-cancelled-while-queued",
        ),
        pytest.param(
            facts(run("failed", [("gone", "failed")])),
            now("gone failed: retry or adjust"),
            id="blocked-stage-removed-uses-id",
        ),
        pytest.param(
            facts(tasks=TASKS), now("add context, then dispatch", run_id=None), id="inbox"
        ),
        pytest.param(
            facts(FAILED_REVIEW, state="done", tasks=TASKS),
            now("done", progress=TASKS),
            id="done",
        ),
        pytest.param(facts(state="done"), now("done", run_id=None), id="done-no-runs"),
    ],
)
def test_now(f: TicketFacts, expected: NowInfo) -> None:
    assert for_viewer(derive(f), ME.id).now == expected


# ------------------------------------------------------------------ dispatch_block (request 22A)


@pytest.mark.parametrize(
    ("state", "mode", "base", "blocked"),
    [
        pytest.param("open", "repo", None, True, id="repo-without-base"),
        pytest.param("open", "repo", "main", False, id="repo-with-base"),
        pytest.param("open", "none", None, False, id="no-repo"),
        pytest.param("done", "repo", None, False, id="done"),
        pytest.param("closed", "repo", None, False, id="closed"),
    ],
)
def test_dispatch_block(
    state: TicketState, mode: WorkspaceMode, base: str | None, blocked: bool
) -> None:
    f = replace(facts(GATED_PLAN, state=state), workspace_mode=mode, base_branch=base)
    expected = DispatchBlock(code="base_branch_missing", text="Set a base branch first")
    assert derive(f).dispatch_block == (expected if blocked else None)
