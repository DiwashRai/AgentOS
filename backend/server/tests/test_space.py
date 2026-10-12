"""The Space's repo catalog, pure parts (SPEC §17): URL matching and repo resolution."""

from __future__ import annotations

import pytest

from agentos.models import BaseBranchSource, RepoInfo
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


APP = "https://github.com/org/app.git"
DOCS = "git@github.com:org/docs.git"
BARE = "https://github.com/org/bare.git"
WITH_BARE = SPACE.model_copy(
    update={"repos": (*SPACE.repos, RepoSpec(name="bare", url=BARE, default_branch=None))}
)


@pytest.mark.parametrize(
    ("repo", "branch", "source", "expected"),
    [
        (None, None, "default", ResolvedRepo("none", None, None, None, None)),
        ("app", None, "default", ResolvedRepo("repo", "app", APP, "main", "repo_default")),
        ("docs", None, "default", ResolvedRepo("repo", "docs", DOCS, "develop", "repo_default")),
        ("docs", "rel", "manual", ResolvedRepo("repo", "docs", DOCS, "rel", "manual")),
        ("docs", "rel", "mapping", ResolvedRepo("repo", "docs", DOCS, "rel", "mapping")),
        # A mapping that left it blank: the repo default does NOT fill it.
        ("docs", None, "mapping", ResolvedRepo("repo", "docs", DOCS, None, "none")),
        # A repo without a default branch.
        ("bare", None, "default", ResolvedRepo("repo", "bare", BARE, None, "none")),
        ("bare", "rel", "manual", ResolvedRepo("repo", "bare", BARE, "rel", "manual")),
        ("bare", "rel", "mapping", ResolvedRepo("repo", "bare", BARE, "rel", "mapping")),
        ("bare", None, "mapping", ResolvedRepo("repo", "bare", BARE, None, "none")),
        # A repo that left the catalog: only the ticket's own branch.
        ("gone", "rel", "manual", ResolvedRepo("repo", "gone", None, "rel", "manual")),
        ("gone", None, "default", ResolvedRepo("repo", "gone", None, None, "none")),
        # No repo: nothing, whatever is stored.
        (None, None, "mapping", ResolvedRepo("none", None, None, None, None)),
    ],
)
def test_resolve(
    repo: str | None, branch: str | None, source: BaseBranchSource, expected: ResolvedRepo
) -> None:
    assert WITH_BARE.resolve(repo, branch, source=source) == expected


@pytest.mark.parametrize("blank", [None, "", "  "])
def test_a_repo_default_branch_can_be_unset(blank: str | None) -> None:
    assert RepoSpec.model_validate({"name": "x", "url": APP, "default_branch": blank}).info() == (
        RepoInfo(name="x", url=APP, default_branch=None)
    )
    assert RepoSpec(name="x", url=APP).default_branch == "main"  # omitted: "main"
