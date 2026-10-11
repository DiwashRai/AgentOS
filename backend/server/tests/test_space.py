"""The Space's repo catalog, pure parts (SPEC §17): URL matching and repo resolution."""

from __future__ import annotations

import pytest

from agentos.pipeline.space import RepoSpec, ResolvedRepo, SpaceConfig, SpaceSection, repo_url_key

SPACE = SpaceConfig(
    space=SpaceSection(default_repo="app"),
    repos=(
        RepoSpec(name="app", url="https://github.com/org/app.git"),
        RepoSpec(name="docs", url="git@github.com:org/docs.git", default_branch="develop"),
    ),
)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://github.com/org/app.git", "https://github.com/org/app"),
        ("https://github.com/org/app.git", "https://GitHub.com/org/app/"),
        ("git@github.com:org/app.git", "git@GITHUB.com:org/app"),
        ("ssh://git@github.com/org/app.git", "ssh://git@github.com/org/app/"),
    ],
)
def test_same_repo_urls(a: str, b: str) -> None:
    assert repo_url_key(a) == repo_url_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://github.com/org/app.git", "https://github.com/org/App.git"),  # paths keep case
        ("https://github.com/org/app.git", "git@github.com:org/app.git"),  # scheme matters
        ("https://github.com/org/app.git", "https://github.com/org/app2.git"),
    ],
)
def test_different_repo_urls(a: str, b: str) -> None:
    assert repo_url_key(a) != repo_url_key(b)


def test_repo_for_url() -> None:
    found = SPACE.repo_for_url("git@github.com:org/docs")
    assert found is not None
    assert found.name == "docs"
    assert SPACE.repo_for_url("https://github.com/org/other.git") is None


@pytest.mark.parametrize(
    ("repo", "branch", "expected"),
    [
        (None, None, ResolvedRepo("none", None, None, None)),
        ("app", None, ResolvedRepo("repo", "app", "https://github.com/org/app.git", "main")),
        ("docs", None, ResolvedRepo("repo", "docs", "git@github.com:org/docs.git", "develop")),
        ("docs", "rel", ResolvedRepo("repo", "docs", "git@github.com:org/docs.git", "rel")),
        ("gone", "rel", ResolvedRepo("repo", "gone", None, "rel")),
    ],
)
def test_resolve(repo: str | None, branch: str | None, expected: ResolvedRepo) -> None:
    assert SPACE.resolve(repo, branch) == expected
