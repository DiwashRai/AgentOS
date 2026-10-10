"""tickets/board.py: the SPEC §13 board model, table-driven (SPEC §14 "Pure units")."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from agentos.models import (
    NowInfo,
    Person,
    Pipeline,
    RunStatus,
    Stage,
    StagePhase,
    StageRunStatus,
    TaskProgress,
    TicketState,
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
# Two build stages in a row, for the phase_since streak.
TWO_BUILDS = pipe(
    stage("plan", "plan"),
    stage("implement", "build"),
    stage("fix", "build"),
    stage("review", "review"),
)


def run(
    status: RunStatus,
    stages: list[tuple[str, StageRunStatus]],
    *,
    rid: int = 7,
    start: float = 0,
    unstarted: Collection[str] = (),
) -> RunFact:
    """Stage i starts at start+4i and ends 3 minutes later; `pending` and `unstarted` stages never
    started (terminal side effects mark unfinished stage_runs `cancelled` without a start)."""

    def began(sid: str, st: StageRunStatus) -> bool:
        return st != "pending" and sid not in unstarted

    return RunFact(
        id=rid,
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
        pytest.param("open", None, None, "up_next", None, id="open-no-runs"),
        pytest.param("done", None, ME, "done", None, id="done-no-runs"),
        pytest.param("done", "running", None, "done", None, id="done-beats-active-run"),
        pytest.param("closed", "failed", None, "done", None, id="closed"),
        pytest.param("open", "queued", SAM, "moving", None, id="queued"),
        pytest.param("open", "dispatched", None, "moving", None, id="dispatched"),
        pytest.param("open", "running", SAM, "moving", None, id="running"),
        pytest.param("open", "failed", SAM, "stuck", None, id="failed"),
        pytest.param("open", "cancelled", ME, "stuck", None, id="cancelled"),
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
        pytest.param(facts(), "pending pending pending pending", id="no-runs"),
        pytest.param(
            facts(run("queued", [("plan", "pending")])),
            "current pending pending pending",
            id="queued-first-stage-current",
        ),
        pytest.param(
            facts(
                run("dispatched", [("implement", "pending"), ("review", "pending")]), ever=PLAN_OK
            ),
            "done current pending pending",
            id="dispatched",
        ),
        pytest.param(
            facts(
                run("running", [("implement", "succeeded"), ("review", "running")]), ever=PLAN_OK
            ),
            "done done current pending",
            id="running-second-stage",
        ),
        pytest.param(
            facts(
                run("running", [("implement", "succeeded"), ("review", "pending")]), ever=PLAN_OK
            ),
            "done done current pending",
            id="running-between-stages",
        ),
        pytest.param(
            facts(
                run("running", [("implement", "running"), ("review", "pending")]),
                ever={"plan", "implement", "review"},
            ),
            "done current pending pending",
            id="segment-stages-not-yet-rerun-are-pending",
        ),
        pytest.param(facts(GATED_PLAN), "current pending pending pending", id="gated-awaits"),
        pytest.param(
            facts(GATED_PLAN, ever={"plan", "implement", "review"}),
            "current stale stale pending",
            id="stale-after-rerunning-an-earlier-stage",
        ),
        pytest.param(
            facts(FAILED_REVIEW, ever=PLAN_OK), "done done failed pending", id="failed-stage"
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
            "done done failed pending",
            id="unstarted-stages-cancelled-by-terminal-side-effect",
        ),
        pytest.param(
            facts(run("cancelled", REST, unstarted={"review", "open-pr"}), ever=PLAN_OK),
            "done failed pending pending",
            id="cancelled-mid-stage",
        ),
        pytest.param(
            facts(run("cancelled", [("plan", "cancelled")], unstarted={"plan"})),
            "failed pending pending pending",
            id="cancelled-while-queued",
        ),
        pytest.param(
            facts(NEVER_CONNECTED, ever=PLAN_OK),
            "done failed pending pending",
            id="worker-never-connected",
        ),
        pytest.param(
            facts(replace(NEVER_CONNECTED, stage_runs=()), ever=PLAN_OK),
            "done failed pending pending",
            id="no-stage-runs",
        ),
        pytest.param(
            facts(
                run("succeeded", [("implement", "succeeded"), ("review", "succeeded")]),
                ever=PLAN_OK,
            ),
            "done done done pending",
            id="succeeded",
        ),
        pytest.param(
            facts(run("succeeded", [("implement", "succeeded"), ("review", "succeeded")])),
            "pending done done pending",
            id="earlier-stage-never-run",
        ),
        pytest.param(
            facts(run("running", [("open-pr", "running")]), ever={"plan", "implement"}),
            "done done pending current",
            id="earlier-stage-never-succeeded",
        ),
        pytest.param(
            facts(GATED_PLAN, state="done", ever={"implement"}),
            "done done pending pending",
            id="done-ticket",
        ),
        pytest.param(
            facts(FAILED_REVIEW, state="done", ever=PLAN_OK),
            "done done pending pending",
            id="done-ticket-with-failed-run",
        ),
        pytest.param(
            facts(GATED_PLAN, state="closed"),
            "current pending pending pending",
            id="closed-ticket-like-open",
        ),
    ],
)
def test_rail(f: TicketFacts, expected: str) -> None:
    assert rail_states(f) == expected


def test_rail_lists_enabled_stages_of_the_current_pipeline() -> None:
    board = derive(facts(pipeline=WITH_LINT))
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
            [("plan", "done"), ("implement", "done"), ("open-pr", "pending")],
            id="removed",
        ),
        pytest.param(
            facts(FAILED_REVIEW, pipeline=REVIEW_DISABLED, ever=PLAN_OK),
            [("plan", "done"), ("implement", "done"), ("open-pr", "pending")],
            id="disabled",
        ),
        pytest.param(
            facts(run("failed", [("gone", "failed")]), ever=PLAN_OK),
            [
                ("plan", "done"),
                ("implement", "pending"),
                ("review", "pending"),
                ("open-pr", "pending"),
            ],
            id="whole-segment-gone",
        ),
    ],
)
def test_run_stages_no_longer_in_the_pipeline_disappear(
    f: TicketFacts, expected: list[tuple[str, str]]
) -> None:
    assert [(r.stage_id, r.state) for r in derive(f).rail] == expected


def test_rail_follows_the_current_order() -> None:
    reordered = pipe(*(DEFAULT.stages[i] for i in (1, 0, 2, 3, 4)))  # implement before plan
    f = facts(GATED_PLAN, pipeline=reordered, ever={"implement", "review"})
    assert [(r.stage_id, r.state) for r in derive(f).rail] == [
        ("implement", "done"),
        ("plan", "current"),
        ("review", "stale"),
        ("open-pr", "pending"),
    ]


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


# ------------------------------------------------------------------ phase_since


@pytest.mark.parametrize(
    ("f", "since"),
    [
        pytest.param(facts(), CREATED, id="no-runs-created"),
        pytest.param(facts(GATED_PLAN, state="done"), UPDATED, id="done-updated"),
        pytest.param(facts(run("queued", [("plan", "pending")], start=30)), at(29), id="queued"),
        pytest.param(
            facts(run("dispatched", [("plan", "pending")], start=30)), at(29), id="dispatched"
        ),
        pytest.param(
            facts(run("running", [("implement", "running")], start=30)),
            at(30),
            id="running-stage-start",
        ),
        pytest.param(
            facts(
                run("running", [("plan", "succeeded"), ("implement", "running")], start=30),
                pipeline=TWO_BUILDS,
            ),
            at(34),
            id="running-after-another-phase",
        ),
        pytest.param(
            facts(
                run(
                    "running",
                    [("plan", "succeeded"), ("implement", "succeeded"), ("fix", "running")],
                    start=30,
                ),
                pipeline=TWO_BUILDS,
            ),
            at(34),
            id="running-same-phase-streak",
        ),
        pytest.param(
            facts(
                run("running", [("plan", "succeeded"), ("implement", "pending")], start=30),
                pipeline=TWO_BUILDS,
            ),
            at(30),
            id="between-stages-run-start",
        ),
        pytest.param(facts(run("gated", [("plan", "succeeded")], start=30)), at(34), id="gated"),
        pytest.param(
            facts(run("failed", [("implement", "succeeded"), ("review", "failed")], start=30)),
            at(38),
            id="failed",
        ),
    ],
)
def test_phase_since(f: TicketFacts, since: datetime) -> None:
    assert derive(f).phase_since == since


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
    assert rail_states(f) == "done done done pending"


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
            now("review the result and mark done"),
            id="gated-with-nothing-left",
        ),
        pytest.param(
            facts(run("succeeded", [("open-pr", "succeeded")]), tasks=TASKS),
            now("review the result and mark done", progress=TASKS),
            id="succeeded-complete",
        ),
        pytest.param(
            facts(run("succeeded", [("plan", "succeeded"), ("implement", "succeeded")]), owner=SAM),
            now("review implement and continue", person=SAM),
            id="succeeded-with-a-stage-following-reads-like-gated",
        ),
        pytest.param(
            facts(run("succeeded", [("gone", "succeeded")])),
            now("review the result and mark done"),
            id="succeeded-anchor-removed",
        ),
        pytest.param(
            facts(FAILED_REVIEW, tasks=TASKS),
            now("Review failed: retry or adjust"),
            id="stuck-failed-stage",
        ),
        pytest.param(
            facts(run("cancelled", REST, unstarted={"review", "open-pr"})),
            now("Implement cancelled: retry or adjust"),
            id="stuck-cancelled-stage",
        ),
        pytest.param(
            facts(run("failed", REST, unstarted={"review", "open-pr"})),
            now("Implement failed: retry or adjust"),
            id="stuck-worker-lost-mid-stage",
        ),
        pytest.param(
            facts(NEVER_CONNECTED), now("run failed: retry or adjust"), id="stuck-no-stage"
        ),
        pytest.param(
            facts(run("cancelled", [("plan", "cancelled")], unstarted={"plan"})),
            now("run cancelled: retry or adjust"),
            id="stuck-cancelled-while-queued",
        ),
        pytest.param(
            facts(run("failed", [("gone", "failed")])),
            now("gone failed: retry or adjust"),
            id="stuck-stage-removed-uses-id",
        ),
        pytest.param(
            facts(tasks=TASKS), now("add context, then dispatch", run_id=None), id="up-next"
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


# ------------------------------------------------------------------ the stub's seeded tickets

PLAN_TASKS = TaskProgress(done=0, total=4)


def test_seeded_aos_3_running_implement() -> None:
    f = facts(
        run("running", [("implement", "running")], rid=1, start=-6),
        owner=ME,
        pipeline=WITH_LINT,
        ever=PLAN_OK,
        tasks=TaskProgress(done=2, total=5),
        next_task="Run API test suite",
    )
    board = derive(f)
    view = for_viewer(board, ME.id)
    assert view.attention == "moving"
    assert board.phase == "build"
    assert board.phase_since == at(-6)
    assert track_states(f) == "done done current pending pending pending"
    assert rail_states(f) == "done current pending pending pending"
    assert board.next_stage is None
    assert view.now == now(
        "Run API test suite", actor="Implement", run_id=1, progress=TaskProgress(done=2, total=5)
    )


def test_seeded_aos_4_gated_plan() -> None:
    f = facts(GATED_PLAN, owner=ME, ever=PLAN_OK, tasks=PLAN_TASKS)
    board = derive(f)
    view = for_viewer(board, ME.id)
    assert view.attention == "needs_you"
    assert board.phase == "plan"
    assert board.phase_since == GATED_PLAN.finished_at
    assert track_states(f) == "done current pending skipped pending pending"
    assert rail_states(f) == "current pending pending pending"
    assert board.next_stage == "implement"
    assert view.now == now("review plan and continue", progress=PLAN_TASKS)


def test_seeded_aos_5_failed_review() -> None:
    f = facts(FAILED_REVIEW, ever={"plan", "implement"}, tasks=TaskProgress(done=6, total=6))
    board = derive(f)
    view = for_viewer(board, ME.id)
    assert view.attention == "stuck"
    assert board.phase == "review"
    assert board.phase_since == FAILED_REVIEW.finished_at
    assert track_states(f) == "done done done skipped failed pending"
    assert rail_states(f) == "done done failed pending"
    assert board.next_stage == "review"
    assert view.now == now("Review failed: retry or adjust")


def test_seeded_aos_6_waiting_on_sam_with_stale_stages() -> None:
    tasks = TaskProgress(done=1, total=3)
    f = facts(GATED_PLAN, owner=SAM, ever={"plan", "implement", "review"}, tasks=tasks)
    board = derive(f)
    view = for_viewer(board, ME.id)
    assert view.attention == "waiting"
    assert board.phase == "plan"
    assert track_states(f) == "done current stale skipped stale pending"
    assert rail_states(f) == "current stale stale pending"
    assert board.next_stage == "implement"
    assert view.now == now("review plan and continue", person=SAM, progress=tasks)


def test_seeded_aos_7_done() -> None:
    tasks = TaskProgress(done=3, total=3)
    latest = run(
        "succeeded",
        [("implement", "succeeded"), ("review", "succeeded"), ("open-pr", "succeeded")],
    )
    f = facts(latest, state="done", owner=ME, ever=PLAN_OK, tasks=tasks)
    board = derive(f)
    view = for_viewer(board, ME.id)
    assert view.attention == "done"
    assert board.phase == "done"
    assert board.phase_since == UPDATED
    assert track_states(f) == "done done done skipped done done"
    assert rail_states(f) == "done done done done"
    assert board.next_stage is None
    assert view.now == now("done", progress=tasks)


@pytest.mark.parametrize("owner", [ME, None], ids=["aos-1-owned", "work-11234-unowned"])
def test_seeded_up_next_without_runs(owner: Person | None) -> None:
    f = facts(owner=owner)
    board = derive(f)
    view = for_viewer(board, ME.id)
    assert view.attention == "up_next"
    assert board.phase == "inbox"
    assert board.phase_since == CREATED
    assert track_states(f) == "current pending pending skipped pending pending"
    assert rail_states(f) == "pending pending pending pending"
    assert board.next_stage == "plan"
    assert view.now == now("add context, then dispatch", run_id=None)
