"""API and domain models. This module is the contract between server, worker and web.

Pure: no web, db or http imports (enforced by import-linter).
Rules: datetimes are timezone-aware (AwareDatetime); error codes come from ErrorCode;
PATCH bodies reject explicit nulls except for fields that can be cleared.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

# `pending` = signed in but not approved by an admin yet: only GET /api/me, logout and the public
# routes work (403 `pending_approval` everywhere else). Ranks below viewer.
Role = Literal["pending", "viewer", "writer", "admin"]
TicketType = Literal["feature", "bug", "improvement", "task"]
Priority = Literal["high", "normal", "low"]
TicketState = Literal["open", "done", "closed"]
WorkspaceMode = Literal["repo", "none"]
# Board columns. Stages map to the four work phases; inbox/done are ticket-level.
Phase = Literal["inbox", "plan", "build", "verify", "review", "done"]
StagePhase = Literal["plan", "build", "verify", "review"]
# Board row groups, derived per viewer (SPEC §13). `needs_you`/`waiting`: the latest run is gated
# or succeeded and a human decides what's next (the viewer, or someone else). `running`: the latest
# run is running; `queued`: queued or dispatched; `blocked`: failed or cancelled; `inbox`: never
# dispatched; `done`: the ticket is done or closed.
Attention = Literal["needs_you", "waiting", "running", "queued", "blocked", "inbox", "done"]
RunStatus = Literal["queued", "dispatched", "running", "succeeded", "gated", "failed", "cancelled"]
StageRunStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]
StageOutcome = Literal["succeeded", "failed", "cancelled"]
# `skipped` = the stage is disabled on this ticket (it still shows, so enable/disable is visible).
RailState = Literal["pending", "done", "current", "failed", "stale", "skipped"]
# Per viewer, like Attention: `you`/`waiting` = the ticket is parked at this gate and it's on the
# viewer / on someone else; `passed` = the ticket is past it; `pending` = not reached yet.
GateState = Literal["pending", "passed", "waiting", "you"]
PhaseState = Literal["pending", "done", "current", "failed", "stale", "skipped"]
Mode = Literal["replace", "block"]
StageKind = Literal["agent", "shell"]
SourceName = Literal["jira", "github", "todoist"]
WorkerFinalStatus = Literal["completed", "failed", "cancelled"]

# Every error code the API can return. The UI branches on these (e.g. 409 stale_version vs
# run_active vs bundle_locked). Add new codes here; never invent them at the raise site.
ErrorCode = Literal[
    # generic
    "unauthorized",
    "forbidden",
    "not_found",
    "method_not_allowed",
    "http_error",
    "validation_error",
    "internal_error",
    "not_implemented",
    "csrf_header_missing",
    "rate_limited",
    "upstream_error",
    "too_large",
    # auth
    "missing_credentials",
    "invalid_credentials",
    "no_project_access",
    "user_disabled",
    "pending_approval",
    # lookups
    "ticket_not_found",
    "file_not_found",
    "version_not_found",
    "run_not_found",
    "user_not_found",
    # conflicts
    "stale_version",
    "run_active",
    "bundle_locked",
    "already_claimed",
    "key_taken",  # an import whose key another ticket already has
    "in_use",  # a settings save would remove something that is still used (e.g. a repo)
    # runs & pipeline
    "invalid_stage",
    "invalid_pipeline",
    "render_error",
    "run_gone",
]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatchModel(ApiModel):
    """PATCH body: omitted = unchanged. Explicit null is rejected unless the field is clearable."""

    clearable: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="before")
    @classmethod
    def _reject_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            bad = sorted(k for k, v in data.items() if v is None and k not in cls.clearable)
            if bad:
                raise ValueError(f"cannot be null: {', '.join(bad)}")
        return data


# ------------------------------------------------------------------ errors


class ErrorDetail(ApiModel):
    code: ErrorCode
    message: str


class ErrorBody(ApiModel):
    error: ErrorDetail


# ------------------------------------------------------------------ app config & health


class RepoInfo(ApiModel):
    """One entry of the Space's repo catalog (Settings > Repos)."""

    name: str
    url: str
    default_branch: str


class AppConfig(ApiModel):
    """Public, unauthenticated: what the UI needs before login and for forms."""

    auth_mode: Literal["dev", "jira"]
    jira_deployment: Literal["cloud", "datacenter"] | None
    # Where the login token comes from (Jira auth); null with dev auth.
    jira_url: str | None
    key_prefix: str
    space_name: str
    repos: list[RepoInfo]
    # A catalog name: what a new ticket gets when its `repo` is omitted.
    default_repo: str | None
    runtimes: list[str]
    # The effective Space defaults for an agent stage whose `runtime` / `model` is null (what a
    # dispatch resolves to now): the template's `defaults`, else the server default runtime
    # (`server_default_runtime`) / no model (the runtime CLI's own default; no `--model` is passed).
    default_runtime: str
    default_model: str | None
    # The server default runtime (the first in Settings > Runners): what `default_runtime` falls
    # back to when the template sets no Space runtime (whatever the template says).
    server_default_runtime: str
    # Where tickets can be imported from (POST /api/tickets/import): ["jira"] when the Jira URL and
    # service account are configured, else [].
    sources: list[Literal["jira", "github"]]


class Health(ApiModel):
    status: Literal["ok"]


# ------------------------------------------------------------------ users & auth


class Me(ApiModel):
    id: int
    display_name: str
    email: str | None
    avatar_url: str | None
    role: Role


class User(Me):
    disabled: bool
    # AGENTOS_ADMINS lists one of the user's identity subjects or their email: always admin,
    # can't be demoted from the UI.
    env_admin: bool
    created_at: AwareDatetime
    last_login_at: AwareDatetime | None


class UserPatch(PatchModel):
    role: Role | SkipJsonSchema[None] = None
    disabled: bool | SkipJsonSchema[None] = None


class LoginRequest(ApiModel):
    """Cloud: email + token. Data Center: token. Dev auth: username."""

    email: str | None = None
    username: str | None = None
    token: str | None = None


# ------------------------------------------------------------------ pipeline & template


class FileEntry(ApiModel):
    path: str
    content: str
    mode: Mode = "replace"


# A model name becomes one argv element: no whitespace or control characters.
MODEL_PATTERN = r"^[A-Za-z0-9._:/-]{1,100}$"


class Stage(ApiModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    name: str = Field(min_length=1, max_length=60)
    kind: StageKind
    # Board column this stage counts towards. Different pipelines (per ticket type) can have
    # different stages but still land on the same shared board phases.
    phase: StagePhase
    enabled: bool = True
    gate_after: bool = False
    requires_repo: bool = False
    prompt: str = Field(default="", max_length=65536)
    # null = the Space default (`Template.defaults`, then the server default), resolved when the
    # run's manifest is built, so changing the Space default reaches every stage that leaves these
    # null.
    runtime: str | None = None
    agent: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._:/-]{1,100}$")
    model: str | None = Field(default=None, pattern=MODEL_PATTERN)
    command: str = ""
    timeout_s: int = Field(default=3600, ge=60, le=21600)


class Pipeline(ApiModel):
    stages: list[Stage] = Field(min_length=1, max_length=20)
    library_sets: list[int] = Field(default_factory=list)


class TemplateDefaults(ApiModel):
    """The Space-wide defaults for agent stages, as the admin set them (null = not set). A stage's
    own `runtime` / `model` wins; unset here → the server default runtime (the first in
    Settings > Runners) / no model. `runtime` must be a configured runtime (422
    `validation_error`)."""

    runtime: str | None = None
    model: str | None = Field(default=None, pattern=MODEL_PATTERN)


class Template(ApiModel):
    pipeline: Pipeline
    scaffold: list[FileEntry]
    defaults: TemplateDefaults
    version: int


class TemplateUpdate(PatchModel):
    """PUT: `pipeline` and `scaffold` replace the current ones. `defaults` omitted = keep the
    current defaults (explicit null is rejected); send `{}` or nulls to clear them."""

    pipeline: Pipeline
    scaffold: list[FileEntry]
    defaults: TemplateDefaults | SkipJsonSchema[None] = None
    base_version: int


# ------------------------------------------------------------------ tickets


class SourceLink(ApiModel):
    source: SourceName
    external_id: str
    url: str


class Person(ApiModel):
    id: int
    display_name: str
    avatar_url: str | None


class GateInfo(ApiModel):
    """The human gate after a stage (`gate_after`). `person` is who it waits on (`waiting` only)."""

    label: str  # e.g. "Plan sign-off"
    short: str  # e.g. "Sign-off": the stage label while parked here
    state: GateState
    person: Person | None


class RailStep(ApiModel):
    """Stage-level progress: every stage of the ticket's pipeline, in order (disabled ones
    `skipped`). `gate` is set on every enabled stage with `gate_after`."""

    stage_id: str
    name: str
    phase: StagePhase
    state: RailState
    gate: GateInfo | None


class PhaseStep(ApiModel):
    """Board-level progress: one entry per Phase, always all six, in order."""

    phase: Phase
    state: PhaseState


class TaskProgress(ApiModel):
    done: int
    total: int


class PlanItem(ApiModel):
    """One checklist line of TASKS.md (`- [ ] text` / `- [x] text`), text stripped."""

    text: str
    done: bool
    # The nearest markdown heading above the item (any level, `#` marks stripped); None if
    # there is none. The UI groups consecutive items by it (request 8). Always sent.
    section: str | None = None


class NowInfo(ApiModel):
    """Server-written "Now / Next" line. The UI renders it; it never derives it.

    `actor` is a non-human label only (the running stage, later "CI" or "Queue"). `person` is who
    the ticket waits on: the owner today; approver, human-stage assignee or PR reviewer later.
    e.g. running:   actor="Implement", run_id=42, text="Run API test suite"
                    → "Implement · run #42 · …"
         queued:    run_id=42, text="queued for a runner"
         blocked:   run_id=42, text="Review failed: retry or adjust"
         needs_you: person=None, text="review plan and continue" → "Waiting on you · …"
         waiting:   person=Sam, text="review plan and continue"  → "Waiting on Sam Lee · …"
         inbox:     run_id=None, text="add context, then dispatch"
    """

    actor: str | None
    person: Person | None
    text: str
    run_id: int | None
    progress: TaskProgress | None


# AGENTOS_LAUNCHER. mailbox: runs travel through a store (design/mailbox.md).
LauncherName = Literal["local", "github", "mailbox"]
LogDelivery = Literal["live", "at_end"]


class RunSummary(ApiModel):
    id: int
    ticket_key: str
    status: RunStatus
    from_stage: str
    stage_ids: list[str]
    created_by: Person | None
    created_at: AwareDatetime
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    # Set while a running run waits for its worker to acknowledge a cancel (UI: "Cancelling…").
    cancel_requested_at: AwareDatetime | None
    error: str | None
    launch_ref: str | None  # informational: a PID (local), the Actions run URL (github)
    # The launcher's page for this run (the GitHub Actions run); null for the local launcher.
    launch_url: str | None
    # When the run's events arrive: "live" as the worker sends them, or "at_end" (a mailbox run:
    # its logs and stage progress land when the GitHub job finishes; launch_url shows it live).
    log_delivery: LogDelivery


# ------------------------------------------------------------------ resolution (request 15)

# `completed` → state `done`; `abandoned` → state `closed`.
ResolveOutcome = Literal["completed", "abandoned"]


class Resolution(ApiModel):
    """How a ticket was closed, and by whom. Cleared by any PATCH that changes `state`."""

    outcome: ResolveOutcome
    note: str | None
    by: Person
    at: AwareDatetime


class ResolveRequest(ApiModel):
    """POST /api/tickets/{key}/resolve. `note` is stripped; blank → null. Not a chat message."""

    outcome: ResolveOutcome
    note: str | None = Field(default=None, max_length=2000)


class TicketSummary(ApiModel):
    key: str
    title: str
    type: TicketType
    priority: Priority
    labels: list[str]
    state: TicketState
    # Set by POST .../resolve (Complete / Abandon); null if open or closed through PATCH `state`.
    resolution: Resolution | None
    owner: Person | None
    # Derived by the server (SPEC §13). `attention` and the rail's gate states depend on the viewer
    # (owner vs not). `attention_since`: when the ticket entered its current attention.
    attention: Attention
    attention_since: AwareDatetime
    phase: Phase
    phase_track: list[PhaseStep]
    rail: list[RailStep]
    now: NowInfo
    tasks: TaskProgress | None
    chat_count: int
    latest_run: RunSummary | None
    source: SourceLink | None
    repo_name: str | None  # the ticket's repo catalog name; null = no-repo mode
    # The PR for `branch`, as last reported by a worker (a `gh pr view` at the end of a repo run).
    pr_url: str | None
    updated_at: AwareDatetime


class TicketDetail(TicketSummary):
    description: str
    slug: str
    skill_name: str
    # `repo_name` set → "repo". `repo_url` is resolved from the catalog (null if the repo left
    # it); `base_branch` is the ticket's own, else the repo's default_branch (null: no repo).
    workspace_mode: WorkspaceMode
    repo_url: str | None
    base_branch: str | None
    branch: str | None
    pipeline: Pipeline
    pipeline_version: int
    next_stage: str | None
    active_run_id: int | None
    created_at: AwareDatetime
    # The current TASKS.md checklist (the same parser as `tasks`); None if the file is absent.
    plan: list[PlanItem] | None


class TicketCreate(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    type: TicketType = "task"
    priority: Priority = "normal"
    labels: list[str] = Field(default_factory=list)
    # A repo catalog name. Omitted → the Space's `default_repo`; null → no-repo mode.
    repo: str | None = None
    # Omitted/null → the repo's `default_branch`.
    base_branch: str | None = None
    owner_id: int | None = None


class TicketPatch(PatchModel):
    """`repo` (null = no-repo mode), `base_branch` (null = the repo's default) and `owner_id` may
    be null; others: omit or set."""

    clearable: ClassVar[frozenset[str]] = frozenset({"repo", "base_branch", "owner_id"})

    title: str | SkipJsonSchema[None] = Field(default=None, min_length=1, max_length=200)
    description: str | SkipJsonSchema[None] = None
    type: TicketType | SkipJsonSchema[None] = None
    priority: Priority | SkipJsonSchema[None] = None
    labels: list[str] | SkipJsonSchema[None] = None
    state: TicketState | SkipJsonSchema[None] = None
    repo: str | None = None
    base_branch: str | None = None
    owner_id: int | None = None


class ImportRequest(ApiModel):
    """A source ticket to import: a Jira key (`SCRUM-6`) or its browse link."""

    ref: str = Field(min_length=1, max_length=2000)


class ImportResult(ApiModel):
    """`created: false` = the issue was imported before; `ticket` is that ticket."""

    ticket: TicketDetail
    created: bool


class PipelineUpdate(ApiModel):
    pipeline: Pipeline
    base_version: int


# ------------------------------------------------------------------ intake (request 14)


class IntakeAssignee(ApiModel):
    """A Jira assignee: a Jira account, not (necessarily) an AgentOS user."""

    display_name: str


class IntakeItem(ApiModel):
    """A Jira issue that matches an intake query and isn't in AgentOS yet. `type` and `priority`
    use the import's mapping, so they are what "Take in" (POST /api/tickets/import) creates;
    `status` is the Jira status name."""

    key: str
    title: str
    type: TicketType
    priority: Priority
    status: str
    assignee: IntakeAssignee | None
    updated_at: AwareDatetime
    url: str


class IntakeQuery(ApiModel):
    """One `[[intake.queries]]` entry with its current candidates, newest-updated first, capped.
    `total` = how many candidates the JQL has (exact up to the fetch window, else estimated from
    Jira's approximate count). A failing query has `error` (a short message) and no items."""

    id: str
    name: str
    jql: str
    items: list[IntakeItem]
    total: int
    error: str | None


# ------------------------------------------------------------------ settings (edited in the app)
#
# Admin-only (GET and PUT). Each area is one document: GET returns it with `version`,
# `updated_at` and `updated_by` (null = seeded on first start, not saved by anyone); PUT sends it
# back whole as an `*Update` (no read-only fields) with `base_version` → 409 `stale_version` if
# someone else saved first. Changes apply without a restart.

KEY_PREFIX_PATTERN = r"^[A-Z][A-Z0-9]{0,9}$"
JIRA_PROJECT_PATTERN = r"^[A-Z][A-Z0-9_]+$"
REPO_NAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,63}$"
INTAKE_QUERY_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
SPACE_NAME_MAX = 100
CLAIM_TIMEOUT_MIN_S = 60
CLAIM_TIMEOUT_MAX_S = 86400
MAX_JIRA_PROJECTS = 50
MAX_REPOS = 100
MAX_INTAKE_QUERIES = 50
INTAKE_NAME_MAX = 100
JQL_MAX = 4000

JiraProjectKey = Annotated[str, Field(pattern=JIRA_PROJECT_PATTERN)]


def _stripped(value: str) -> str:
    """Strip; blank → ValueError (reported as 422 `validation_error` with the field path)."""
    if not value.strip():
        raise ValueError("must not be blank")
    return value.strip()


class SpaceSettings(ApiModel):
    """Settings > Space. `key_prefix` only affects new native tickets (stored keys never change).
    `jira_projects`: signing in needs browse access to one of them (AGENTOS_ADMINS people
    excepted); imports and intake only take issues from them."""

    name: str
    default_repo: str | None  # a repo catalog name: what a new ticket gets when it names none
    key_prefix: str
    jira_projects: list[str]
    claim_timeout_s: int  # a dispatched run whose worker never connects fails after this
    version: int
    updated_at: AwareDatetime
    updated_by: Person | None


class SpaceSettingsUpdate(ApiModel):
    """PUT /api/settings/space. 422 `validation_error` when `default_repo` isn't a configured repo,
    `key_prefix` is one of `jira_projects` (native keys would take real Jira keys), a project
    repeats, or `jira_projects` is empty while sign-in is with Jira."""

    name: str = Field(min_length=1, max_length=SPACE_NAME_MAX)
    default_repo: str | None
    key_prefix: str = Field(pattern=KEY_PREFIX_PATTERN)
    jira_projects: list[JiraProjectKey] = Field(max_length=MAX_JIRA_PROJECTS)
    claim_timeout_s: int = Field(ge=CLAIM_TIMEOUT_MIN_S, le=CLAIM_TIMEOUT_MAX_S)
    base_version: int

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _stripped(value)


class RepoEntry(ApiModel):
    name: str
    url: str
    default_branch: str
    # Open tickets that use this repo: removing it is refused (409 `in_use`) while this is > 0.
    open_ticket_count: int


class RepoSettings(ApiModel):
    """Settings > Repos: the Space's repo catalog. Tickets reference a repo by name, so changing a
    URL or default branch reaches every ticket that uses it."""

    repos: list[RepoEntry]
    version: int
    updated_at: AwareDatetime
    updated_by: Person | None


class RepoEntryIn(ApiModel):
    """`url`: https://..., ssh://... or git@host:path (GitHub Actions runners: https)."""

    name: str = Field(pattern=REPO_NAME_PATTERN)
    url: str = Field(min_length=1, max_length=500)
    default_branch: str = Field(min_length=1, max_length=255)


class RepoSettingsUpdate(ApiModel):
    """PUT /api/settings/repos: the whole catalog, in order. Names are unique and can't be renamed
    (a missing name = removed, a new one = added). Removing a repo that open tickets or the
    Space's `default_repo` use → 409 `in_use`; done and closed tickets keep the name."""

    repos: list[RepoEntryIn] = Field(max_length=MAX_REPOS)
    base_version: int


class IntakeQuerySpec(ApiModel):
    """One intake query: a JQL whose matches the Intake tab lists (request 14)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=INTAKE_QUERY_ID_PATTERN)
    name: str = Field(min_length=1, max_length=INTAKE_NAME_MAX)
    jql: str = Field(min_length=1, max_length=JQL_MAX)

    @field_validator("name", "jql")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _stripped(value)


class IntakeSettings(ApiModel):
    """Settings > Intake: the queries, in tab order."""

    queries: list[IntakeQuerySpec]
    version: int
    updated_at: AwareDatetime
    updated_by: Person | None


class IntakeQueryIn(ApiModel):
    """`id: null` = a new query: the server makes a unique id from its name. Sent ids are kept."""

    id: str | None = Field(pattern=INTAKE_QUERY_ID_PATTERN)
    name: str = Field(min_length=1, max_length=INTAKE_NAME_MAX)
    jql: str = Field(min_length=1, max_length=JQL_MAX)

    @field_validator("name", "jql")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _stripped(value)


class IntakeSettingsUpdate(ApiModel):
    """PUT /api/settings/intake. The JQL isn't checked against Jira (it might be down): use the
    preview. Saving drops the intake cache."""

    queries: list[IntakeQueryIn] = Field(max_length=MAX_INTAKE_QUERIES)
    base_version: int


RUNTIME_NAME_PATTERN = r"^[a-z0-9][a-z0-9._-]{0,31}$"
MAX_RUNTIMES = 20
MAX_RUNTIME_ARGS = 50
RUNTIME_ARG_MAX = 1000
# GitHub's `owner/name`, and a workflow file name under .github/workflows/.
GITHUB_REPO_PATTERN = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"
GITHUB_WORKFLOW_PATTERN = r"^[A-Za-z0-9_.-]+\.ya?ml$"
GIT_REF_MAX = 255

RuntimeArgs = Annotated[
    list[Annotated[str, Field(max_length=RUNTIME_ARG_MAX)]], Field(max_length=MAX_RUNTIME_ARGS)
]


class RuntimeEntry(ApiModel):
    """One agent runtime: how a stage's argv is built (SPEC §7). `argv`, then `model_args` when a
    model resolves, `agent_args` when the stage names an agent, then `prompt_args` when
    `prompt_via` is "arg" (else the prompt is the stdin). The placeholders `{model}`, `{agent}`
    and `{prompt}` replace whole elements; no shell is involved. `prompt_via: "arg"` needs
    `prompt_args` containing "{prompt}"; "stdin" takes no `prompt_args`."""

    name: str = Field(pattern=RUNTIME_NAME_PATTERN)
    argv: RuntimeArgs = Field(min_length=1)
    model_args: RuntimeArgs
    agent_args: RuntimeArgs
    prompt_via: Literal["stdin", "arg"]
    prompt_args: RuntimeArgs


class RuntimeSettings(ApiModel):
    """Settings > Runners (runtimes). The first runtime is the server default: what an agent stage
    runs on when neither it nor the template's `defaults` names one."""

    runtimes: list[RuntimeEntry]
    version: int
    updated_at: AwareDatetime
    updated_by: Person | None


class RuntimeSettingsUpdate(ApiModel):
    """PUT /api/settings/runtimes: the whole list, in order (the first = the server default).
    Names are unique. Removing a runtime the current template's `defaults` or stages use → 409
    `in_use` (a ticket's own stage pinned to it fails when dispatched)."""

    runtimes: list[RuntimeEntry] = Field(min_length=1, max_length=MAX_RUNTIMES)
    base_version: int


class RunnerSettings(ApiModel):
    """Settings > Runners: where `AGENTOS_LAUNCHER=github` dispatches each run's workflow. Read at
    every launch (no restart). `launcher` (AGENTOS_LAUNCHER) and `token_set` (AGENTOS_GITHUB_TOKEN
    is set) come from the server's env and are read-only. `github_repo` null = none set: with the
    github launcher, runs then fail at launch saying so."""

    launcher: LauncherName
    github_repo: str | None
    workflow: str
    ref: str
    token_set: bool
    version: int
    updated_at: AwareDatetime
    updated_by: Person | None


class RunnerSettingsUpdate(ApiModel):
    """PUT /api/settings/runner. `github_repo`: `owner/name` or null; `workflow`: a .yml file name
    in that repo's .github/workflows/; `ref`: the branch or tag the workflow file is on."""

    github_repo: str | None = Field(pattern=GITHUB_REPO_PATTERN, max_length=200)
    workflow: str = Field(pattern=GITHUB_WORKFLOW_PATTERN, max_length=200)
    ref: str = Field(min_length=1, max_length=GIT_REF_MAX)
    base_version: int

    @field_validator("ref")
    @classmethod
    def _ref(cls, value: str) -> str:
        return _stripped(value)


class IntakePreviewRequest(ApiModel):
    jql: str = Field(min_length=1, max_length=JQL_MAX)

    @field_validator("jql")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _stripped(value)


class IntakePreview(ApiModel):
    """POST /api/settings/intake/preview ("Test query"): live, not cached. The same filtering and
    error messages as GET /api/intake, at most 5 items; `total` counts every candidate."""

    total: int
    error: str | None
    items: list[IntakeItem]


# ------------------------------------------------------------------ files


class FileInfo(ApiModel):
    path: str
    mode: Mode
    version: int
    size: int
    author: Person | None  # a human edit
    author_run_id: int | None  # an agent edit (synced by a run)
    updated_at: AwareDatetime


class FileContent(FileInfo):
    content: str


class FileVersionInfo(ApiModel):
    version: int
    deleted: bool
    mode: Mode
    size: int
    author: Person | None
    author_run_id: int | None
    created_at: AwareDatetime


class FileWrite(ApiModel):
    content: str
    mode: Mode = "replace"
    base_version: int = Field(ge=0, description="0 to create; otherwise the current version")


# ------------------------------------------------------------------ chat


class ChatMessage(ApiModel):
    id: int
    ticket_key: str
    author: Person
    body: str
    created_at: AwareDatetime


class ChatPost(ApiModel):
    body: str = Field(min_length=1, max_length=4000)


# ------------------------------------------------------------------ manifest (protocol v1)


class ManifestTicket(ApiModel):
    key: str
    slug: str
    title: str


class ManifestWorkspace(ApiModel):
    mode: WorkspaceMode
    repo_url: str | None
    base_branch: str | None
    branch: str | None


class ManifestFile(ApiModel):
    path: str
    content: str
    mode: Mode
    sync: bool


class ManifestStage(ApiModel):
    id: str
    name: str
    kind: StageKind
    timeout_s: int
    argv: list[str] | None = None
    stdin: str | None = None
    command: str | None = None


class ManifestLimits(ApiModel):
    max_file_bytes: int
    max_line_bytes: int


class Manifest(ApiModel):
    protocol: Literal[1] = 1
    run_id: int
    ticket: ManifestTicket
    workspace: ManifestWorkspace
    files: list[ManifestFile]
    sync_dirs: list[str]
    stages: list[ManifestStage]
    limits: ManifestLimits


# ------------------------------------------------------------------ runs & run events


class DispatchRequest(ApiModel):
    from_stage: str | None = None


class StageRunInfo(ApiModel):
    stage_id: str
    name: str
    idx: int
    status: StageRunStatus
    exit_code: int | None
    reason: str | None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None


class RunDetail(RunSummary):
    # The manifest (can be MiBs) is served separately: GET /api/runs/{id}/manifest.
    stage_runs: list[StageRunInfo]


class EmptyData(ApiModel):
    pass


class LogData(ApiModel):
    stream: Literal["stdout", "stderr", "system"]
    text: str
    # True when the line exceeded max_line_bytes and was cut (it can no longer be parsed as JSON).
    truncated: bool = False


class StageFinishedData(ApiModel):
    status: StageOutcome
    exit_code: int | None
    reason: str | None


class WarningData(ApiModel):
    message: str


class _RunEventBase(ApiModel):
    seq: int = Field(ge=1)
    ts: AwareDatetime
    stage_id: str | None


class StageStartedEvent(_RunEventBase):
    type: Literal["stage_started"]
    data: EmptyData


class StageFinishedEvent(_RunEventBase):
    type: Literal["stage_finished"]
    data: StageFinishedData


class LogEvent(_RunEventBase):
    type: Literal["log"]
    data: LogData


class WarningEvent(_RunEventBase):
    type: Literal["warning"]
    data: WarningData


# Discriminated on `type`; the same shape is sent by the worker and served to the UI.
RunEvent = Annotated[
    StageStartedEvent | StageFinishedEvent | LogEvent | WarningEvent,
    Field(discriminator="type"),
]


# ------------------------------------------------------------------ worker API


class ClaimRequest(ApiModel):
    worker_token: str = Field(min_length=32, max_length=256)


class ClaimResponse(ApiModel):
    manifest: Manifest


class EventsRequest(ApiModel):
    events: list[RunEvent] = Field(max_length=500)


class EventsResponse(ApiModel):
    cancel: bool


class SyncFile(ApiModel):
    path: str
    content: str | None
    mode: Mode


class SyncRequest(ApiModel):
    stage_id: str
    files: list[SyncFile]


class SyncResponse(ApiModel):
    versions: dict[str, int]


class CompleteRequest(ApiModel):
    status: WorkerFinalStatus
    failed_stage: str | None = None
    error: str | None = None
    # The PR for the run's `branch`, as last reported by a worker (repo mode, when one exists).
    # The server stores it only if it passes `validate_pr_url`; an invalid one is ignored.
    pr_url: str | None = Field(default=None, max_length=500)


# ------------------------------------------------------------------ live updates (SSE)


class StreamEvent(ApiModel):
    """Payload of each `data:` line on GET /api/stream. Notifications only: refetch on receipt."""

    type: Literal["ticket", "chat", "run"]
    key: str
    id: int | None = None
