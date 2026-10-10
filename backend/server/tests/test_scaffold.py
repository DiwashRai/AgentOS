"""Slugs, derived names and the default seed (SPEC §4, §15)."""

from __future__ import annotations

import re

import pytest

from agentos.pipeline.scaffold import (
    default_pipeline,
    names,
    repo_name,
    skill_dir,
    skill_name,
    slugify,
)

SKILL_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")


@pytest.mark.parametrize(
    ("title", "slug"),
    [
        ("Fix login bug", "fix-login-bug"),
        ("Fix login bug: session expires early", "fix-login-bug-session-expires-early"),
        ("#42 -- Crash on save!", "42-crash-on-save"),
        ("Café déjà vu", "cafe-deja-vu"),
        ("ﬁle ①", "file-1"),  # NFKD compatibility forms
        ("修复登录", "ticket"),  # CJK has no ASCII decomposition
        ("🚀 Launch 🚀", "launch"),
        ("😀", "ticket"),
        ("", "ticket"),
        ("---", "ticket"),
        ("  Mixed   CASE\tand\nnewlines ", "mixed-case-and-newlines"),
    ],
)
def test_slugify(title: str, slug: str) -> None:
    assert slugify(title, "AOS-1") == slug


@pytest.mark.parametrize("key", ["AOS-1", "AOS-123456", "PROJECTNAME-99999", "A" * 37 + "-99"])
def test_long_title_keeps_skill_name_within_64(key: str) -> None:
    slug = slugify("word " * 100, key)
    name = skill_name(key, slug)
    assert len(name) <= 64
    assert SKILL_NAME_RE.match(name)
    assert not slug.startswith("-")
    assert not slug.endswith("-")


def test_truncation_trims_trailing_dash() -> None:
    # budget for AOS-1 is 64 - len("context-aos-1-") = 50; cut lands right after a word
    title = "a" * 49 + " b"
    assert slugify(title, "AOS-1") == "a" * 49


def test_names_repo_mode() -> None:
    n = names("AOS-42", "fix-login-bug", "repo")
    d = ".claude/skills/context-aos-42-fix-login-bug"
    assert n.skill_name == "context-aos-42-fix-login-bug"
    assert n.skill_dir == d == skill_dir("AOS-42", "fix-login-bug")
    assert (n.skill_md, n.tasks_md, n.learnings, n.swarm, n.out) == (
        f"{d}/SKILL.md",
        f"{d}/TASKS.md",
        f"{d}/learnings.log",
        f"{d}/swarm.log",
        f"{d}/out",
    )
    assert n.branch == "agent/AOS-42"


def test_names_no_repo_mode_has_no_branch() -> None:
    assert names("AOS-42", "x", "none").branch is None


@pytest.mark.parametrize(
    ("url", "name"),
    [
        (None, None),
        ("", None),
        ("https://github.com/org/app.git", "app"),
        ("https://github.com/org/app/", "app"),
        ("git@github.com:org/app.git", "app"),
        ("git@github.com:app.git", "app"),
    ],
)
def test_repo_name(url: str | None, name: str | None) -> None:
    assert repo_name(url) == name


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
