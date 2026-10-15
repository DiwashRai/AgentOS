"""The Space's repo catalog, pure parts (SPEC §17): repo resolution."""

from __future__ import annotations

import pytest

from agentos.models import BaseBranchSource, RepoInfo
from agentos.pipeline.space import RepoSpec, ResolvedRepo, SpaceConfig, SpaceSection

SPACE = SpaceConfig(
    space=SpaceSection(default_repo="app"),
    repos=(
        RepoSpec(name="app", url="https://github.com/org/app.git"),
        RepoSpec(name="docs", url="git@github.com:org/docs.git", default_branch="develop"),
    ),
)


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
