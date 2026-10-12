"""The Space (SPEC §17): one AgentOS instance, its settings, its repo catalog, its intake queries
and where its runs are dispatched (the runner).

They are edited in the app (Settings > Space, Intake, Runners) and stored in the db `settings`
table (`agentos.space_store`); on first start each area is seeded from an `agentos.toml` if one
exists (`[space]` name/default_repo, `[[repos]]`, `[[intake.queries]]`) and the moved env vars
(config.py), else from the defaults here.
Tickets store a repo *name*; its URL and default branch are resolved from the catalog when they
are read or dispatched, so moving a repo is a settings change.

Pure: services pass a `SpaceConfig` in.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentos.errors import ApiError
from agentos.models import (
    CLAIM_TIMEOUT_MAX_S,
    CLAIM_TIMEOUT_MIN_S,
    GIT_REF_MAX,
    GITHUB_REPO_PATTERN,
    GITHUB_WORKFLOW_PATTERN,
    INTAKE_QUERY_ID_PATTERN,
    KEY_PREFIX_PATTERN,
    REPO_NAME_PATTERN,
    SPACE_NAME_MAX,
    IntakeQuerySpec,
    JiraProjectKey,
    RepoInfo,
    WorkspaceMode,
)
from agentos.validate import validate_base_branch, validate_repo_url

__all__ = [
    "INTAKE_QUERY_ID_PATTERN",
    "REPO_NAME_PATTERN",
    "IntakeQuerySpec",
    "IntakeSection",
    "RepoSpec",
    "ResolvedRepo",
    "RunnerSection",
    "SpaceConfig",
    "SpaceSection",
    "query_id",
    "repo_url_key",
]

DEFAULT_SPACE_NAME = "AgentOS"
DEFAULT_BRANCH = "main"
DEFAULT_KEY_PREFIX = "AOS"
DEFAULT_CLAIM_TIMEOUT_S = 900
DEFAULT_WORKFLOW = "agentos-worker.yml"
# The keys agentos.toml's `[space]` may hold (the first-start seed); the rest come from env.
SPACE_TOML_KEYS = ("name", "default_repo")
DEFAULT_QUERY_ID = "query"
_QUERY_ID_MAX = 64


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
    """The Space's own settings (Settings > Space; the db `space` area). Every rule that needs no
    other area lives here, so the API and the first-start seed check the same things."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(default=DEFAULT_SPACE_NAME, min_length=1, max_length=SPACE_NAME_MAX)
    default_repo: str | None = None
    key_prefix: str = Field(default=DEFAULT_KEY_PREFIX, pattern=KEY_PREFIX_PATTERN)
    jira_projects: tuple[JiraProjectKey, ...] = ()
    claim_timeout_s: int = Field(
        default=DEFAULT_CLAIM_TIMEOUT_S, ge=CLAIM_TIMEOUT_MIN_S, le=CLAIM_TIMEOUT_MAX_S
    )

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        seen: set[str] = set()
        for key in self.jira_projects:
            if key in seen:
                raise ValueError(f"jira_projects: {key!r} is listed twice")
            seen.add(key)
        if self.key_prefix in seen:
            raise ValueError(
                f"key_prefix: {self.key_prefix!r} is one of jira_projects (native keys would take "
                "real Jira keys, which then can't be imported); pick another prefix"
            )
        return self


class IntakeSection(BaseModel):
    """The intake queries (Settings > Intake). Zero queries is valid (the Intake tab is empty)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    queries: tuple[IntakeQuerySpec, ...] = ()


class RunnerSection(BaseModel):
    """Where AGENTOS_LAUNCHER=github dispatches runs (Settings > Runners; the db `runner` area):
    the workflow file `workflow` on `ref` of `github_repo` (None = not set yet). Read at every
    launch. The token and API URL stay in env."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    github_repo: str | None = Field(default=None, pattern=GITHUB_REPO_PATTERN, max_length=200)
    workflow: str = Field(default=DEFAULT_WORKFLOW, pattern=GITHUB_WORKFLOW_PATTERN, max_length=200)
    ref: str = Field(default=DEFAULT_BRANCH, min_length=1, max_length=GIT_REF_MAX)

    @field_validator("ref")
    @classmethod
    def _ref(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class SpaceConfig(BaseModel):
    """The Space's settings, repo catalog and intake queries: one consistent snapshot. Zero repos is
    valid (no-repo tickets only); zero intake queries too."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    space: SpaceSection = SpaceSection()
    repos: tuple[RepoSpec, ...] = ()
    intake: IntakeSection = IntakeSection()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        problems: list[str] = []
        seen: set[str] = set()
        for repo in self.repos:
            if repo.name.casefold() in seen:
                problems.append(f"repos: duplicate name {repo.name!r}")
            seen.add(repo.name.casefold())
        query_ids: set[str] = set()
        for query in self.intake.queries:
            if query.id in query_ids:
                problems.append(f"intake.queries: duplicate id {query.id!r}")
            query_ids.add(query.id)
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

    @property
    def key_prefix(self) -> str:
        return self.space.key_prefix

    @property
    def jira_projects(self) -> tuple[str, ...]:
        return self.space.jira_projects

    @property
    def claim_timeout_s(self) -> int:
        return self.space.claim_timeout_s

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


def query_id(name: str, taken: Collection[str]) -> str:
    """A new intake query's id from its name, not in `taken`: `SCRUM: to do` → `scrum-to-do`,
    then `scrum-to-do-2`, `-3`... A name with no letters or digits → `query`. Always matches
    INTAKE_QUERY_ID_PATTERN."""
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:_QUERY_ID_MAX].rstrip("-")
    base = base or DEFAULT_QUERY_ID
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        suffix = f"-{n}"
        candidate = base[: _QUERY_ID_MAX - len(suffix)].rstrip("-") + suffix
    return candidate
