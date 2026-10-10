"""Default template seed and the naming helpers for ticket context (SPEC §4, §15).

Pure: no web, db or http imports (enforced by import-linter).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from agentos.models import FileEntry, Pipeline, Stage, WorkspaceMode

MAX_SKILL_NAME = 64  # Agent Skills limit on `name` (must also match the directory name)
SKILL_PREFIX = "context-"
FALLBACK_SLUG = "ticket"
# Slug budget when no key is given (only the stubs call slugify without one).
_NO_KEY_SLUG_CHARS = 40

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


# `tojson` is agentos' YAML-safe variant (see render.py), so any title keeps the frontmatter
# valid. A title is ≤ 200 chars, so the description stays under the 1024-char skill limit.
DEFAULT_SCAFFOLD = [
    FileEntry(
        path="{{paths.skill_md}}",
        content=(
            "---\nname: {{skill.name}}\ndescription: {{ ('Objective and working context for ' ~ "
            "ticket.key ~ ' (' ~ ticket.title ~ '). Use when working on ' ~ ticket.key ~ '.') "
            "| tojson }}\n---\n# {{ticket.key}}: {{ticket.title}}\n\n"
            "{% if ticket.source_url %}Source: {{ticket.source_url}}\n\n{% endif %}"
            "## Objective\n{{ticket.description}}\n"
        ),
    ),
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


def slugify(title: str, key: str | None = None) -> str:
    """SPEC §4: NFKD → ASCII, lowercase, runs of non-[a-z0-9] → '-', trim, truncate so
    `skill_name(key, slug)` ≤ 64, trim again, empty → 'ticket'.

    `key=None` (stubs only) caps the slug at 40 chars. Real callers pass the key.
    """
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")
    if key is None:
        budget = _NO_KEY_SLUG_CHARS
    else:
        budget = MAX_SKILL_NAME - len(f"{SKILL_PREFIX}{key.lower()}-")
    return slug[: max(budget, 0)].strip("-") or FALLBACK_SLUG


def skill_name(key: str, slug: str) -> str:
    return f"{SKILL_PREFIX}{key.lower()}-{slug}"


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


def repo_name(url: str | None) -> str | None:
    if not url:
        return None
    tail = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return tail.removesuffix(".git") or None
