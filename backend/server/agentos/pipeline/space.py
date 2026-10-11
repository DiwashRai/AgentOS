"""The Space (SPEC §17): one AgentOS instance, its name and its repo catalog.

`agentos.toml` holds `[space]` (name, default_repo) and `[[repos]]` (name, url, default_branch).
Tickets store a repo *name*; its URL and default branch are resolved from the catalog when they
are read or dispatched, so moving a repo is a config change.

Pure: config.py parses the TOML into these; services pass a `SpaceConfig` in.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentos.errors import ApiError
from agentos.models import RepoInfo, WorkspaceMode
from agentos.validate import validate_base_branch, validate_repo_url

REPO_NAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
DEFAULT_SPACE_NAME = "AgentOS"
DEFAULT_BRANCH = "main"


def _checked(check: Callable[[str], str], value: str) -> str:
    """Run a `validate.py` check, turning its ApiError into the ValueError pydantic reports."""
    try:
        return check(value)
    except ApiError as exc:
        raise ValueError(exc.message.split(": ", 1)[-1]) from None


class RepoSpec(BaseModel):
    """One `[[repos]]` entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=REPO_NAME_PATTERN)
    url: str
    default_branch: str = DEFAULT_BRANCH

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _checked(validate_repo_url, value)

    @field_validator("default_branch")
    @classmethod
    def _branch(cls, value: str) -> str:
        return _checked(validate_base_branch, value)

    def info(self) -> RepoInfo:
        return RepoInfo(name=self.name, url=self.url, default_branch=self.default_branch)


class SpaceSection(BaseModel):
    """The `[space]` table."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(default=DEFAULT_SPACE_NAME, min_length=1, max_length=100)
    default_repo: str | None = None


class SpaceConfig(BaseModel):
    """`[space]` + `[[repos]]` of `agentos.toml`. Zero repos is valid (no-repo tickets only)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    space: SpaceSection = SpaceSection()
    repos: tuple[RepoSpec, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        problems: list[str] = []
        seen: set[str] = set()
        for repo in self.repos:
            if repo.name.casefold() in seen:
                problems.append(f"repos: duplicate name {repo.name!r}")
            seen.add(repo.name.casefold())
        default = self.space.default_repo
        if default is not None and self.repo(default) is None:
            problems.append(f"space.default_repo {default!r} is not in [[repos]]")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    @property
    def name(self) -> str:
        return self.space.name

    @property
    def default_repo(self) -> str | None:
        return self.space.default_repo

    def repo(self, name: str) -> RepoSpec | None:
        return next((r for r in self.repos if r.name == name), None)

    def require_repo(self, name: str) -> RepoSpec:
        """422 `validation_error` naming the repo if it isn't in the catalog."""
        repo = self.repo(name)
        if repo is None:
            raise ApiError(
                422, "validation_error", f"repo: {name!r} is not in this Space's repo catalog"
            )
        return repo

    def repo_for_url(self, url: str) -> RepoSpec | None:
        """The catalog entry for a repo URL (see `repo_url_key`), if any."""
        key = repo_url_key(url)
        return next((r for r in self.repos if repo_url_key(r.url) == key), None)

    def resolve(self, repo: str | None, base_branch: str | None) -> ResolvedRepo:
        """A ticket's stored `repo` + own `base_branch` → what the UI and the manifest see.

        No repo → no-repo mode (nothing else). A repo that left the catalog stays in repo mode
        with a null `url` (dispatch rejects it) and only the ticket's own base branch.
        """
        if repo is None:
            return ResolvedRepo(mode="none", name=None, url=None, base_branch=None)
        spec = self.repo(repo)
        if spec is None:
            return ResolvedRepo(mode="repo", name=repo, url=None, base_branch=base_branch)
        return ResolvedRepo(
            mode="repo", name=repo, url=spec.url, base_branch=base_branch or spec.default_branch
        )


@dataclass(frozen=True, slots=True)
class ResolvedRepo:
    mode: WorkspaceMode
    name: str | None
    url: str | None
    base_branch: str | None


def repo_url_key(url: str) -> str:
    """A repo URL's identity for matching: no trailing `/` or `.git`, host case-insensitive.

    `https://GitHub.com/o/r.git/` and `https://github.com/o/r` match; https vs ssh don't.
    """
    u = url.strip().rstrip("/").removesuffix(".git").rstrip("/")
    if "://" in u:
        scheme, rest = u.split("://", 1)
        authority, sep, path = rest.partition("/")
        return f"{scheme.lower()}://{authority.lower()}{sep}{path}"
    authority, sep, path = u.partition(":")  # scp-like git@host:path
    return f"{authority.lower()}{sep}{path}"
