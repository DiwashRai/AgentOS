"""Runtime resolution and job package building (SPEC §7)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentos.errors import ApiError
from agentos.models import (
    ContextEntry,
    FileEntry,
    JobPackage,
    ManifestJob,
    ManifestJournal,
    ManifestReport,
    Mode,
    NoteEntry,
    Stage,
    TemplateDefaults,
)
from agentos.pipeline.manifest import build_package, classify, render_stages, resolve_agent
from agentos.pipeline.render import TicketRef, render_scaffold
from agentos.pipeline.runtime import RuntimesConfig, RuntimeSpec
from agentos.pipeline.scaffold import DEFAULT_SCAFFOLD, default_pipeline
from agentos.pipeline.segment import RunPlan, plan_run
from agentos.validate import (
    MAX_AGENT_LINE_BYTES,
    MAX_FILE_BYTES,
    MAX_SYNC_BACK_BYTES,
    SYNC_BACK_DIR,
)

FIXTURES = Path(__file__).parent / "fixtures"
CLAUDE_ARGV = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
RUNTIMES = RuntimesConfig(
    default_runtime="claude",
    runtimes={
        "claude": RuntimeSpec(
            argv=CLAUDE_ARGV,
            model_args=["--model", "{model}"],
            agent_args=["--agent", "{agent}"],
        ),
        "copilot": RuntimeSpec(
            argv=["copilot", "--allow-all"],
            model_args=["--model={model}", "{model}"],
            agent_args=["--agent", "{agent}"],
            prompt_via="arg",
            prompt_args=["-p", "{prompt}"],
        ),
    },
)
TICKET = TicketRef(
    key="AOS-42",
    title="Fix login bug: session expires early",
    description="Sessions expire after 5 minutes instead of 14 days.",
    type="bug",
    source_url=None,
    repo_url="https://github.com/example/app.git",
    base_branch="main",
    workspace_mode="repo",
)
D = ".claude/skills/context-aos-42"
NONE = TemplateDefaults()  # no Space defaults: the config's default_runtime, no model


def agent(
    *, runtime: str | None = None, model: str | None = None, agent_name: str | None = None
) -> Stage:
    return Stage(
        id="implement",
        name="Implement",
        kind="agent",
        phase="build",
        runtime=runtime,
        model=model,
        agent=agent_name,
        prompt="Implement {{ticket.key}}.",
    )


@pytest.mark.parametrize(
    ("stage", "argv", "stdin"),
    [
        (agent(), CLAUDE_ARGV, "P"),
        (agent(model="sonnet"), [*CLAUDE_ARGV, "--model", "sonnet"], "P"),
        (agent(agent_name="reviewer"), [*CLAUDE_ARGV, "--agent", "reviewer"], "P"),
        (
            agent(model="opus", agent_name="x/y"),
            [*CLAUDE_ARGV, "--model", "opus", "--agent", "x/y"],
            "P",
        ),
        (agent(runtime="copilot"), ["copilot", "--allow-all", "-p", "P"], None),
        (
            # whole-element substitution only: "--model={model}" stays literal
            agent(runtime="copilot", model="gpt", agent_name="a"),
            ["copilot", "--allow-all", "--model={model}", "gpt", "--agent", "a", "-p", "P"],
            None,
        ),
    ],
)
def test_resolve_agent(stage: Stage, argv: list[str], stdin: str | None) -> None:
    assert resolve_agent(stage, "P", RUNTIMES, defaults=NONE) == (argv, stdin)


def test_resolve_agent_prompt_is_one_element() -> None:
    prompt = 'multi\nline {model} "quoted" & | prompt'
    argv, stdin = resolve_agent(
        agent(runtime="copilot", model="m"), prompt, RUNTIMES, defaults=NONE
    )
    assert argv[-2:] == ["-p", prompt]  # the whole prompt, unsplit and unsubstituted
    assert stdin is None


def test_resolve_agent_unknown_runtime() -> None:
    with pytest.raises(ApiError) as e:
        resolve_agent(agent(runtime="gemini"), "P", RUNTIMES, defaults=NONE)
    assert (e.value.status, e.value.code) == (422, "invalid_stage")
    assert e.value.message == "stage implement: unknown runtime 'gemini'"


@pytest.mark.parametrize(
    ("stage", "defaults", "argv", "stdin"),
    [
        # a null model takes the Space default; the stage's own model wins
        (agent(), TemplateDefaults(model="haiku"), [*CLAUDE_ARGV, "--model", "haiku"], "P"),
        (
            agent(model="opus"),
            TemplateDefaults(model="haiku"),
            [*CLAUDE_ARGV, "--model", "opus"],
            "P",
        ),
        # a null runtime takes the Space default runtime; the stage's own runtime wins
        (agent(), TemplateDefaults(runtime="copilot"), ["copilot", "--allow-all", "-p", "P"], None),
        (agent(runtime="claude"), TemplateDefaults(runtime="copilot"), CLAUDE_ARGV, "P"),
        # the Space model belongs to the Space runtime: a stage on another runtime doesn't get it
        (
            agent(runtime="copilot"),
            TemplateDefaults(model="haiku"),
            ["copilot", "--allow-all", "-p", "P"],
            None,
        ),
        (
            agent(runtime="claude"),
            TemplateDefaults(model="haiku"),
            [*CLAUDE_ARGV, "--model", "haiku"],
            "P",
        ),
        (
            agent(),
            TemplateDefaults(runtime="copilot", model="gpt"),
            ["copilot", "--allow-all", "--model={model}", "gpt", "-p", "P"],
            None,
        ),
    ],
)
def test_resolve_agent_space_defaults(
    stage: Stage, defaults: TemplateDefaults, argv: list[str], stdin: str | None
) -> None:
    assert resolve_agent(stage, "P", RUNTIMES, defaults=defaults) == (argv, stdin)


def test_resolve_agent_unknown_space_default_runtime() -> None:
    """A Space default runtime that left the config fails like a stage's own would."""
    with pytest.raises(ApiError) as e:
        resolve_agent(agent(), "P", RUNTIMES, defaults=TemplateDefaults(runtime="gone"))
    assert (e.value.status, e.value.code) == (422, "invalid_stage")


def test_render_stages_shell_and_agent() -> None:
    lint = Stage(
        id="lint", name="Lint", kind="shell", phase="verify", command="make lint", timeout_s=600
    )
    stages = render_stages(5, TICKET, [agent(model="sonnet"), lint], RUNTIMES, defaults=NONE)
    outside_loops = {
        "check": False,
        "rounds": [],
        "repairs": [],
        "verdict_from": None,
        "verdict_repairs": 0,
    }
    assert [s.model_dump() for s in stages] == [
        {
            "id": "implement",
            "name": "Implement",
            "kind": "agent",
            "timeout_s": 3600,
            "argv": [*CLAUDE_ARGV, "--model", "sonnet"],
            "stdin": "Implement AOS-42.",
            "command": None,
            **outside_loops,
        },
        {
            "id": "lint",
            "name": "Lint",
            "kind": "shell",
            "timeout_s": 600,
            "argv": None,
            "stdin": None,
            "command": "make lint",
            **outside_loops,
        },
    ]


def test_render_stages_render_error_names_stage() -> None:
    bad = agent().model_copy(update={"prompt": "{{ run.nope }}"})
    with pytest.raises(ApiError) as e:
        render_stages(1, TICKET, [bad], RUNTIMES, defaults=NONE)
    assert e.value.code == "render_error"
    assert e.value.message.startswith("stage implement: ")


def _bundle(ticket: TicketRef = TICKET) -> list[FileEntry]:
    return render_scaffold(DEFAULT_SCAFFOLD, ticket)


def _build(run_id: int, ticket: TicketRef, plan: RunPlan) -> JobPackage:
    job = ManifestJob(
        id=f"{run_id}-1-0123456789abcdef",
        run_id=run_id,
        segment=1,
        segments=1,
        budget_s=plan.time_budget_s(),
        not_after=datetime(2026, 10, 16, tzinfo=UTC),
    )
    return build_package(
        run_id,
        ticket,
        plan,
        _bundle(ticket),
        RUNTIMES,
        defaults=NONE,
        job=job,
        journal=ManifestJournal(first_seq=1),
        report=ManifestReport(mode="live", url="http://127.0.0.1:8000"),
    )


def test_build_package_default_ticket() -> None:
    m = _build(42, TICKET, plan_run(default_pipeline(), "implement"))
    assert JobPackage.model_validate_json(m.model_dump_json()) == m
    assert (m.protocol, m.run_id) == (2, 42)
    assert m.ticket.model_dump() == {"key": "AOS-42", "title": TICKET.title}
    assert m.workspace.model_dump() == {
        "mode": "repo",
        "repo_url": "https://github.com/example/app.git",
        "base_branch": "main",
        "branch": "agent/AOS-42",
        "protect": [D],
        "blocks": ["AOS-42"],
    }
    assert [(e.path, e.kind, e.mode) for e in m.context] == [
        (f"{D}/SKILL.md", "skill", "replace"),
        (f"{D}/TASKS.md", "plan", "replace"),
        ("AGENTS.md", "instructions", "block"),
        ("CLAUDE.md", "instructions", "block"),
    ]
    assert [n.path for n in m.notes] == [f"{D}/learnings.log", f"{D}/swarm.log"]
    assert m.sync_back.model_dump() == {"dir": SYNC_BACK_DIR, "max_bytes": MAX_SYNC_BACK_BYTES}
    assert m.env == {"AGENTOS_SKILL_DIR": D, "AGENTOS_SYNC_BACK": SYNC_BACK_DIR}
    assert [s.id for s in m.stages] == ["implement", "review", "open-pr"]
    assert all(s.argv and s.argv[0] == "claude" and s.stdin for s in m.stages)
    assert m.stages[2].stdin is not None
    assert "from agent/AOS-42 into main" in m.stages[2].stdin
    assert m.limits.model_dump() == {
        "max_file_bytes": MAX_FILE_BYTES,
        "max_line_bytes": MAX_AGENT_LINE_BYTES,
    }


def test_build_package_no_repo_nulls_workspace() -> None:
    t = replace(TICKET, workspace_mode="none")
    m = _build(1, t, plan_run(default_pipeline(), "plan"))
    ws = m.workspace
    assert (ws.mode, ws.repo_url, ws.base_branch, ws.branch) == ("none", None, None, None)
    agents = next(e for e in m.context if e.path == "AGENTS.md")
    assert agents.content.endswith(" in .agentos/sync_back/.")


@pytest.mark.parametrize(
    ("path", "mode", "kind", "name", "block"),
    [
        (f"{D}/learnings.log", "replace", "note", "learnings", None),
        (f"{D}/swarm.log", "replace", "note", "swarm", None),
        (f"{D}/TASKS.md", "replace", "plan", "TASKS", None),
        (".claude/skills/CONTEXT-AOS-42/tasks.md", "replace", "plan", "tasks", None),
        (f"{D}/SKILL.md", "replace", "skill", "context-aos-42", None),
        (f"{D}/refs/api.md", "replace", "skill", "context-aos-42", None),
        (".claude/skills/review/SKILL.md", "replace", "skill", "review", None),
        ("AGENTS.md", "block", "instructions", "AOS-42", "AOS-42"),
        ("docs/AGENTS.md", "block", "instructions", "AOS-42", "AOS-42"),
        (".claude/agents/reviewer.md", "replace", "agent", "reviewer", None),
        (f"{D}/out/report.md", "replace", "file", "report.md", None),
        (".claude/skills/loose.md", "replace", "file", "loose.md", None),
        ("docs/notes.txt", "replace", "file", "notes.txt", None),
        ("learnings.log", "replace", "file", "learnings.log", None),
    ],
)
def test_classify(path: str, mode: Mode, kind: str, name: str, block: str | None) -> None:
    e = classify(FileEntry(path=path, content="x", mode=mode), "AOS-42")
    if kind == "note":
        assert e == NoteEntry(path=path, name=name, content="x")
        return
    assert isinstance(e, ContextEntry)
    assert (e.kind, e.name, e.mode, e.block, e.access) == (kind, name, mode, block, "write")


def test_fixture_shape_matches_builder() -> None:
    """The builder emits every field the shared fixture has (same keys at every level)."""
    fixture = JobPackage.model_validate_json((FIXTURES / "job_v2.json").read_text("utf-8"))
    # The fixture exercises both file modes and both stage kinds in the worker suite too.
    assert {e.mode for e in fixture.context} == {"replace", "block"}
    assert {s.kind for s in fixture.stages} == {"agent", "shell"}
    built = _build(42, TICKET, plan_run(default_pipeline(), "implement"))
    assert set(built.model_dump()) == set(fixture.model_dump())
    assert set(built.job.model_dump()) == set(fixture.job.model_dump())
    assert set(built.report.model_dump()) == set(fixture.report.model_dump())
    assert set(built.context[0].model_dump()) == set(fixture.context[0].model_dump())
    assert set(built.notes[0].model_dump()) == set(fixture.notes[0].model_dump())
    assert built.ticket == fixture.ticket
    assert built.workspace == fixture.workspace
    assert (built.sync_back, built.env) == (fixture.sync_back, fixture.env)
