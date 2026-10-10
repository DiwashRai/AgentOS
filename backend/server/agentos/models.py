"""API and domain models. This module is the contract between server, worker and web.

Pure: no web, db or http imports (enforced by import-linter).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["viewer", "writer", "admin"]
TicketType = Literal["feature", "bug", "improvement", "task"]
Priority = Literal["high", "normal", "low"]
TicketState = Literal["open", "done", "closed"]
WorkspaceMode = Literal["repo", "none"]
Lane = Literal["inbox", "running", "review", "attention", "done"]
RunStatus = Literal["queued", "dispatched", "running", "succeeded", "gated", "failed", "cancelled"]
StageRunStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]
RailState = Literal["pending", "done", "current", "failed", "stale"]
Mode = Literal["replace", "block"]
StageKind = Literal["agent", "shell"]
SourceName = Literal["jira", "github", "todoist"]
RunEventType = Literal["stage_started", "stage_finished", "log", "warning"]
WorkerFinalStatus = Literal["completed", "failed", "cancelled"]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ errors


class ErrorDetail(ApiModel):
    code: str
    message: str


class ErrorBody(ApiModel):
    error: ErrorDetail


# ------------------------------------------------------------------ users & auth


class Me(ApiModel):
    id: int
    display_name: str
    email: str | None
    avatar_url: str | None
    role: Role


class User(Me):
    disabled: bool
    created_at: datetime
    last_login_at: datetime | None


class UserPatch(ApiModel):
    role: Role | None = None
    disabled: bool | None = None


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


class Stage(ApiModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    name: str = Field(min_length=1, max_length=60)
    kind: StageKind
    enabled: bool = True
    gate_after: bool = False
    requires_repo: bool = False
    prompt: str = Field(default="", max_length=65536)
    runtime: str | None = None
    agent: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._:/-]{1,100}$")
    model: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._:/-]{1,100}$")
    command: str = ""
    timeout_s: int = Field(default=3600, ge=60, le=21600)


class Pipeline(ApiModel):
    stages: list[Stage] = Field(min_length=1, max_length=20)
    library_sets: list[int] = Field(default_factory=list)


class Template(ApiModel):
    pipeline: Pipeline
    scaffold: list[FileEntry]
    version: int


class TemplateUpdate(ApiModel):
    pipeline: Pipeline
    scaffold: list[FileEntry]
    base_version: int


# ------------------------------------------------------------------ tickets


class SourceLink(ApiModel):
    source: SourceName
    external_id: str
    url: str


class RailStep(ApiModel):
    stage_id: str
    name: str
    state: RailState


class TaskProgress(ApiModel):
    done: int
    total: int


class RunSummary(ApiModel):
    id: int
    ticket_key: str
    status: RunStatus
    from_stage: str
    stage_ids: list[str]
    created_by: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    launch_ref: str | None


class TicketSummary(ApiModel):
    key: str
    title: str
    type: TicketType
    priority: Priority
    labels: list[str]
    state: TicketState
    lane: Lane
    rail: list[RailStep]
    tasks: TaskProgress | None
    chat_count: int
    latest_run: RunSummary | None
    source: SourceLink | None
    updated_at: datetime


class TicketDetail(TicketSummary):
    description: str
    slug: str
    skill_name: str
    workspace_mode: WorkspaceMode
    repo_url: str | None
    base_branch: str | None
    branch: str | None
    pipeline: Pipeline
    pipeline_version: int
    next_stage: str | None
    active_run_id: int | None
    created_at: datetime


class TicketCreate(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    type: TicketType = "task"
    priority: Priority = "normal"
    labels: list[str] = Field(default_factory=list)
    workspace_mode: WorkspaceMode = "repo"
    repo_url: str | None = None
    base_branch: str | None = None


class TicketPatch(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    type: TicketType | None = None
    priority: Priority | None = None
    labels: list[str] | None = None
    state: TicketState | None = None
    repo_url: str | None = None
    base_branch: str | None = None


class PipelineUpdate(ApiModel):
    pipeline: Pipeline
    base_version: int


# ------------------------------------------------------------------ files


class FileInfo(ApiModel):
    path: str
    mode: Mode
    version: int
    size: int
    author: str | None
    updated_at: datetime


class FileContent(FileInfo):
    content: str


class FileVersionInfo(ApiModel):
    version: int
    deleted: bool
    mode: Mode
    size: int
    author: str | None
    created_at: datetime


class FileWrite(ApiModel):
    content: str
    mode: Mode = "replace"
    base_version: int = Field(ge=0, description="0 to create; otherwise the current version")


# ------------------------------------------------------------------ chat


class ChatAuthor(ApiModel):
    id: int
    display_name: str
    avatar_url: str | None


class ChatMessage(ApiModel):
    id: int
    ticket_key: str
    author: ChatAuthor
    body: str
    created_at: datetime


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


# ------------------------------------------------------------------ runs


class DispatchRequest(ApiModel):
    from_stage: str | None = None


class StageRunInfo(ApiModel):
    stage_id: str
    name: str
    idx: int
    status: StageRunStatus
    exit_code: int | None
    reason: str | None
    started_at: datetime | None
    finished_at: datetime | None


class RunDetail(RunSummary):
    stage_runs: list[StageRunInfo]
    manifest: Manifest | None


class RunEvent(ApiModel):
    seq: int
    ts: datetime
    stage_id: str | None
    type: RunEventType
    data: dict[str, Any]


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


# ------------------------------------------------------------------ live updates (SSE)


class StreamEvent(ApiModel):
    """Payload of each `data:` line on GET /api/stream. Notifications only: refetch on receipt."""

    type: Literal["ticket", "chat", "run"]
    key: str
    id: int | None = None


class Health(ApiModel):
    status: Literal["ok"]
