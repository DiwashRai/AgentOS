"""Loop blocks (request 24): pipeline validation, the run plan, the manifest's later-round and
repair calls, the runtimes' session/verdict settings, and a loop run through the API (dispatch,
claim, a stage_run per round, verdicts, the rail's round and the failure anchor)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agentos.context import AppContext
from agentos.errors import ApiError
from agentos.models import (
    Loop,
    ManifestInvocation,
    Pipeline,
    RunDetail,
    Stage,
    TemplateDefaults,
    TicketDetail,
)
from agentos.pipeline.manifest import (
    FOLLOW_UP,
    PREVIOUS_ROUND,
    REPAIR_HEAD,
    VERDICT_SCHEMA_JSON,
    render_stages,
    verdict_block,
)
from agentos.pipeline.render import TicketRef
from agentos.pipeline.runtime import BUILTIN_RUNTIMES, RuntimesConfig, RuntimeSpec
from agentos.pipeline.segment import RunPlan, plan_run, validate_pipeline
from tests.run_support import CSRF, RunDriver, Worker, event

CreateTicket = Callable[..., TicketDetail]

CLAUDE = ["claude", "-p"]
COPILOT = ["copilot", "--allow-all"]
PLAIN = ["plain"]
RUNTIMES = RuntimesConfig(
    default_runtime="claude",
    runtimes={
        "claude": RuntimeSpec(
            argv=CLAUDE,
            session_args=["--session-id", "{session}"],
            resume_args=["--resume", "{session}"],
            verdict_args=["--json-schema", "{verdict_schema}"],
            verdict_from="structured_output",
        ),
        "copilot": RuntimeSpec(
            argv=COPILOT,
            prompt_via="arg",
            prompt_args=["-p", "{prompt}"],
            session_args=["--resume={session}"],
            resume_args=["--resume={session}"],
        ),
        "plain": RuntimeSpec(argv=PLAIN),
    },
)
TICKET = TicketRef(
    key="AOS-1",
    title="T",
    description="",
    type="task",
    source_url=None,
    repo_url=None,
    base_branch=None,
    workspace_mode="none",
)
NONE = TemplateDefaults()
SCHEMA = ["--json-schema", VERDICT_SCHEMA_JSON]


def st(stage_id: str, kind: str = "agent", **fields: Any) -> Stage:
    extra = {"command": f"make {stage_id}"} if kind == "shell" else {"prompt": f"Do {stage_id}."}
    return Stage.model_validate(
        {"id": stage_id, "name": stage_id.title(), "kind": kind, "phase": "build"} | extra | fields
    )


def pipe(*stages: Stage, loops: tuple[tuple[str, str, int], ...] = ()) -> Pipeline:
    return Pipeline(
        stages=list(stages), loops=[Loop(start=a, end=b, max_rounds=n) for a, b, n in loops]
    )


def sessions() -> Callable[[], str]:
    counter: Iterator[int] = iter(range(1, 100))
    return lambda: f"s{next(counter)}"


def render(plan: RunPlan, *, runtimes: RuntimesConfig = RUNTIMES) -> list[Any]:
    return render_stages(
        7, TICKET, plan.stages, runtimes, defaults=NONE, loops=plan.loops, new_session=sessions()
    )


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    ("pipeline", "message"),
    [
        (pipe(st("a"), st("b", check=True), loops=(("a", "x", 3),)), "loops.0: no stage 'x'"),
        (
            pipe(st("a", check=True), st("b"), loops=(("b", "a", 3),)),
            "loops.0: b comes after a",
        ),
        (
            pipe(st("a"), st("b"), loops=(("a", "b", 3),)),
            "loops.0: the block needs a check (a shell stage, or an agent stage with check)",
        ),
        (
            pipe(st("a", "shell"), st("b", check=True), loops=(("a", "b", 3),)),
            "loops.0: the block needs an agent stage that isn't a check, to fix what the checks "
            "find",
        ),
        (
            pipe(st("a", gate_after=True), st("b", check=True), loops=(("a", "b", 3),)),
            "stage a: only the last stage of a loop block (b) may have a gate",
        ),
        (
            pipe(
                st("a"),
                st("b", check=True),
                st("c"),
                st("d", check=True),
                loops=(("c", "d", 2), ("a", "c", 2)),
            ),
            "loops.0: overlaps loops.1",
        ),
    ],
)
def test_invalid_loops(pipeline: Pipeline, message: str) -> None:
    with pytest.raises(ApiError) as e:
        validate_pipeline(pipeline, runtimes=RUNTIMES.runtimes.keys())
    assert (e.value.status, e.value.code, e.value.message) == (422, "invalid_pipeline", message)


def test_valid_loops_count_disabled_stages_and_allow_a_gate_on_the_end() -> None:
    validate_pipeline(
        pipe(
            st("a"),
            st("b", check=True, enabled=False),  # disabling the only check on a ticket is fine
            st("c", gate_after=True),
            st("d"),
            st("e", "shell"),
            loops=(("a", "c", 3), ("d", "e", 5)),
        ),
        runtimes=RUNTIMES.runtimes.keys(),
    )


@pytest.mark.parametrize("rounds", [0, 6])
def test_max_rounds_is_one_to_five(rounds: int) -> None:
    with pytest.raises(ValidationError):
        Loop(start="a", end="b", max_rounds=rounds)


def test_loop_defaults() -> None:
    assert Loop(start="a", end="b").max_rounds == 3
    s = st("a")
    assert (s.check, s.verdict_repairs, s.resume) == (False, 2, None)


# ------------------------------------------------------------------ the run plan

LOOPED = pipe(
    st("plan", gate_after=True),
    st("implement"),
    st("lint", "shell"),
    st("review", check=True, gate_after=True),
    st("open-pr"),
    loops=(("implement", "review", 3),),
)


def ids(plan: RunPlan) -> list[str]:
    return [s.id for s in plan.stages]


def test_a_plan_from_the_block_start_runs_to_the_gate_after_the_block() -> None:
    plan = plan_run(LOOPED, "implement")
    assert ids(plan) == ["implement", "lint", "review"]
    assert plan.start == "implement"
    assert plan.loops == (Loop(start="implement", end="review", max_rounds=3),)
    assert plan.warnings == ()


def test_a_plan_from_inside_a_block_adds_the_block_start() -> None:
    plan = plan_run(LOOPED, "review")
    assert ids(plan) == ["implement", "lint", "review"]
    assert plan.start == "review"
    assert plan.loops == (Loop(start="implement", end="review", max_rounds=3),)


def test_a_plan_before_the_block_and_past_it() -> None:
    assert ids(plan_run(LOOPED, "plan")) == ["plan"]
    after = plan_run(LOOPED, "open-pr")
    assert (ids(after), after.loops) == (["open-pr"], ())


def test_a_block_left_without_an_enabled_check_or_maker_runs_once() -> None:
    no_check = LOOPED.model_copy(deep=True)
    no_check.stages[3].enabled = False  # review; lint is a check too
    no_check.stages[2].enabled = False
    plan = plan_run(no_check, "implement")
    assert (ids(plan), plan.loops) == (["implement", "open-pr"], ())
    assert plan.warnings == ("The loop implement → review runs once: none of its enabled stages "
                             "is a check",)  # fmt: skip
    no_maker = LOOPED.model_copy(deep=True)
    no_maker.stages[1].enabled = False
    plan = plan_run(no_maker, "lint")
    assert (ids(plan), plan.loops) == (["lint", "review"], ())
    assert plan.warnings == (
        "The loop implement → review runs once: its enabled stages are all checks, none fixes",
    )


def test_disabled_stages_drop_out_of_a_block() -> None:
    p = LOOPED.model_copy(deep=True)
    p.stages[1].enabled = False  # implement: the block's maker
    p.stages.insert(2, st("fix"))
    plan = plan_run(p, "fix")
    assert plan.loops == (Loop(start="fix", end="review", max_rounds=3),)


def test_the_time_budget_counts_every_round_and_repair() -> None:
    p = pipe(
        st("implement", timeout_s=100),
        st("lint", "shell", timeout_s=60),
        st("review", check=True, timeout_s=200, verdict_repairs=1),
        st("open-pr", timeout_s=70),
        loops=(("implement", "review", 2),),
    )
    # 2 rounds x (100 + 60 + 200 x (1 + 1 repair)) + 70
    assert plan_run(p, "implement").time_budget_s() == 2 * (100 + 60 + 400) + 70


# ------------------------------------------------------------------ rendering


def loop_of(*stages: Stage, rounds: int = 2) -> RunPlan:
    return plan_run(pipe(*stages, loops=((stages[0].id, stages[-1].id, rounds),)), stages[0].id)


def test_render_with_sessions_and_structured_output() -> None:
    implement, review = render(loop_of(st("implement"), st("review", check=True)))
    assert (implement.argv, implement.stdin) == ([*CLAUDE, "--session-id", "s1"], "Do implement.")
    assert implement.rounds == [
        ManifestInvocation(
            argv=[*CLAUDE, "--resume", "s1"],
            stdin=f"Round 2 of 2. {FOLLOW_UP}\n\n",
            prompt_arg=None,
            tail="",
        )
    ]
    assert (implement.check, implement.repairs, implement.verdict_from) == (False, [], None)

    block = verdict_block("structured_output")
    assert "give the verdict as your final answer" in block
    assert review.argv == [*CLAUDE, "--session-id", "s2", *SCHEMA]
    assert review.stdin == f"Do review.\n\n{block}"
    assert review.rounds == [
        ManifestInvocation(
            argv=[*CLAUDE, "--session-id", "s3", *SCHEMA],  # checks start fresh each round
            stdin=f"Do review.\n\n{PREVIOUS_ROUND}",
            prompt_arg=None,
            tail=f"\n\n{block}",
        )
    ]
    assert [(r.argv, r.stdin) for r in review.repairs] == [
        ([*CLAUDE, "--resume", "s2", *SCHEMA], REPAIR_HEAD),  # each round's own session
        ([*CLAUDE, "--resume", "s3", *SCHEMA], REPAIR_HEAD),
    ]
    assert review.repairs[0].tail.startswith(".\nDon't redo the review. Answer only with")
    assert (review.check, review.verdict_from, review.verdict_repairs) == (
        True,
        "structured_output",
        2,
    )


def test_render_a_prompt_argument_runtime_with_the_file_channel() -> None:
    implement, review = render(
        loop_of(st("implement", runtime="copilot"), st("review", check=True, runtime="copilot"))
    )
    assert implement.argv == [*COPILOT, "--resume=s1", "-p", "Do implement."]
    assert implement.rounds[0].argv == [
        *COPILOT,
        "--resume=s1",
        "-p",
        f"Round 2 of 2. {FOLLOW_UP}\n\n",
    ]
    assert (implement.rounds[0].stdin, implement.rounds[0].prompt_arg) == (None, 4)
    block = verdict_block("file")
    assert "`.agentos/verdict.json`" in block
    assert review.argv == [*COPILOT, "--resume=s2", "-p", f"Do review.\n\n{block}"]
    assert review.rounds[0].argv[-1] == f"Do review.\n\n{PREVIOUS_ROUND}"
    assert (review.rounds[0].prompt_arg, review.rounds[0].tail) == (4, f"\n\n{block}")
    assert review.repairs[1].argv == [*COPILOT, "--resume=s3", "-p", REPAIR_HEAD]
    assert "Write only the verdict to `.agentos/verdict.json`" in review.repairs[1].tail
    assert review.verdict_from == "file"


def test_render_without_sessions_starts_fresh_and_never_repairs() -> None:
    implement, review = render(
        loop_of(
            st("implement", runtime="plain"), st("review", check=True, runtime="plain"), rounds=3
        )
    )
    assert implement.argv == PLAIN
    assert [r.stdin for r in implement.rounds] == [
        f"Do implement.\n\n## Round {n} of 3\n{FOLLOW_UP}\n\n" for n in (2, 3)
    ]
    assert all(r.argv == PLAIN for r in [*implement.rounds, *review.rounds])
    assert (review.repairs, review.verdict_from) == ([], "file")


def test_render_resume_overrides_the_role_default() -> None:
    implement, review = render(
        loop_of(st("implement", resume=False), st("review", check=True, resume=True))
    )
    assert implement.argv == CLAUDE
    assert implement.rounds[0].stdin == f"Do implement.\n\n## Round 2 of 2\n{FOLLOW_UP}\n\n"
    assert review.argv == [*CLAUDE, "--session-id", "s1", *SCHEMA]
    assert review.rounds[0].argv == [*CLAUDE, "--resume", "s1", *SCHEMA]
    assert review.rounds[0].stdin is not None
    assert review.rounds[0].stdin.startswith("Round 2 of 2: the makers worked on the findings.")
    assert [r.argv[-3] for r in review.repairs] == ["s1", "s1"]


def test_render_no_repairs_and_shell_checks() -> None:
    implement, lint, review = render(
        loop_of(st("implement"), st("lint", "shell"), st("review", check=True, verdict_repairs=0))
    )
    assert (review.repairs, review.verdict_repairs) == ([], 0)
    assert (lint.check, lint.command, lint.rounds) == (True, "make lint", [])
    assert not implement.check


def test_stages_outside_a_loop_render_as_before() -> None:
    (stage,) = render(RunPlan((st("review", check=True),), "review"))
    assert (stage.argv, stage.stdin, stage.check, stage.rounds) == (CLAUDE, "Do review.", False, [])


# ------------------------------------------------------------------ runtimes


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"prompt_via": "arg", "prompt_args": ["{prompt}", "{prompt}"]}, '"{prompt}" once'),
        ({"session_args": ["--session-id"]}, 'session_args must contain "{session}"'),
        ({"resume_args": ["--resume"]}, 'resume_args must contain "{session}"'),
        ({"verdict_from": "structured_output"}, "needs verdict_args"),
        ({"verdict_args": ["--x\x00"]}, "NUL"),
    ],
)
def test_runtime_loop_settings_are_checked(fields: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        RuntimeSpec.model_validate({"argv": ["x"], **fields})


def test_built_in_runtimes_support_sessions_and_verdicts() -> None:
    claude, copilot = BUILTIN_RUNTIMES
    assert (claude.session_args, claude.resume_args) == (
        ["--session-id", "{session}"],
        ["--resume", "{session}"],
    )
    assert (claude.verdict_args, claude.verdict_from) == (
        ["--json-schema", "{verdict_schema}"],
        "structured_output",
    )
    assert (copilot.session_args, copilot.resume_args) == (
        ["--resume={session}"],
        ["--resume={session}"],
    )
    assert (copilot.verdict_args, copilot.verdict_from) == ([], "file")
    assert claude.sessions
    assert copilot.sessions
    assert not RuntimeSpec(argv=["x"]).sessions


# ------------------------------------------------------------------ through the API
# The test Space's server default runtime ("fake") has no sessions: no repairs, makers start fresh.


def put_pipeline(client: TestClient, key: str, edit: Callable[[dict[str, Any]], None]) -> Any:
    t = client.get(f"/api/tickets/{key}").json()
    pipeline = t["pipeline"]
    edit(pipeline)
    return client.put(
        f"/api/tickets/{key}/pipeline",
        json={"pipeline": pipeline, "base_version": t["pipeline_version"]},
        headers=CSRF,
    )


def looping(pipeline: dict[str, Any], rounds: int = 3) -> None:
    """Default pipeline: implement → (lint: disabled) → review (check) loop, then open-pr."""
    review = next(s for s in pipeline["stages"] if s["id"] == "review")
    review["check"] = True
    pipeline["loops"] = [{"start": "implement", "end": "review", "max_rounds": rounds}]


@pytest.fixture
def ticket(authed: TestClient, create_ticket: CreateTicket) -> TicketDetail:
    t = create_ticket(repo=None)  # no-repo: open-pr is disabled
    r = put_pipeline(authed, t.key, looping)
    assert r.status_code == 200, r.text
    detail = TicketDetail.model_validate(r.json())
    assert [lp.model_dump() for lp in detail.pipeline.loops] == [
        {"start": "implement", "end": "review", "max_rounds": 3}
    ]
    return detail


def test_a_bad_loop_is_refused_on_save(authed: TestClient, create_ticket: CreateTicket) -> None:
    t = create_ticket()

    def edit(pipeline: dict[str, Any]) -> None:
        pipeline["loops"] = [{"start": "review", "end": "open-pr"}]  # two makers, no check

    r = put_pipeline(authed, t.key, edit)
    assert (r.status_code, r.json()["error"]["code"]) == (422, "invalid_pipeline")
    assert "the block needs a check" in r.json()["error"]["message"]


def test_dispatch_and_claim_a_loop(ticket: TicketDetail, runs: RunDriver) -> None:
    run = runs.queue(ticket.key, "implement")
    assert run.stage_ids == ["implement", "review"]
    # 3 rounds of implement + 3 rounds x (1 + 2 repairs) of review, at 3600 s each
    assert runs.db_run(run.id)["time_budget_s"] == (3 + 9) * 3600
    worker = runs.launch_and_bind(run.id)
    m = worker.manifest
    assert [lp.model_dump() for lp in m.loops] == [
        {"start": "implement", "end": "review", "max_rounds": 3}
    ]
    assert (m.start_stage, m.warnings) == ("implement", [])
    implement, review = m.stages
    assert (len(implement.rounds), implement.check) == (2, False)
    assert (review.check, review.verdict_from, review.repairs) == (True, "file", [])
    assert review.stdin is not None
    assert review.stdin.endswith(verdict_block("file"))


def test_a_run_from_inside_the_block_plans_round_one_from_there(
    ticket: TicketDetail, runs: RunDriver
) -> None:
    run = runs.queue(ticket.key, "review")
    assert (run.from_stage, run.stage_ids) == ("review", ["implement", "review"])
    assert [(s.stage_id, s.idx, s.round) for s in runs.get(run.id).stage_runs] == [("review", 0, 1)]
    assert runs.launch_and_bind(run.id).manifest.start_stage == "review"


def test_a_block_without_an_enabled_check_runs_once_with_a_warning(
    authed: TestClient, ticket: TicketDetail, runs: RunDriver
) -> None:
    def disable_review(pipeline: dict[str, Any]) -> None:
        next(s for s in pipeline["stages"] if s["id"] == "review")["enabled"] = False

    assert put_pipeline(authed, ticket.key, disable_review).status_code == 200
    m = runs.start(ticket.key, "implement").manifest
    assert (m.loops, [s.id for s in m.stages]) == ([], ["implement"])
    assert m.warnings == [
        "The loop implement → review runs once: none of its enabled stages is a check"
    ]


def started(w: Worker, stage_id: str, round_: int) -> dict[str, Any]:
    return event(w.next_seq(), "stage_started", stage_id, round=round_)


def finished(w: Worker, stage_id: str, round_: int, status: str = "succeeded") -> Any:
    return event(
        w.next_seq(),
        "stage_finished",
        stage_id,
        status=status,
        exit_code=0,
        reason=None if status == "succeeded" else "verdict: fail in the last round (3 of 3)",
        round=round_,
    )


def verdict(w: Worker, stage_id: str, round_: int, value: str, notes: str = "") -> Any:
    data = {"round": round_, "verdict": value, "notes": notes, "source": "file", "repairs": 0}
    return event(w.next_seq(), "verdict", stage_id, **data)


def rows(runs: RunDriver, run_id: int) -> list[tuple[str, int, int, str, str | None]]:
    detail: RunDetail = runs.get(run_id)
    return [(s.stage_id, s.idx, s.round, s.status, s.verdict) for s in detail.stage_runs]


def test_each_round_gets_its_own_stage_runs_in_execution_order(
    authed: TestClient, create_ticket: CreateTicket, runs: RunDriver
) -> None:
    t = create_ticket()  # with a repo: open-pr follows the block

    assert put_pipeline(authed, t.key, looping).status_code == 200
    w = runs.start(t.key, "implement")
    assert [s.id for s in w.manifest.stages] == ["implement", "review", "open-pr"]
    first_round = [
        started(w, "implement", 1),
        finished(w, "implement", 1),
        started(w, "review", 1),
        verdict(w, "review", 1, "fail", "Add a test."),
        finished(w, "review", 1),
        started(w, "implement", 2),
    ]
    assert w.events(*first_round).status_code == 200
    assert w.events(*first_round).status_code == 200  # a resent batch adds nothing
    assert rows(runs, w.run_id) == [
        ("implement", 0, 1, "succeeded", None),
        ("review", 1, 1, "succeeded", "fail"),
        ("implement", 2, 2, "running", None),  # inserted before open-pr's planned row
        ("open-pr", 3, 1, "pending", None),
    ]
    rail = {s.stage_id: (s.state, s.round, s.max_rounds) for s in ticket_rail(authed, t.key)}
    assert rail["implement"] == ("current", 2, 3)
    assert rail["review"] == ("done", 1, 3)
    assert rail["open-pr"] == ("pending", None, None)

    w.events(
        finished(w, "implement", 2),
        started(w, "review", 2),
        verdict(w, "review", 2, "pass"),
        finished(w, "review", 2),
        started(w, "open-pr", 1),
        finished(w, "open-pr", 1),
    )
    assert rows(runs, w.run_id)[3:] == [
        ("review", 3, 2, "succeeded", "pass"),
        ("open-pr", 4, 1, "succeeded", None),
    ]
    events = runs.client.get(f"/api/runs/{w.run_id}/events").json()
    assert events[3] == {
        "seq": 5,  # after the `started` record's
        "ts": events[3]["ts"],
        "stage_id": "review",
        "type": "verdict",
        "data": {
            "round": 1,
            "verdict": "fail",
            "notes": "Add a test.",
            "source": "file",
            "repairs": 0,
        },
    }


def test_a_stage_outside_the_run_moves_no_stage_run(ticket: TicketDetail, runs: RunDriver) -> None:
    w = runs.start(ticket.key, "implement")  # no repo: open-pr is disabled, not in the run
    assert w.events(started(w, "open-pr", 2)).status_code == 200
    assert rows(runs, w.run_id) == [
        ("implement", 0, 1, "pending", None),
        ("review", 1, 1, "pending", None),
    ]


def ticket_rail(client: TestClient, key: str) -> list[Any]:
    return TicketDetail.model_validate(client.get(f"/api/tickets/{key}").json()).rail


def test_a_lost_loop_blocks_the_ticket_at_its_failing_check(
    authed: TestClient, ticket: TicketDetail, runs: RunDriver
) -> None:
    w = runs.start(ticket.key, "implement")
    batch: list[Any] = []
    for n in (1, 2, 3):
        batch += [started(w, "implement", n), finished(w, "implement", n), started(w, "review", n)]
        batch += [verdict(w, "review", n, "fail", f"Still broken ({n}).")]
        batch += [finished(w, "review", n, "succeeded" if n < 3 else "failed")]
    assert w.events(*batch).status_code == 200
    error = "Checks still failing after 3 rounds"
    assert w.complete("failed", failed_stage="review", error=error).status_code == 200
    run = runs.get(w.run_id)
    assert (run.status, run.error) == ("failed", error)
    assert [(s.round, s.status) for s in run.stage_runs if s.stage_id == "review"] == [
        (1, "succeeded"),
        (2, "succeeded"),
        (3, "failed"),
    ]
    detail = TicketDetail.model_validate(authed.get(f"/api/tickets/{ticket.key}").json())
    assert (detail.attention, detail.next_stage) == ("blocked", "review")  # Retry: the check
    assert detail.now.text == "Review failed: retry or adjust"
    review = next(s for s in detail.rail if s.stage_id == "review")
    assert (review.state, review.round, review.max_rounds) == ("failed", 3, 3)


def test_dispatch_fails_a_run_whose_pipeline_changed(
    ctx: AppContext, ticket: TicketDetail, runs: RunDriver
) -> None:
    run = runs.queue(ticket.key, "review")
    with ctx.db.tx() as conn:  # behind the API's back: the pipeline is locked during a run
        row = conn.execute("SELECT pipeline FROM tickets WHERE key = ?", (ticket.key,)).fetchone()
        pipeline = json.loads(row["pipeline"])
        pipeline["loops"] = []
        conn.execute(
            "UPDATE tickets SET pipeline = ? WHERE key = ?", (json.dumps(pipeline), ticket.key)
        )
    assert run.id in runs.tick()
    assert runs.launcher.launched == []
    detail = runs.get(run.id)
    assert detail.status == "failed"
    assert detail.error == (
        "cannot build the manifest: the pipeline changed since the run was dispatched"
    )
