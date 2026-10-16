"""API and domain models. This module is the contract between server, worker and web.

Pure: no web, db or http imports (enforced by import-linter).
Rules: datetimes are timezone-aware (AwareDatetime); error codes come from ErrorCode;
PATCH bodies reject explicit nulls except for fields that can be cleared.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated, Any, ClassVar, Literal, NamedTuple, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
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
SourceName = Literal["jira"]
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
    # base branch (request 22A)
    "base_branch_missing",  # dispatch of a repo ticket whose base branch resolves to nothing
    "base_branch_locked",  # changing the base branch or repo after a run started work on it
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


class ValidationIssue(ApiModel):
    """One problem with a request. `loc` is the path to the field, like pydantic's, relative to
    the request body (no leading `body`); an empty `loc` is the request as a whole."""

    loc: list[str | int]
    message: str


class ErrorDetail(ApiModel):
    code: ErrorCode
    # The first problem as `loc: message` for a validation error, otherwise a sentence.
    message: str
    # Every problem of a 422 (at least one); [] on other errors.
    issues: list[ValidationIssue]


class ErrorBody(ApiModel):
    error: ErrorDetail


# ------------------------------------------------------------------ app config & health


class RepoInfo(ApiModel):
    """One entry of the Space's repo catalog (Settings > Repos)."""

    name: str
    url: str
    # null = none: a ticket on this repo needs its own base branch before it can be dispatched.
    default_branch: str | None


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
    # The Space default effort (the template's `defaults.effort`; null = none: no effort flag).
    default_effort: str | None
    # The workflow a new ticket gets when it names none (`Workflow.default`).
    default_workflow_id: int
    # The server default runtime (the first in Settings > Runners): what `default_runtime` falls
    # back to when the template sets no Space runtime (whatever the template says).
    server_default_runtime: str
    # Where tickets can be imported from (POST /api/tickets/import): ["jira"] when the Jira URL and
    # service account are configured, else [].
    sources: list[Literal["jira"]]


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


# ------------------------------------------------------------------ client tokens

CLIENT_NAME_MAX = 80


class ClientToken(ApiModel):
    """One of the signed-in user's client tokens (Settings > My client). The token itself is
    shown only once, when it is created. `online`: it leased within the last minute."""

    id: int
    name: str
    created_at: AwareDatetime
    last_seen_at: AwareDatetime | None
    online: bool


class ClientTokenCreate(ApiModel):
    """POST /api/me/clients. `name` is stripped (a machine, e.g. "work laptop")."""

    name: str = Field(min_length=1, max_length=CLIENT_NAME_MAX)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _stripped(value)


class ClientTokenCreated(ApiModel):
    """The new token, in plaintext this once (only its digest is stored), and the server URL the
    client polls (`worker.py --client --server <server_url>`)."""

    client: ClientToken
    token: str
    server_url: str


# ------------------------------------------------------------------ pipeline & template


class FileEntry(ApiModel):
    path: str
    content: str
    mode: Mode = "replace"


# A model name becomes one argv element: no whitespace or control characters.
MODEL_PATTERN = r"^[A-Za-z0-9._:/-]{1,100}$"
# An effort level, also one argv element: a free per-CLI token (Claude: low|medium|high|...).
EFFORT_PATTERN = r"^[A-Za-z0-9._-]{1,32}$"


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
    # null = the Space default effort (on the Space runtime only, like `model`), else no flag. A
    # runtime without `effort_args` passes none. Shell stages ignore `model` and `effort`.
    effort: str | None = Field(default=None, pattern=EFFORT_PATTERN)
    command: str = ""
    timeout_s: int = Field(default=3600, ge=60, le=21600)
    # Loop blocks (request 24). Inside a block, an agent stage with `check` (and every shell
    # stage) is a check: it passes or fails the round with a verdict. The other agent stages are
    # makers: they change the code and get the failing checks' notes in later rounds. Outside a
    # block `check` has no effect.
    check: bool = False
    # How many times an invalid verdict is asked for again (resuming the check's session) before
    # the run fails. 0 = no repair. Agent checks only.
    verdict_repairs: int = Field(default=2, ge=0, le=3)
    # Whether the stage resumes its own session in later rounds. null = by role: makers resume,
    # checks start fresh. A runtime without `session_args` / `resume_args` always starts fresh.
    resume: bool | None = None


LOOP_MAX_ROUNDS = 5


class Loop(ApiModel):
    """A loop block: the stages from `start` to `end` (ids; pipeline order, `start` at or before
    `end`) run round after round until every check in it passes, at most `max_rounds` times. A
    block holds at least one check and one maker; blocks don't overlap; only `end` may have
    `gate_after` (the gate comes after the loop). 422 `invalid_pipeline` otherwise."""

    start: str
    end: str
    max_rounds: int = Field(default=3, ge=1, le=LOOP_MAX_ROUNDS)


class Pipeline(ApiModel):
    stages: list[Stage] = Field(min_length=1, max_length=20)
    loops: list[Loop] = Field(default_factory=list, max_length=10)


class TemplateDefaults(ApiModel):
    """The Space-wide defaults for agent stages, as the admin set them (null = not set). A stage's
    own `runtime` / `model` / `effort` wins; unset here → the server default runtime (the first in
    Settings > Runners) / no model / no effort. `runtime` must be a configured runtime (422
    `validation_error`). `model` and `effort` apply only to stages on the Space runtime."""

    runtime: str | None = None
    model: str | None = Field(default=None, pattern=MODEL_PATTERN)
    effort: str | None = Field(default=None, pattern=EFFORT_PATTERN)


class Template(ApiModel):
    """The Space-wide part of a new ticket: its scaffold (context files) and the agent defaults.
    The pipeline comes from a workflow (request 23B)."""

    scaffold: list[FileEntry]
    defaults: TemplateDefaults
    version: int


class TemplateUpdate(PatchModel):
    """PUT: `scaffold` replaces the current one. `defaults` omitted = keep the current defaults
    (explicit null is rejected); send `{}` or nulls to clear them."""

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
    `skipped`). `gate` is set on every enabled stage with `gate_after`. `round` / `max_rounds`:
    for a stage in a loop block that the latest run has reached (current, or run), its latest
    round and the block's cap ("round 2 of 3"); null otherwise."""

    stage_id: str
    name: str
    phase: StagePhase
    state: RailState
    gate: GateInfo | None
    round: int | None
    max_rounds: int | None


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
                    (a client run: text="waiting for Sam Lee's client")
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
LauncherName = Literal["github", "mailbox"]
LogDelivery = Literal["live", "at_end"]
# Who serves a run: the server's route, or the dispatching user's own client (a pull run).
DispatchTarget = Literal["server", "client"]
# A run's route, stored on it at dispatch: push (a provider starts its worker) or pull (its
# owner's client leases it), by live (HTTP) or file (a mailbox) reporting. Pull + file is refused.
StartMode = Literal["push", "pull"]
ReportMode = Literal["live", "file"]
# Why a run (and its job) was asked to stop: a user's cancel, its time budget, or never started.
StopReason = Literal["cancel", "budget", "never_started"]


class Route(NamedTuple):
    """A run's route (`runs.start`, `provider`, `report`); `provider` is None for pull."""

    start: StartMode
    provider: str | None
    report: ReportMode


class RunSummary(ApiModel):
    id: int
    ticket_key: str
    status: RunStatus
    from_stage: str
    # The stages the run covers, in order: from `from_stage` on, plus the start of the loop block
    # `from_stage` is in (those run from round 2 on).
    stage_ids: list[str]
    created_by: Person | None
    created_at: AwareDatetime
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    # Set once a running run was asked to stop, and why (UI: "Cancelling…", "Over budget…").
    stop_requested_at: AwareDatetime | None
    stop_reason: StopReason | None
    # Why a queued run waits (e.g. for the previous run's worker to stop); null otherwise.
    queue_reason: str | None
    error: str | None
    # Informational: the Actions run URL (github), the client's name (client).
    launch_ref: str | None
    # The provider's page for this run (the GitHub Actions run); null for client runs.
    launch_url: str | None
    # When the run's events arrive: "live" as the worker sends them, or "at_end" (a mailbox run:
    # its logs and stage progress land when the GitHub job finishes; launch_url shows it live).
    log_delivery: LogDelivery
    target: DispatchTarget
    # Client runs: whether the creator has a client token that leased within the last minute.
    # Null for server runs.
    client_online: bool | None


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


# Where a repo ticket's own `base_branch` came from, as stored (`tickets.base_branch_source`):
# `default` = none of its own (the repo's default_branch applies), `manual` = set by hand,
# `mapping` = set (or left blank) by a Jira field mapping (request 22B). Not in the API.
BaseBranchSource = Literal["default", "manual", "mapping"]
# What the UI shows: `manual` / `mapping` = the ticket's own branch, `repo_default` = the repo's
# default_branch, `none` = blank (dispatch is refused until it is set).
BaseBranchOrigin = Literal["manual", "mapping", "repo_default", "none"]


class BaseBranchInfo(ApiModel):
    """Where a repo ticket's resolved `base_branch` came from, and whether it can still change.

    `source_value`: the raw field text a mapping read (several joined with `, `), kept after a
    manual edit for the record; null otherwise. `reason`: a short sentence when the branch is
    blank (`source: none`), else null. `set_by`: who set it by hand (`manual` only). `locked`:
    a run has started work from it (the worker got past checking out the ticket branch), so a
    PATCH of `base_branch` or `repo` → 409 `base_branch_locked`; `locked_by_run` is that run.
    """

    source: BaseBranchOrigin
    source_value: str | None
    reason: str | None
    set_by: Person | None
    locked: bool
    locked_by_run: int | None


class DispatchBlock(ApiModel):
    """Why Dispatch / Continue / Retry / Run from… would be refused now: the 409 `code` the
    dispatch endpoint answers with, and a short UI sentence (e.g. "Set a base branch first")."""

    code: ErrorCode
    text: str


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
    # Set when the dispatch endpoint would refuse this open ticket (e.g. a repo ticket without a
    # base branch → `base_branch_missing`): disable Dispatch / Continue / Retry and show `text`.
    # Null for done/closed tickets (they must be reopened first anyway).
    dispatch_block: DispatchBlock | None
    updated_at: AwareDatetime


class TicketWorkflow(ApiModel):
    """The workflow a ticket's pipeline was copied from, as it was then: `id` is null once that
    workflow is deleted (the name stays); `version` is the workflow's version at the copy."""

    id: int | None
    name: str
    version: int


class TicketDetail(TicketSummary):
    description: str
    skill_name: str
    # `repo_name` set → "repo". `repo_url` is resolved from the catalog (null if the repo left
    # it); `base_branch` is the resolved one: the ticket's own, else (unless a mapping left it
    # blank) the repo's default_branch. Null on a no-repo ticket, and on a repo ticket that has
    # none (then `dispatch_block` is set).
    workspace_mode: WorkspaceMode
    repo_url: str | None
    base_branch: str | None
    # Where `base_branch` came from and whether it is locked; null for a no-repo ticket.
    base_branch_info: BaseBranchInfo | None
    branch: str | None
    pipeline: Pipeline
    pipeline_version: int
    # The workflow `pipeline` was copied from (at creation or the last switch); null for tickets
    # created before workflows. Edits to the ticket's pipeline don't change it.
    workflow: TicketWorkflow | None
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
    # Given → the ticket's own (`manual`, set by the creator). Omitted/null → the repo's
    # `default_branch`; a repo without one leaves the ticket blank and blocked from dispatch.
    base_branch: str | None = None
    owner_id: int | None = None
    # The workflow whose pipeline the ticket copies. Omitted/null → the default (422 if unknown).
    workflow_id: int | None = None


class TicketPatch(PatchModel):
    """`repo` (null = no-repo mode), `base_branch` (null = the repo's default) and `owner_id` may
    be null; others: omit or set. A `base_branch` sets it by hand (`manual`, by the caller);
    changing `repo` drops the ticket's own base branch. Once a run has started work
    (`base_branch_info.locked`), changing `base_branch` or `repo` → 409 `base_branch_locked`
    (sending the current value is fine)."""

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
    # As in TicketCreate: omitted/null → the default workflow; 422 if unknown. Unused when the
    # issue was imported before (that ticket is returned as it is).
    workflow_id: int | None = None


class ImportResult(ApiModel):
    """`created: false` = the issue was imported before; `ticket` is that ticket."""

    ticket: TicketDetail
    created: bool


class PipelineUpdate(ApiModel):
    pipeline: Pipeline
    base_version: int


# ------------------------------------------------------------------ workflows (request 23B)

WORKFLOW_NAME_MAX = 60
WORKFLOW_DESCRIPTION_MAX = 500


class Workflow(ApiModel):
    """A named pipeline preset. Applying one copies its pipeline into a ticket; editing it later
    never changes existing tickets. `default`: what a new ticket gets when it names none."""

    id: int
    name: str
    description: str
    pipeline: Pipeline
    version: int
    updated_at: AwareDatetime
    updated_by: Person | None
    default: bool


class WorkflowCreate(ApiModel):
    """POST /api/workflows. `name` is unique ignoring case (422 `validation_error`); `pipeline` is
    checked like a ticket's (stage ids, runtimes exist, agent prompts render)."""

    name: str = Field(min_length=1, max_length=WORKFLOW_NAME_MAX)
    description: str = Field(default="", max_length=WORKFLOW_DESCRIPTION_MAX)
    pipeline: Pipeline

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _stripped(value)

    @field_validator("description")
    @classmethod
    def _description(cls, value: str) -> str:
        return value.strip()


class WorkflowUpdate(WorkflowCreate):
    """PUT /api/workflows/{id}: replaces name, description and pipeline. 409 `stale_version`."""

    base_version: int


class WorkflowSwitch(ApiModel):
    """POST /api/tickets/{key}/workflow. `base_version` = the ticket's `pipeline_version`."""

    workflow_id: int
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
# Admin-only (GET, PUT and validate). Space, repos, intake, runner and field mappings share one
# versioned-document shape: GET answers `{value, version, updated_at, updated_by}` (`updated_by`
# null = seeded on first start, not saved by anyone; read-only extras sit beside `value`); PUT
# sends `{value, base_version}` → 409 `stale_version` if someone else saved first; POST
# `.../validate` sends `{value}` and runs the same checks without saving (204, or the 422 a save
# would give). A `*Doc` carries types only: the rules live once, in `pipeline.space` /
# `pipeline.mapping`, and one 422 lists every problem at its `value...` field. Changes apply
# without a restart.

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


def matching(pattern: str, message: str | Callable[[str], str]) -> AfterValidator:
    """A string rule for `Annotated[str, matching(...)]`: an empty value is refused as
    "Required.", one `pattern` doesn't match whole with `message` (called with the value when it
    is a function). The same values as `Field(pattern=...)` pass, but the refusal is UI text, not
    the regex."""
    compiled = re.compile(pattern)

    def check(value: str) -> str:
        if not value:
            raise ValueError("Required.")
        if not compiled.fullmatch(value):
            raise ValueError(message if isinstance(message, str) else message(value))
        return value

    return AfterValidator(check)


JiraProjectKey = Annotated[
    str, matching(JIRA_PROJECT_PATTERN, lambda key: f"{key} isn't a Jira project key.")
]


def _stripped(value: str) -> str:
    """Strip; blank → ValueError (reported as 422 `validation_error` with the field path)."""
    if not value.strip():
        raise ValueError("Required.")
    return value.strip()


REPO_URL_MAX = 500


class SettingsMeta(ApiModel):
    """What every settings document's read carries beside its `value`."""

    version: int
    updated_at: AwareDatetime
    updated_by: Person | None


class SpaceDoc(ApiModel):
    """Settings > Space. `key_prefix` only affects new native tickets (stored keys never change);
    it can't be one of `jira_projects` (native keys would take real Jira keys). `jira_projects`:
    signing in needs browse access to one of them (AGENTOS_ADMINS people excepted), so at least one
    is needed while sign-in is with Jira; imports and intake only take issues from them."""

    name: str
    default_repo: str | None  # a repo catalog name: what a new ticket gets when it names none
    key_prefix: str
    jira_projects: list[str]
    claim_timeout_s: int  # the start timeout: a push job not started by then fails


class SpaceSettings(SettingsMeta):
    value: SpaceDoc


class SpaceSettingsValidate(ApiModel):
    value: SpaceDoc


class SpaceSettingsUpdate(ApiModel):
    value: SpaceDoc
    base_version: int


class RepoEntry(ApiModel):
    """`url`: https://..., ssh://... or git@host:path (GitHub Actions runners: https).
    `default_branch`: null or blank = none (then each ticket on the repo needs its own)."""

    name: str
    url: str
    default_branch: str | None


class ReposDoc(ApiModel):
    """Settings > Repos: the Space's repo catalog, in order. Tickets reference a repo by name, so
    changing a URL or default branch reaches every ticket that uses it. Names are unique and
    can't be renamed (a missing name = removed, a new one = added). Removing a repo that open
    tickets or the Space's `default_repo` use → 409 `in_use` on save; done and closed tickets keep
    the name."""

    repos: list[RepoEntry]


class RepoSettings(SettingsMeta):
    value: ReposDoc
    # Open tickets per catalog repo: removing one is refused (409 `in_use`) while it is > 0.
    open_ticket_counts: dict[str, int]


class RepoSettingsValidate(ApiModel):
    value: ReposDoc


class RepoSettingsUpdate(ApiModel):
    value: ReposDoc
    base_version: int


class IntakeQuerySpec(ApiModel):
    """One intake query: a JQL whose matches the Intake tab lists (request 14)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Annotated[
        str,
        matching(
            INTAKE_QUERY_ID_PATTERN,
            "Lowercase letters, digits, _ or -, starting with a letter or digit, at most 64.",
        ),
    ]
    name: str = Field(min_length=1, max_length=INTAKE_NAME_MAX)
    jql: str = Field(min_length=1, max_length=JQL_MAX)

    @field_validator("name", "jql")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _stripped(value)


class IntakeQueryEntry(ApiModel):
    """`id: null` = a new query: the server makes a unique id from its name. Sent ids are kept
    (always set when read)."""

    id: str | None
    name: str
    jql: str


class IntakeDoc(ApiModel):
    """Settings > Intake: the queries, in tab order. The JQL isn't checked against Jira (it might
    be down): use the preview. Saving drops the intake cache."""

    queries: list[IntakeQueryEntry]


class IntakeSettings(SettingsMeta):
    value: IntakeDoc


class IntakeSettingsValidate(ApiModel):
    value: IntakeDoc


class IntakeSettingsUpdate(ApiModel):
    value: IntakeDoc
    base_version: int


# Where the worker reads a check's verdict: the CLI's schema-checked `structured_output` (its last
# `{"type": "result"}` stdout line), or the file `VERDICT_FILE` the agent writes.
VerdictFrom = Literal["structured_output", "file"]

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
    model resolves, `effort_args` when an effort resolves, `agent_args` when the stage names an
    agent, then (loop stages, request 24) `session_args` or `resume_args` and, for checks,
    `verdict_args`, then `prompt_args` when `prompt_via` is "arg" (else the prompt is the stdin).
    The placeholders `{model}`, `{effort}`, `{agent}`, `{verdict_schema}` and `{prompt}` replace
    whole elements; `{session}` (a session id) may be part of one (`--resume={session}`). No
    shell is involved. Empty `effort_args` = the runtime has no effort flag (an effort is then
    ignored). `prompt_via: "arg"` needs `prompt_args` containing "{prompt}" once; "stdin" takes
    no `prompt_args`.

    Sessions: `session_args` start a session with a known id, `resume_args` resume it, and each
    non-empty one contains "{session}". Both empty = no sessions: loop stages start fresh each
    round and an invalid verdict isn't repaired. `verdict_args` ask the CLI for schema-checked
    output (`{verdict_schema}`); `verdict_from` is where the worker reads a check's verdict
    ("structured_output" needs `verdict_args`)."""

    name: str = Field(pattern=RUNTIME_NAME_PATTERN)
    argv: RuntimeArgs = Field(min_length=1)
    model_args: RuntimeArgs
    effort_args: RuntimeArgs = Field(default_factory=list)
    agent_args: RuntimeArgs
    prompt_via: Literal["stdin", "arg"]
    prompt_args: RuntimeArgs
    session_args: RuntimeArgs = Field(default_factory=list)
    resume_args: RuntimeArgs = Field(default_factory=list)
    verdict_args: RuntimeArgs = Field(default_factory=list)
    verdict_from: VerdictFrom = "file"


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


class RunnerDoc(ApiModel):
    """Settings > Runners: where `AGENTOS_LAUNCHER=github` dispatches each run's workflow, read at
    every launch (no restart). `github_repo`: `owner/name`, or null = none set (with the github
    launcher, runs then fail at launch saying so); `workflow`: a .yml file name in that repo's
    .github/workflows/; `ref`: the branch or tag the workflow file is on."""

    github_repo: str | None
    workflow: str
    ref: str


class RunnerSettings(SettingsMeta):
    """`launcher` (AGENTOS_LAUNCHER; null = unset) and `token_set` (AGENTOS_GITHUB_TOKEN is set)
    come from the server's env and are read-only."""

    value: RunnerDoc
    launcher: LauncherName | None
    token_set: bool


class RunnerSettingsValidate(ApiModel):
    value: RunnerDoc


class RunnerSettingsUpdate(ApiModel):
    value: RunnerDoc
    base_version: int


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


# ------------------------------------------------------------------ field mappings (request 22B)

# What a Jira field can set (a closed set that grows: `repo` from Component, priority, type...).
MappingTarget = Literal["base_branch"]
# A Jira field's value shape, from its `schema.type` (GET /rest/api/2/field): `array` covers
# fixVersions, components, labels and multi-selects; `other` = no schema or another type.
JiraFieldKind = Literal["string", "option", "version", "array", "other"]
JIRA_FIELD_ID_PATTERN = r"^[A-Za-z0-9_.:-]+$"  # `fixVersions`, `customfield_10042` (no commas)
JIRA_FIELD_ID_MAX = 255
JIRA_FIELD_NAME_MAX = 255
MAPPING_MATCH_MAX = 200
MAPPING_VALUE_MAX = 255
MAX_MAPPING_RULES = 100
MAX_MAPPINGS = 20
MAX_PREVIEW_VALUES = 100
PREVIEW_VALUE_MAX = 1000


class MappingRule(ApiModel):
    """`match`: a wildcard pattern, not a regex: `*` = any run of characters, everything else
    literal, matched against the whole (trimmed) value, case-insensitive. `value`: what the target
    gets (`base_branch`: a valid branch name). Rules are ordered; the first match wins."""

    match: str = Field(min_length=1, max_length=MAPPING_MATCH_MAX)
    value: str = Field(min_length=1, max_length=MAPPING_VALUE_MAX)

    @field_validator("match", "value")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _stripped(value)


class FieldMapping(ApiModel):
    """A Jira field → an AgentOS field, by rules. `field` = the Jira field id (`fixVersions`,
    `customfield_10042`); `field_name` = its display name (stored, so the UI and the ticket's
    reasons can say "Fix versions" without a Jira call)."""

    target: MappingTarget
    source: Literal["jira"]
    field: Annotated[
        str,
        Field(min_length=1, max_length=JIRA_FIELD_ID_MAX),
        matching(
            JIRA_FIELD_ID_PATTERN,
            "Not a Jira field id: letters, digits, _ . : or -, e.g. customfield_10042.",
        ),
    ]
    field_name: str = Field(min_length=1, max_length=JIRA_FIELD_NAME_MAX)
    rules: list[MappingRule] = Field(max_length=MAX_MAPPING_RULES)

    @field_validator("field_name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _stripped(value)


class MappingRuleEntry(ApiModel):
    """A rule as sent and read (the rules are `MappingRule`'s)."""

    match: str
    value: str


class FieldMappingEntry(ApiModel):
    """A mapping as sent and read (the rules are `FieldMapping`'s)."""

    target: MappingTarget
    source: Literal["jira"]
    field: str
    field_name: str
    rules: list[MappingRuleEntry]


class MappingsDoc(ApiModel):
    """Settings > Field mappings: at most one mapping per target, each rule's `value` valid for
    its target. A `base_branch` mapping sets an imported (manual import or Intake Take in) repo
    ticket's base branch from the Jira field; no match leaves it blank and blocked from dispatch
    (the repo default does not fill it). Saving re-applies the rules to every unlocked ticket
    whose base branch came from a mapping (from its stored Jira value; no Jira call), except
    during a run; hand-set and locked tickets are never touched."""

    mappings: list[FieldMappingEntry]


class MappingSettings(SettingsMeta):
    value: MappingsDoc


class MappingSettingsValidate(ApiModel):
    value: MappingsDoc


class MappingSettingsUpdate(ApiModel):
    value: MappingsDoc
    base_version: int


class JiraField(ApiModel):
    """A Jira field for the mapping's field picker (GET /api/settings/mappings/jira-fields)."""

    id: str
    name: str
    kind: JiraFieldKind


class MappingPreviewRequest(ApiModel):
    """ "Try it": run `mapping`'s (unsaved) rules on `values`, or on the field of `issue` (a Jira
    key or browse link, read with the service account). Exactly one of `values` / `issue`; the
    other is null. `mapping` is checked as on save."""

    mapping: FieldMappingEntry
    values: list[Annotated[str, Field(max_length=PREVIEW_VALUE_MAX)]] | None = Field(
        max_length=MAX_PREVIEW_VALUES
    )
    issue: str | None = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _one_input(self) -> Self:
        if (self.values is None) == (self.issue is None):
            raise ValueError("Give exactly one: values or an issue key.")
        return self


class MappingPreview(ApiModel):
    """What the mapping gives: `values` = the field's values it read (trimmed, blanks dropped),
    `value` = the result (null = blank), `rule` = the index of the rule that gave it, `reason` =
    why it is blank (null when set). The same matcher as an import."""

    values: list[str]
    value: str | None
    rule: int | None
    reason: str | None


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


# ------------------------------------------------------------------ job package (protocol 2)

ContextKind = Literal["skill", "plan", "instructions", "agent", "file"]


class ManifestTicket(ApiModel):
    key: str
    title: str


class ManifestWorkspace(ApiModel):
    """`protect`: folders kept out of git besides the declared files (the ticket skill dir; the
    worker always adds `.agentos/`). `blocks`: every block name in `context`, all of which the
    pre-push marker scan looks for."""

    mode: WorkspaceMode
    repo_url: str | None
    base_branch: str | None
    branch: str | None
    protect: list[str]
    blocks: list[str]


class ContextEntry(ApiModel):
    """A context file. `name`: the skill, block, agent or file it belongs to. `block`: the block
    name in block mode (`content` is then the block's). `access: write` entries are synced back."""

    path: str
    kind: ContextKind
    name: str
    mode: Mode
    block: str | None
    access: Literal["write", "read"]
    content: str


class NoteEntry(ApiModel):
    """An append-only log (`learnings.log`, `swarm.log`), shipped and synced like write context."""

    path: str
    name: str
    content: str


class SyncBack(ApiModel):
    """The workspace folder the agent returns files through, as one zip of at most `max_bytes`."""

    dir: str
    max_bytes: int


class ManifestInvocation(ApiModel):
    """Another call of a loop stage's agent: a later round, or a verdict repair (request 24). The
    prompt is the `stdin`, or `argv[prompt_arg]` when the runtime takes it as an argument (then
    `stdin` is null). The worker appends its text to the prompt (the round's feedback, or why the
    verdict was invalid), then `tail`, and changes nothing else."""

    argv: list[str]
    stdin: str | None
    prompt_arg: int | None
    tail: str


class ManifestStage(ApiModel):
    """`argv` / `stdin` (agent) or `command` (shell): the stage's call, in round 1 of a loop.

    Loop block stages only (otherwise the defaults): `check` = the stage decides the round (a
    shell stage, or an agent check). Agent stages: `rounds` = the calls for rounds 2 to
    `max_rounds`, in order. Agent checks: `verdict_from` = where the verdict is read,
    `verdict_repairs` = at most this many repairs per round, and `repairs` = one repair call per
    round (index 0 = round 1), resuming that round's session; empty = no repair (the runtime has
    no sessions)."""

    id: str
    name: str
    kind: StageKind
    timeout_s: int
    argv: list[str] | None = None
    stdin: str | None = None
    command: str | None = None
    check: bool = False
    rounds: list[ManifestInvocation] = Field(default_factory=list)
    repairs: list[ManifestInvocation] = Field(default_factory=list)
    verdict_from: VerdictFrom | None = None
    verdict_repairs: int = 0


class ManifestLoop(ApiModel):
    """A loop block over the package's `stages` (ids, `start` at or before `end`)."""

    start: str
    end: str
    max_rounds: int


class ManifestLimits(ApiModel):
    max_file_bytes: int
    max_line_bytes: int


class ManifestJob(ApiModel):
    """`id`: `<run_id>-<segment>-<16 hex>`. The worker publishes nothing after `not_after`."""

    id: str
    run_id: int
    segment: int
    segments: int
    budget_s: int
    not_after: AwareDatetime


class ManifestJournal(ApiModel):
    first_seq: int  # the seq of the job's first journal record


class ManifestReport(ApiModel):
    mode: Literal["live", "file"]
    url: str | None  # live: the server's base URL


class JobPackage(ApiModel):
    """What a worker receives for its job. `env`: non-secret stage variables (workspace-relative
    paths); the worker adds `AGENTOS_RUN_ID` and `AGENTOS_TICKET_KEY` itself."""

    protocol: Literal[2]
    job: ManifestJob
    journal: ManifestJournal
    report: ManifestReport
    run_id: int
    ticket: ManifestTicket
    workspace: ManifestWorkspace
    context: list[ContextEntry]
    notes: list[NoteEntry]
    sync_back: SyncBack
    env: dict[str, str]
    stages: list[ManifestStage]
    limits: ManifestLimits
    # Loop blocks over `stages`, in order and not overlapping (request 24).
    loops: list[ManifestLoop] = Field(default_factory=list)
    # Where round 1 starts (the run's `from_stage`); null = the first stage. Stages before it are
    # the start of the loop block it is in: they run from round 2 on.
    start_stage: str | None = None
    # Lines the worker emits as `warning` events when it starts (e.g. a loop that is ignored
    # because its enabled stages have no check).
    warnings: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ runs & run events


class DispatchRequest(ApiModel):
    """`target: "client"` queues the run for the caller's own client: only the ticket's owner may
    (403 `forbidden` otherwise, and for an unowned ticket). It waits until a client of theirs
    leases it; cancel works as for any queued run."""

    from_stage: str | None = None
    target: DispatchTarget = "server"


Verdict = Literal["pass", "fail"]
# Where a verdict came from: the CLI's structured output, the verdict file, or a shell check's
# exit code (0 = pass).
VerdictSource = Literal["structured_output", "file", "exit_code"]
VERDICT_NOTES_MAX = 20_000
# Where a check on the `file` channel writes its verdict, in the workspace. `.agentos/` belongs to
# AgentOS: the worker deletes the file before each check call and never commits the directory.
VERDICT_FILE = ".agentos/verdict.json"
# The verdict shape, sent to a CLI as `{verdict_schema}` and shown in a check's prompt. The worker
# checks the same by hand, plus: `notes` at most VERDICT_NOTES_MAX characters, and not blank on a
# "fail" (left out here: CLIs' structured output supports few keywords).
VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail"]},
        "notes": {"type": "string"},
    },
    "required": ["verdict", "notes"],
    "additionalProperties": False,
}


class StageRunInfo(ApiModel):
    """One execution of a stage in a run, in execution order (`idx`). A stage in a loop block has
    one per round it ran (`round`; 1 outside loops). `verdict`: a check's verdict in that round
    (null for other stages, and until it is known). A check that gave a valid verdict
    `succeeded`, even a "fail", except a "fail" in the block's last round: that one `failed` (the
    loop is lost there; the run's error says so)."""

    stage_id: str
    name: str
    idx: int
    round: int
    status: StageRunStatus
    verdict: Verdict | None
    exit_code: int | None
    reason: str | None
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None


class SyncBackInfo(ApiModel):
    files: int
    bytes: int


class RunDetail(RunSummary):
    # The manifest (can be MiBs) is served separately: GET /api/runs/{id}/manifest.
    stage_runs: list[StageRunInfo]
    # The zip the worker returned from sync_back; download: GET /api/runs/{id}/sync-back.
    sync_back: SyncBackInfo | None


class StageStartedData(ApiModel):
    # The stage's round in its loop block (1 outside loops).
    round: int = Field(default=1, ge=1)


class LogData(ApiModel):
    stream: Literal["stdout", "stderr", "system"]
    text: str
    # True when the line exceeded max_line_bytes and was cut (it can no longer be parsed as JSON).
    truncated: bool = False


class StageFinishedData(ApiModel):
    status: StageOutcome
    exit_code: int | None
    reason: str | None
    round: int = Field(default=1, ge=1)


class WarningData(ApiModel):
    message: str


class VerdictData(ApiModel):
    """A check's verdict in a round (sent before its `stage_finished`). `repairs`: how many
    repairs it took (0 = valid at once)."""

    round: int = Field(ge=1)
    verdict: Verdict
    notes: str = Field(max_length=VERDICT_NOTES_MAX)
    source: VerdictSource
    repairs: int = Field(ge=0)


class VerdictRepairData(ApiModel):
    """The check's verdict was invalid (`error` says why), so it is asked again: repair
    `attempt` (1-based) of the round."""

    round: int = Field(ge=1)
    attempt: int = Field(ge=1)
    error: str = Field(max_length=2000)


class _RunEventBase(ApiModel):
    seq: int = Field(ge=1)
    ts: AwareDatetime
    stage_id: str | None


class StageStartedEvent(_RunEventBase):
    type: Literal["stage_started"]
    data: StageStartedData


class StageFinishedEvent(_RunEventBase):
    type: Literal["stage_finished"]
    data: StageFinishedData


class LogEvent(_RunEventBase):
    type: Literal["log"]
    data: LogData


class WarningEvent(_RunEventBase):
    type: Literal["warning"]
    data: WarningData


class VerdictEvent(_RunEventBase):
    type: Literal["verdict"]
    data: VerdictData


class VerdictRepairEvent(_RunEventBase):
    type: Literal["verdict_repair"]
    data: VerdictRepairData


# Discriminated on `type`; the same shape is sent by the worker and served to the UI.
RunEvent = Annotated[
    StageStartedEvent
    | StageFinishedEvent
    | LogEvent
    | WarningEvent
    | VerdictEvent
    | VerdictRepairEvent,
    Field(discriminator="type"),
]


# ------------------------------------------------------------------ worker API


class SyncFile(ApiModel):
    path: str
    content: str | None
    mode: Mode


class ResultData(ApiModel):
    """A `result` record's data: how the job ended."""

    status: WorkerFinalStatus
    failed_stage: str | None = None
    error: str | None = None
    # The PR for the run's `branch`, as last reported by a worker (repo mode, when one exists).
    # The server stores it only if it passes `validate_pr_url`; an invalid one is ignored.
    pr_url: str | None = Field(default=None, max_length=500)


class JournalRecord(ApiModel):
    """One record of a run's journal; ingest checks each type's `data` and consumes a bad one."""

    seq: int = Field(ge=1)
    ts: AwareDatetime
    job: str
    type: str
    stage_id: str | None = None
    data: dict[str, Any]


class JournalRequest(ApiModel):
    records: list[JournalRecord] = Field(max_length=500)


class JournalResponse(ApiModel):
    """`ack`: the highest applied seq; `rejected`: this batch's seqs consumed without applying."""

    ack: int
    cancel: bool
    rejected: list[int]


class StartedData(ApiModel):
    """The first record of a job: it binds the posting instance (`X-AgentOS-Instance`)."""

    protocol: Literal[2]
    worker_sha256: str


class SyncData(ApiModel):
    files: list[SyncFile]


class SyncBackData(ApiModel):
    """Describes the zip uploaded to `PUT /api/worker/jobs/{id}/sync-back` before it."""

    sha256: str
    bytes: int = Field(ge=1)
    files: int = Field(ge=1)


class PushedData(ApiModel):
    branch: str
    head: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    commits: int = Field(ge=0)
    method: Literal["git", "api"]


class LeaseRequest(ApiModel):
    """Waits up to `wait_s` for a job (`take: false`: a heartbeat, answered at once). `alive`:
    the jobs the client's workers still run. `instance`: the client process's random id, the same
    on every lease it sends; only the instance that last listed a job can end it by leaving it
    out of `alive`."""

    instance: str = Field(min_length=16, max_length=128)
    wait_s: int = Field(default=25, ge=0, le=25)
    take: bool = True
    alive: list[str] = Field(default_factory=list, max_length=100)


class LeasedJob(ApiModel):
    """A job handed to a client: its worker runs at `server_url`, `job_token` in AGENTOS_TOKEN."""

    job_id: str
    run_id: int
    server_url: str
    job_token: str


class LeaseResponse(ApiModel):
    """`job: null` = nothing queued within `wait_s`. `protocol`, `worker_sha256`: the server's."""

    job: LeasedJob | None
    protocol: Literal[2]
    worker_sha256: str
