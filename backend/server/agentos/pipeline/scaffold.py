"""Default template seed and the naming helpers for ticket context (SPEC §4, §15).

Pure: no web, db or http imports (enforced by import-linter).
"""

from __future__ import annotations

from dataclasses import dataclass

from agentos.models import FileEntry, Pipeline, Stage, WorkspaceMode

MAX_SKILL_NAME = 64  # Agent Skills limit on `name` (must also match the directory name)
SKILL_PREFIX = "context-"

PROMPTS = {
    "plan": (
        "You are planning {{ticket.key}}: {{ticket.title}}. Read {{paths.skill_md}} and the "
        "repository. Do not change code. Write a step-by-step plan to {{paths.tasks_md}} as a "
        "markdown checklist (`- [ ] ...`). Append non-obvious findings to {{paths.learnings}}."
    ),
    "implement": (
        "Implement {{ticket.key}}. Objective: {{paths.skill_md}}. Plan: {{paths.tasks_md}}. Work "
        "through unchecked items in order and tick each (`- [x]`) as soon as it is done. Run the "
        "relevant tests. Append learnings to {{paths.learnings}}."
    ),
    "review": (
        "Review the work for {{ticket.key}}{% if ticket.branch %} on this branch against "
        "origin/{{ticket.base_branch}}{% endif %}, checking it against {{paths.skill_md}} and "
        "{{paths.tasks_md}}. Fix clear defects directly. Write remaining findings to "
        "{{paths.skill_dir}}/REVIEW.md."
    ),
    "open-pr": (
        "Open a pull request from {{ticket.branch}} into {{ticket.base_branch}}, or update the "
        "existing one, using the gh CLI. Title: `{{ticket.key}}: {{ticket.title}}`. Body: a "
        "summary of the change plus the completed items from {{paths.tasks_md}}."
    ),
}


def default_pipeline(*, lint: bool = False) -> Pipeline:
    return Pipeline(
        stages=[
            Stage(
                id="plan",
                name="Plan",
                kind="agent",
                phase="plan",
                gate_after=True,
                prompt=PROMPTS["plan"],
            ),
            Stage(
                id="implement",
                name="Implement",
                kind="agent",
                phase="build",
                prompt=PROMPTS["implement"],
            ),
            Stage(
                id="lint",
                name="Lint",
                kind="shell",
                phase="verify",
                enabled=lint,
                command="npm run lint" if lint else "",
            ),
            Stage(
                id="review",
                name="Review",
                kind="agent",
                phase="review",
                prompt=PROMPTS["review"],
            ),
            Stage(
                id="open-pr",
                name="Open PR",
                kind="agent",
                phase="review",
                requires_repo=True,
                prompt=PROMPTS["open-pr"],
            ),
        ]
    )


SKILL_MD_PATH = "{{paths.skill_md}}"

# The description names the ticket by key only (a long title would crowd it); the body keeps the
# title. `tojson` is agentos' YAML-safe variant (see render.py), so the frontmatter stays valid.
SKILL_MD = (
    "---\nname: {{skill.name}}\ndescription: {{ ('Objective and working context for ' ~ "
    "ticket.key ~ '. Use when working on ' ~ ticket.key ~ '.') | tojson }}\n---\n"
    "# {{ticket.key}}: {{ticket.title}}\n\n"
    "{% if ticket.source_url %}Source: {{ticket.source_url}}\n\n{% endif %}"
    "## Objective\n{{ticket.description}}\n"
)

DEFAULT_SCAFFOLD = [
    FileEntry(path=SKILL_MD_PATH, content=SKILL_MD),
    FileEntry(path="{{paths.tasks_md}}", content="# Tasks\n"),
    FileEntry(path="{{paths.learnings}}", content=""),
    FileEntry(path="{{paths.swarm}}", content=""),
    FileEntry(
        path="AGENTS.md",
        mode="block",
        content=(
            "Working on {{ticket.key}}. Read {{paths.skill_md}} first; the plan is "
            "{{paths.tasks_md}}. Do not run git commit or push; AgentOS commits and pushes "
            "after each stage.{% if not ticket.branch %} Put deliverables in "
            "{{paths.out}}/.{% endif %}"
        ),
    ),
    FileEntry(path="CLAUDE.md", mode="block", content="@AGENTS.md"),
]


def skill_name(key: str, slug: str) -> str:
    """`context-{key}` (≤ 64: keys are ≤ MAX_KEY_CHARS). New tickets store an empty slug; tickets
    created while the name carried a title slug keep `context-{key}-{slug}`, because their bundle
    files are stored under that directory."""
    name = f"{SKILL_PREFIX}{key.lower()}"
    return f"{name}-{slug}" if slug else name


def skill_dir(key: str, slug: str) -> str:
    return f".claude/skills/{skill_name(key, slug)}"


@dataclass(frozen=True, slots=True)
class TicketNames:
    """Derived names (SPEC §4). `branch` is None in no-repo mode."""

    skill_name: str
    skill_dir: str
    skill_md: str
    tasks_md: str
    learnings: str
    swarm: str
    out: str
    branch: str | None


def names(key: str, slug: str, workspace_mode: WorkspaceMode) -> TicketNames:
    d = skill_dir(key, slug)
    return TicketNames(
        skill_name=skill_name(key, slug),
        skill_dir=d,
        skill_md=f"{d}/SKILL.md",
        tasks_md=f"{d}/TASKS.md",
        learnings=f"{d}/learnings.log",
        swarm=f"{d}/swarm.log",
        out=f"{d}/out",
        branch=f"agent/{key}" if workspace_mode == "repo" else None,
    )
