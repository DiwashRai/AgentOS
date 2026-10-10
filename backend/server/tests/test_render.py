"""Sandboxed rendering of prompts and the scaffold (SPEC §7 "Render", §15)."""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from agentos.errors import ApiError
from agentos.models import FileEntry, Stage
from agentos.pipeline.render import (
    TicketRef,
    check_scaffold,
    render_context,
    render_prompt,
    render_scaffold,
    yaml_safe_json,
)
from agentos.pipeline.scaffold import DEFAULT_SCAFFOLD, PROMPTS, slugify

D = ".claude/skills/context-aos-42-fix-login-bug"

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
NO_REPO = replace(TICKET, workspace_mode="none", repo_url=None, base_branch=None)


def _stage(prompt: str, sid: str = "plan") -> Stage:
    return Stage(id=sid, name=sid.title(), kind="agent", phase="plan", prompt=prompt)


# ------------------------------------------------------------------ context


def test_render_context_shape() -> None:
    ctx = render_context(TICKET, stage=_stage(""), run_id=7)
    assert ctx == {
        "ticket": {
            "key": "AOS-42",
            "title": TICKET.title,
            "description": TICKET.description,
            "type": "bug",
            "source_url": None,
            "repo_url": TICKET.repo_url,
            "base_branch": "main",
            "branch": "agent/AOS-42",
        },
        "skill": {"name": "context-aos-42-fix-login-bug"},
        "paths": {
            "skill_dir": D,
            "skill_md": f"{D}/SKILL.md",
            "tasks_md": f"{D}/TASKS.md",
            "learnings": f"{D}/learnings.log",
            "swarm": f"{D}/swarm.log",
            "out": f"{D}/out",
        },
        "stage": {"id": "plan", "name": "Plan"},
        "run": {"id": 7},
    }


def test_render_context_without_stage_or_run() -> None:
    ctx = render_context(NO_REPO)
    assert "stage" not in ctx
    assert "run" not in ctx
    assert ctx["ticket"]["branch"] is None


# ------------------------------------------------------------------ prompts


def test_render_prompt_default_review_repo_and_no_repo() -> None:
    stage = _stage(PROMPTS["review"], "review")
    assert render_prompt(stage, TICKET, 1).startswith(
        "Review the work for AOS-42 on this branch against origin/main, checking it against "
        f"{D}/SKILL.md"
    )
    assert render_prompt(stage, NO_REPO, 1).startswith(
        f"Review the work for AOS-42, checking it against {D}/SKILL.md"
    )


def test_render_prompt_stage_and_run() -> None:
    assert render_prompt(_stage("{{stage.id}}/{{stage.name}}#{{run.id}}"), TICKET, 9) == (
        "plan/Plan#9"
    )


def test_render_prompt_keeps_trailing_newline_and_does_not_escape() -> None:
    out = render_prompt(_stage("<b>{{ticket.title}}</b> & 'x'\n"), replace(TICKET, title="a<b"), 1)
    assert out == "<b>a<b</b> & 'x'\n"


@pytest.mark.parametrize(
    ("prompt", "needle"),
    [
        ("{{ ticket.nope }}", "nope"),
        ("{{ nothing }}", "nothing"),
        ("{% if %}", ""),
        ("{{ ticket.key ", ""),
        ("{{ 1 / 0 }}", "ZeroDivisionError"),
        ("{{ ''.__class__.__mro__ }}", ""),  # sandbox
        ("{{ ticket.key.__class__ }}", ""),
    ],
)
def test_render_errors_name_the_stage(prompt: str, needle: str) -> None:
    with pytest.raises(ApiError) as e:
        render_prompt(_stage(prompt, "implement"), TICKET, 1)
    assert (e.value.status, e.value.code) == (422, "render_error")
    assert e.value.message.startswith("stage implement: ")
    assert needle in e.value.message


# ------------------------------------------------------------------ tojson


def yaml_printable(c: str) -> bool:
    """YAML 1.2 c-printable, minus NEL/LS/PS (line breaks to YAML 1.1 parsers)."""
    o = ord(c)
    return (
        o in (0x09, 0x0A, 0x0D)
        or 0x20 <= o <= 0x7E
        or (0xA0 <= o <= 0xD7FF and o not in (0x2028, 0x2029))
        or 0xE000 <= o <= 0xFFFD
        or 0x10000 <= o <= 0x10FFFF
    )


@pytest.mark.parametrize(
    "value",
    [
        'He said "hi": done # not a comment',
        "line1\nline2\r\n\ttab",
        "😀 emoji and 修复 CJK and café",
        "<script>&'</script>",
        "\x00\x1f\x7f\x85" + chr(0x2028) + chr(0x2029) + " controls",
        "back\\slash",
    ],
)
def test_yaml_safe_json(value: str) -> None:
    out = yaml_safe_json(value)
    assert json.loads(out) == value
    # Valid YAML double-quoted scalar: only YAML-printable chars raw, no surrogate escapes.
    assert all(yaml_printable(c) for c in out)
    assert not re.search(r"\\u[dD][89a-fA-F]", out)
    assert "\n" not in out


# ------------------------------------------------------------------ scaffold


def _frontmatter(skill_md: str) -> dict[str, str]:
    """Minimal parse of the SKILL.md frontmatter: `name` is a plain scalar, `description` must be
    a JSON string (which is also a valid YAML double-quoted scalar)."""
    lines = skill_md.split("\n")
    assert lines[0] == "---"
    end = lines.index("---", 1)
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        k, v = line.split(": ", 1)
        fields[k] = v
    assert set(fields) == {"name", "description"}
    assert re.fullmatch(r"[a-z0-9-]{1,64}", fields["name"])
    return {"name": fields["name"], "description": json.loads(fields["description"])}


def test_render_default_scaffold_repo_mode() -> None:
    files = render_scaffold(DEFAULT_SCAFFOLD, TICKET)
    by_path = {f.path: f for f in files}
    assert list(by_path) == [
        f"{D}/SKILL.md",
        f"{D}/TASKS.md",
        f"{D}/learnings.log",
        f"{D}/swarm.log",
        "AGENTS.md",
        "CLAUDE.md",
    ]
    skill = by_path[f"{D}/SKILL.md"].content
    assert _frontmatter(skill) == {
        "name": "context-aos-42-fix-login-bug",
        "description": "Objective and working context for AOS-42 (Fix login bug: session expires "
        "early). Use when working on AOS-42.",
    }
    assert skill.endswith(
        "---\n# AOS-42: Fix login bug: session expires early\n\n## Objective\n"
        "Sessions expire after 5 minutes instead of 14 days.\n"
    )
    assert by_path[f"{D}/TASKS.md"].content == "# Tasks\n"
    assert by_path[f"{D}/learnings.log"].content == ""
    agents = by_path["AGENTS.md"]
    assert agents.mode == "block"
    assert agents.content == (
        f"Working on AOS-42. Read {D}/SKILL.md first; the plan is {D}/TASKS.md. Do not run git "
        "commit or push; AgentOS commits and pushes after each stage."
    )
    assert by_path["CLAUDE.md"].content == "@AGENTS.md"


def test_render_default_scaffold_no_repo_and_source_link() -> None:
    t = replace(NO_REPO, source_url="https://jira.example.com/browse/AOS-42")
    by_path = {f.path: f for f in render_scaffold(DEFAULT_SCAFFOLD, t)}
    assert by_path["AGENTS.md"].content.endswith(f"after each stage. Put deliverables in {D}/out/.")
    assert "\n\nSource: https://jira.example.com/browse/AOS-42\n\n## Objective\n" in (
        by_path[f"{D}/SKILL.md"].content
    )


@pytest.mark.parametrize(
    "title",
    [
        "Quote \"this\" and 'that'",
        "Colon: in: title",
        "# hash at start",
        "multi\nline\r\ntitle",
        "修复登录 with emoji 😀🚀",
        "--- yaml doc marker",
        "back\\slash {{ not jinja }}",
        "x" * 200,
    ],
)
def test_skill_md_frontmatter_valid_for_awkward_titles(title: str) -> None:
    t = replace(TICKET, title=title, slug=slugify(title, TICKET.key))
    skill = render_scaffold(DEFAULT_SCAFFOLD, t)[0]
    fm = _frontmatter(skill.content)
    assert title in fm["description"]
    assert len(fm["description"]) <= 1024


def test_scaffold_render_error_names_entry() -> None:
    entries = [FileEntry(path="notes/{{ticket.nope}}.md", content="x")]
    with pytest.raises(ApiError) as e:
        render_scaffold(entries, TICKET)
    assert e.value.code == "render_error"
    assert "notes/{{ticket.nope}}.md" in e.value.message


def test_scaffold_stage_is_undefined() -> None:
    with pytest.raises(ApiError) as e:
        render_scaffold([FileEntry(path="a.md", content="{{stage.id}}")], TICKET)
    assert e.value.code == "render_error"
    assert "'a.md' content" in e.value.message


@pytest.mark.parametrize(
    "entries",
    [
        [FileEntry(path="{{ticket.title}}.md", content="")],  # ':' in the rendered path
        [FileEntry(path="../{{ticket.key}}", content="")],
        [FileEntry(path=".git/hooks/x", content="")],
        [FileEntry(path="a.md", content=""), FileEntry(path="A.MD", content="")],
        [FileEntry(path="AGENTS.md", mode="block", content="<!-- agentos:end {{ticket.key}} -->")],
    ],
)
def test_scaffold_validation_errors(entries: list[FileEntry]) -> None:
    with pytest.raises(ApiError) as e:
        render_scaffold(entries, TICKET)
    assert (e.value.status, e.value.code) == (422, "validation_error")


def test_scaffold_oversize_file() -> None:
    with pytest.raises(ApiError) as e:
        render_scaffold([FileEntry(path="big.md", content="{{ 'x' * 1048577 }}")], TICKET)
    assert (e.value.status, e.value.code) == (413, "too_large")


def test_check_scaffold_default_and_mode_dependent_paths() -> None:
    check_scaffold(DEFAULT_SCAFFOLD)
    # Valid in repo mode only: no-repo renders an empty path segment.
    bad = [FileEntry(path="{{ticket.branch or ''}}/x.md", content="")]
    with pytest.raises(ApiError):
        check_scaffold(bad)
