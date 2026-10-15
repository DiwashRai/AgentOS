"""Runtime resolution and manifest building (SPEC §7)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agentos.errors import ApiError
from agentos.models import FileEntry, Manifest, Stage, TemplateDefaults
from agentos.pipeline.manifest import build_manifest, render_stages, resolve_agent
from agentos.pipeline.render import TicketRef, render_scaffold
from agentos.pipeline.runtime import RuntimesConfig, RuntimeSpec
from agentos.pipeline.scaffold import DEFAULT_SCAFFOLD, default_pipeline
from agentos.pipeline.segment import plan_run
from agentos.validate import MAX_AGENT_LINE_BYTES, MAX_FILE_BYTES

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
    slug="fix-login-bug",
    title="Fix login bug: session expires early",
    description="Sessions expire after 5 minutes instead of 14 days.",
    type="bug",
    source_url=None,
    repo_url="https://github.com/example/app.git",
    base_branch="main",
    workspace_mode="repo",
)
D = ".claude/skills/context-aos-42-fix-login-bug"
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


def test_build_manifest_default_ticket() -> None:
    p = default_pipeline()
    m = build_manifest(42, TICKET, plan_run(p, "implement"), _bundle(), RUNTIMES, defaults=NONE)
    assert Manifest.model_validate_json(m.model_dump_json()) == m
    assert m.protocol == 1
    assert m.run_id == 42
    assert m.ticket.model_dump() == {
        "key": "AOS-42",
        "slug": "fix-login-bug",
        "title": TICKET.title,
    }
    assert m.workspace.model_dump() == {
        "mode": "repo",
        "repo_url": "https://github.com/example/app.git",
        "base_branch": "main",
        "branch": "agent/AOS-42",
    }
    assert [(f.path, f.mode, f.sync) for f in m.files] == [
        (f"{D}/SKILL.md", "replace", True),
        (f"{D}/TASKS.md", "replace", True),
        (f"{D}/learnings.log", "replace", True),
        (f"{D}/swarm.log", "replace", True),
        ("AGENTS.md", "block", True),
        ("CLAUDE.md", "block", True),
    ]
    assert m.sync_dirs == [D]
    assert [s.id for s in m.stages] == ["implement", "review", "open-pr"]
    assert all(s.argv and s.argv[0] == "claude" and s.stdin for s in m.stages)
    assert m.stages[2].stdin is not None
    assert "from agent/AOS-42 into main" in m.stages[2].stdin
    assert m.limits.model_dump() == {
        "max_file_bytes": MAX_FILE_BYTES,
        "max_line_bytes": MAX_AGENT_LINE_BYTES,
    }


def test_build_manifest_no_repo_nulls_workspace() -> None:
    t = replace(TICKET, workspace_mode="none")
    m = build_manifest(
        1, t, plan_run(default_pipeline(), "plan"), _bundle(t), RUNTIMES, defaults=NONE
    )
    assert m.workspace.model_dump() == {
        "mode": "none",
        "repo_url": None,
        "base_branch": None,
        "branch": None,
    }
    agents = next(f for f in m.files if f.path == "AGENTS.md")
    assert agents.content.endswith(f"Put deliverables in {D}/out/.")


def test_fixture_shape_matches_builder() -> None:
    """The builder emits every field the shared fixture has (same keys at every level)."""
    fixture = Manifest.model_validate_json((FIXTURES / "manifest_v1.json").read_text("utf-8"))
    # The fixture exercises both file modes and both stage kinds in the worker suite too.
    assert {f.mode for f in fixture.files} == {"replace", "block"}
    assert {s.kind for s in fixture.stages} == {"agent", "shell"}
    built = build_manifest(
        42, TICKET, plan_run(default_pipeline(), "implement"), _bundle(), RUNTIMES, defaults=NONE
    )
    assert set(built.model_dump()) == set(fixture.model_dump())
    assert built.ticket == fixture.ticket
    assert built.workspace == fixture.workspace
    assert built.sync_dirs == fixture.sync_dirs
