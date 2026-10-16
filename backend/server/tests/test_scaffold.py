"""Derived names and the default seed (SPEC §4, §15)."""

from __future__ import annotations

import re

import pytest

from agentos.pipeline.scaffold import (
    MAX_SKILL_NAME,
    default_pipeline,
    names,
    skill_dir,
    skill_name,
)
from agentos.validate import MAX_KEY_CHARS

SKILL_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")


@pytest.mark.parametrize(
    ("key", "name"),
    [
        ("AOS-1", "context-aos-1"),
        ("SCRUM-123", "context-scrum-123"),
        ("A" * (MAX_KEY_CHARS - 3) + "-99", "context-" + "a" * (MAX_KEY_CHARS - 3) + "-99"),
    ],
)
def test_skill_is_named_by_the_key(key: str, name: str) -> None:
    assert skill_name(key) == name
    assert len(name) <= MAX_SKILL_NAME
    assert SKILL_NAME_RE.match(name)


def test_names_repo_mode() -> None:
    n = names("AOS-42", "repo")
    d = ".claude/skills/context-aos-42"
    assert n.skill_name == "context-aos-42"
    assert n.skill_dir == d == skill_dir("AOS-42")
    assert (n.skill_md, n.tasks_md, n.learnings, n.swarm, n.sync_back) == (
        f"{d}/SKILL.md",
        f"{d}/TASKS.md",
        f"{d}/learnings.log",
        f"{d}/swarm.log",
        ".agentos/sync_back",
    )
    assert n.branch == "agent/AOS-42"


def test_names_no_repo_mode_has_no_branch_and_the_same_sync_back() -> None:
    n = names("AOS-42", "none")
    assert (n.branch, n.sync_back) == (None, ".agentos/sync_back")


def test_default_pipeline_seed() -> None:
    p = default_pipeline()
    assert [s.id for s in p.stages] == ["plan", "implement", "lint", "review", "open-pr"]
    by_id = {s.id: s for s in p.stages}
    assert by_id["plan"].gate_after
    assert by_id["lint"].kind == "shell"
    assert not by_id["lint"].enabled
    assert by_id["lint"].command == ""
    assert by_id["open-pr"].requires_repo
    assert all(s.prompt for s in p.stages if s.kind == "agent")
