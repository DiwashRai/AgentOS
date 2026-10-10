"""In-memory stub data so the web app can be built against the real contract.

STUB: routes call into this module until the backend replaces them with real services.
Shapes must always match `agentos.models`. Derived fields (attention, phase, phase_track, now,
next_stage) are computed by `_derive` using the SPEC §13 rules, so the real backend should
produce the same values. Delete this module once nothing imports it.
"""

from __future__ import annotations

import re
import threading
from datetime import UTC, datetime, timedelta
from typing import get_args

from agentos.errors import ApiError
from agentos.models import (
    ChatMessage,
    EmptyData,
    FileContent,
    FileEntry,
    FileInfo,
    FileVersionInfo,
    LogData,
    LogEvent,
    Manifest,
    ManifestFile,
    ManifestLimits,
    ManifestStage,
    ManifestTicket,
    ManifestWorkspace,
    Me,
    Mode,
    NowInfo,
    Person,
    Phase,
    PhaseState,
    PhaseStep,
    Pipeline,
    RailState,
    RailStep,
    RunDetail,
    RunEvent,
    RunStatus,
    RunSummary,
    SourceLink,
    Stage,
    StageFinishedData,
    StageFinishedEvent,
    StageRunInfo,
    StageRunStatus,
    StageStartedEvent,
    TaskProgress,
    Template,
    TicketCreate,
    TicketDetail,
    TicketPatch,
    TicketSummary,
    User,
)

NOW = datetime.now(UTC)
PHASES: tuple[Phase, ...] = get_args(Phase)
ACTIVE: tuple[RunStatus, ...] = ("queued", "dispatched", "running")


def ago(**delta: float) -> datetime:
    return NOW - timedelta(**delta)


# ------------------------------------------------------------------ template

PROMPTS = {
    "plan": (
        "You are planning {{ticket.key}}: {{ticket.title}}. Read {{paths.skill_md}} and the "
        "repository. Do not change code. Write a step-by-step plan to {{paths.tasks_md}} as a "
        "markdown checklist (`- [ ] ...`). Append non-obvious findings to {{paths.learnings}}."
    ),
    "implement": (
        "Implement {{ticket.key}}. Objective: {{paths.skill_md}}. Plan: {{paths.tasks_md}}. Work "
        "through unchecked items in order and tick each (`- [x]`) as soon as it is done. Run the "
        "relevant tests. Append learnings to {{paths.learnings}}."
    ),
    "review": (
        "Review the work for {{ticket.key}}{% if ticket.branch %} on this branch against "
        "origin/{{ticket.base_branch}}{% endif %}, checking it against {{paths.skill_md}} and "
        "{{paths.tasks_md}}. Fix clear defects directly. Write remaining findings to "
        "{{paths.skill_dir}}/REVIEW.md."
    ),
    "open-pr": (
        "Open a pull request from {{ticket.branch}} into {{ticket.base_branch}}, or update the "
        "existing one, using the gh CLI. Title: `{{ticket.key}}: {{ticket.title}}`. Body: a "
        "summary of the change plus the completed items from {{paths.tasks_md}}."
    ),
}


def default_pipeline(*, lint: bool = False) -> Pipeline:
    return Pipeline(
        stages=[
            Stage(
                id="plan",
                name="Plan",
                kind="agent",
                phase="plan",
                gate_after=True,
                prompt=PROMPTS["plan"],
            ),
            Stage(
                id="implement",
                name="Implement",
                kind="agent",
                phase="build",
                prompt=PROMPTS["implement"],
            ),
            Stage(
                id="lint",
                name="Lint",
                kind="shell",
                phase="verify",
                enabled=lint,
                command="npm run lint" if lint else "",
            ),
            Stage(
                id="review",
                name="Review",
                kind="agent",
                phase="review",
                prompt=PROMPTS["review"],
            ),
            Stage(
                id="open-pr",
                name="Open PR",
                kind="agent",
                phase="review",
                requires_repo=True,
                prompt=PROMPTS["open-pr"],
            ),
        ]
    )


DEFAULT_SCAFFOLD = [
    FileEntry(
        path="{{paths.skill_md}}",
        content=(
            "---\nname: {{skill.name}}\ndescription: {{ ('Objective and working context for ' ~ "
            "ticket.key ~ ' (' ~ ticket.title ~ '). Use when working on ' ~ ticket.key ~ '.') "
            "| tojson }}\n---\n# {{ticket.key}}: {{ticket.title}}\n\n## Objective\n"
            "{{ticket.description}}\n"
        ),
    ),
    FileEntry(path="{{paths.tasks_md}}", content="# Tasks\n"),
    FileEntry(path="{{paths.learnings}}", content=""),
    FileEntry(path="{{paths.swarm}}", content=""),
    FileEntry(
        path="AGENTS.md",
        mode="block",
        content=(
            "Working on {{ticket.key}}. Read {{paths.skill_md}} first; the plan is "
            "{{paths.tasks_md}}. Do not run git commit or push; AgentOS commits and pushes "
            "after each stage."
        ),
    ),
    FileEntry(path="CLAUDE.md", mode="block", content="@AGENTS.md"),
]

# ------------------------------------------------------------------ people

ME = Me(id=1, display_name="Dev", email="dev@localhost", avatar_url=None, role="admin")
USERS = [
    User(**ME.model_dump(), disabled=False, created_at=ago(days=3), last_login_at=ago(minutes=5)),
    User(
        id=2,
        display_name="Sam Lee",
        email="sam@example.com",
        avatar_url=None,
        role="writer",
        disabled=False,
        created_at=ago(days=2),
        last_login_at=ago(hours=3),
    ),
    User(
        id=3,
        display_name="Priya Shah",
        email="priya@example.com",
        avatar_url=None,
        role="viewer",
        disabled=False,
        created_at=ago(hours=20),
        last_login_at=ago(hours=20),
    ),
]


def person(user_id: int) -> Person:
    for u in USERS:
        if u.id == user_id:
            return Person(id=u.id, display_name=u.display_name, avatar_url=u.avatar_url)
    raise ApiError(422, "user_not_found", f"No user {user_id}")


ME_P = person(1)
SAM_P = person(2)


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:40].strip("-") or "ticket"


def skill_dir(key: str, slug: str) -> str:
    return f".claude/skills/context-{key.lower()}-{slug}"


def repo_name(url: str | None) -> str | None:
    if not url:
        return None
    return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or None


# ------------------------------------------------------------------ store


class _Version:
    def __init__(
        self, content: str | None, mode: Mode, author: str | None, created_at: datetime
    ) -> None:
        self.content = content
        self.mode = mode
        self.author = author
        self.created_at = created_at


class StubStore:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.tickets: dict[str, TicketDetail] = {}
        self.files: dict[str, dict[str, list[_Version]]] = {}
        self.chat: dict[str, list[ChatMessage]] = {}
        self.runs: dict[int, RunDetail] = {}
        self.events: dict[int, list[RunEvent]] = {}
        self.template = Template(pipeline=default_pipeline(), scaffold=DEFAULT_SCAFFOLD, version=1)
        self.next_ticket = 8
        self.next_run = 1
        self.next_chat = 1
        self._seed()

    # ---------------------------------------------------------- derivation (SPEC §13 rules)

    def _derive(self, t: TicketDetail, viewer_id: int) -> TicketDetail:
        run = self.runs.get(t.latest_run.id) if t.latest_run else None
        status = run.status if run else None
        if t.state in ("done", "closed"):
            t.attention = "done"
        elif status in ACTIVE:
            t.attention = "moving"
        elif status in ("failed", "cancelled"):
            t.attention = "stuck"
        elif status in ("gated", "succeeded"):
            mine = t.owner is None or t.owner.id == viewer_id
            t.attention = "needs_you" if mine else "waiting"
        else:
            t.attention = "up_next"

        focus = next((r for r in t.rail if r.state in ("current", "failed")), None)
        done_steps = [r for r in t.rail if r.state == "done"]
        if t.state == "done":
            t.phase = "done"
        elif run is None:
            t.phase = "inbox"
        elif focus is not None:
            t.phase = focus.phase
        else:
            t.phase = done_steps[-1].phase if done_steps else "inbox"

        if t.state == "done":
            t.phase_since = t.updated_at
        elif run is None:
            t.phase_since = t.created_at
        elif status in ACTIVE:
            t.phase_since = run.started_at or run.created_at
        else:
            t.phase_since = run.finished_at or run.created_at

        t.phase_track = [PhaseStep(phase=p, state=self._phase_state(t, p, run)) for p in PHASES]
        t.next_stage = self._next_stage(t, run)
        t.now = self._now(t, run)
        t.repo_name = repo_name(t.repo_url)
        return t

    def _phase_state(self, t: TicketDetail, phase: Phase, run: RunDetail | None) -> PhaseState:
        if phase == "inbox":
            return "current" if run is None and t.state == "open" else "done"
        if phase == "done":
            return "done" if t.state == "done" else "pending"
        states = [r.state for r in t.rail if r.phase == phase]
        if not states:
            return "skipped"
        if t.state == "done":
            return "done"
        if "failed" in states:
            return "failed"
        if all(s == "done" for s in states):
            return "done"
        if "current" in states or t.phase == phase:
            return "current"
        if "stale" in states:
            return "stale"
        return "pending"

    def _next_stage(self, t: TicketDetail, run: RunDetail | None) -> str | None:
        enabled = [s.id for s in t.pipeline.stages if s.enabled]
        if not enabled:
            return None
        if run is None:
            return enabled[0]
        if run.status in ("failed", "cancelled"):
            anchor = next(
                (sr.stage_id for sr in run.stage_runs if sr.status in ("failed", "cancelled")),
                run.stage_ids[-1],
            )
            return anchor if anchor in enabled else enabled[0]
        if run.status == "gated":
            last = run.stage_ids[-1]
            if last in enabled and enabled.index(last) + 1 < len(enabled):
                return enabled[enabled.index(last) + 1]
            return enabled[0]
        return None

    def _now(self, t: TicketDetail, run: RunDetail | None) -> NowInfo:
        run_id = run.id if run else None
        names = {s.id: s.name for s in t.pipeline.stages}
        if t.attention == "done":
            return NowInfo(actor=None, text="done", run_id=run_id, progress=t.tasks)
        if t.attention == "up_next":
            return NowInfo(
                actor=None, text="add context, then dispatch", run_id=None, progress=None
            )
        if run is None:  # unreachable: every other attention group has a run
            return NowInfo(actor=None, text="", run_id=None, progress=None)
        if t.attention == "moving":
            if run.status != "running":
                return NowInfo(actor=None, text="queued for a runner", run_id=run_id, progress=None)
            stage = next((sr for sr in run.stage_runs if sr.status == "running"), None)
            actor = stage.name if stage else None
            return NowInfo(
                actor=actor, text=self._next_task(t) or "working", run_id=run_id, progress=t.tasks
            )
        if t.attention == "stuck":
            failed = next(
                (sr for sr in run.stage_runs if sr.status in ("failed", "cancelled")), None
            )
            what = f"{failed.name} {failed.status}" if failed else f"run {run.status}"
            return NowInfo(
                actor=None, text=f"{what}: retry or adjust", run_id=run_id, progress=None
            )
        text = (
            f"review {names[run.stage_ids[-1]].lower()} and continue"
            if run.status == "gated"
            else "review the result and mark done"
        )
        actor = t.owner.display_name if t.attention == "waiting" and t.owner else None
        return NowInfo(actor=actor, text=text, run_id=run_id, progress=t.tasks)

    def _next_task(self, t: TicketDetail) -> str | None:
        path = f"{skill_dir(t.key, t.slug)}/TASKS.md"
        content = self.files.get(t.key, {}).get(path, [None])[-1]
        if content is None or content.content is None:
            return None
        match = re.search(r"^\s*- \[ \] (.+)$", content.content, re.M)
        return match.group(1).strip() if match else None

    def summary(self, t: TicketDetail) -> TicketSummary:
        self._derive(t, ME.id)
        return TicketSummary.model_validate(t.model_dump(include=set(TicketSummary.model_fields)))

    # ---------------------------------------------------------- seeding helpers

    def _add_ticket(
        self,
        key: str,
        title: str,
        *,
        ticket_type: str = "feature",
        priority: str = "normal",
        labels: list[str] | None = None,
        stage_states: list[RailState] | None = None,
        tasks: list[tuple[bool, str]] | None = None,
        updated: datetime | None = None,
        source: SourceLink | None = None,
        state: str = "open",
        owner: Person | None = None,
        pipeline: Pipeline | None = None,
    ) -> TicketDetail:
        pipeline = pipeline or default_pipeline()
        enabled = [s for s in pipeline.stages if s.enabled]
        states = stage_states or ["pending"] * len(enabled)
        slug = slugify(title)
        repo_url = "https://github.com/DiwashRai/AgentOS.git"
        ticket = TicketDetail.model_validate(
            {
                "key": key,
                "title": title,
                "type": ticket_type,
                "priority": priority,
                "labels": labels or [],
                "state": state,
                "owner": owner,
                "attention": "up_next",
                "phase": "inbox",
                "phase_since": None,
                "phase_track": [],
                "rail": [
                    RailStep(stage_id=s.id, name=s.name, phase=s.phase, state=st)
                    for s, st in zip(enabled, states, strict=True)
                ],
                "now": NowInfo(actor=None, text="", run_id=None, progress=None),
                "tasks": None,
                "chat_count": 0,
                "latest_run": None,
                "source": source,
                "repo_name": repo_name(repo_url),
                "updated_at": updated or ago(hours=1),
                "description": f"{title}.\n\nMake this a dependable part of the everyday flow.",
                "slug": slug,
                "skill_name": f"context-{key.lower()}-{slug}",
                "workspace_mode": "repo",
                "repo_url": repo_url,
                "base_branch": "main",
                "branch": f"agent/{key}",
                "pipeline": pipeline,
                "pipeline_version": 1,
                "next_stage": None,
                "active_run_id": None,
                "created_at": ago(days=1),
            }
        )
        self.tickets[key] = ticket
        self._scaffold_files(ticket, tasks or [])
        return ticket

    def _scaffold_files(self, t: TicketDetail, tasks: list[tuple[bool, str]]) -> None:
        d = skill_dir(t.key, t.slug)
        lines = ["# Tasks", ""] + [f"- [{'x' if done else ' '}] {text}" for done, text in tasks]
        files = {
            f"{d}/SKILL.md": (
                f'---\nname: {t.skill_name}\ndescription: "Objective and working context for '
                f'{t.key}."\n---\n# {t.key}: {t.title}\n\n## Objective\n{t.description}\n',
                "replace",
            ),
            f"{d}/TASKS.md": ("\n".join(lines) + "\n", "replace"),
            f"{d}/learnings.log": ("", "replace"),
            f"{d}/swarm.log": ("", "replace"),
            "AGENTS.md": (f"Working on {t.key}. Read {d}/SKILL.md first.", "block"),
            "CLAUDE.md": ("@AGENTS.md", "block"),
        }
        self.files[t.key] = {
            path: [_Version(content, mode, None, t.created_at)]  # type: ignore[arg-type]
            for path, (content, mode) in files.items()
        }
        if tasks:
            t.tasks = TaskProgress(done=sum(1 for d, _ in tasks if d), total=len(tasks))

    def _add_run(
        self,
        ticket: TicketDetail,
        status: RunStatus,
        stages: list[tuple[str, StageRunStatus]],
        *,
        started: datetime,
        finished: datetime | None = None,
        error: str | None = None,
    ) -> RunDetail:
        run_id = self.next_run
        self.next_run += 1
        names = {s.id: s.name for s in ticket.pipeline.stages}
        run = RunDetail(
            id=run_id,
            ticket_key=ticket.key,
            status=status,
            from_stage=stages[0][0],
            stage_ids=[s for s, _ in stages],
            created_by=ME.display_name,
            created_at=started,
            started_at=started,
            finished_at=finished,
            cancel_requested_at=None,
            error=error,
            launch_ref=None,
            stage_runs=[
                StageRunInfo(
                    stage_id=sid,
                    name=names[sid],
                    idx=i,
                    status=st,
                    exit_code=0 if st == "succeeded" else (1 if st == "failed" else None),
                    reason=None,
                    started_at=None if st == "pending" else started + timedelta(minutes=4 * i),
                    finished_at=(
                        started + timedelta(minutes=4 * i + 3)
                        if st in ("succeeded", "failed")
                        else None
                    ),
                )
                for i, (sid, st) in enumerate(stages)
            ],
            manifest=self._manifest(run_id, ticket, [s for s, _ in stages]),
        )
        self.runs[run_id] = run
        self.events[run_id] = self._events(run)
        self._sync_latest(run)
        ticket.active_run_id = run_id if status in ACTIVE else None
        return run

    def _manifest(self, run_id: int, t: TicketDetail, stage_ids: list[str]) -> Manifest:
        stages = {s.id: s for s in t.pipeline.stages}
        return Manifest(
            run_id=run_id,
            ticket=ManifestTicket(key=t.key, slug=t.slug, title=t.title),
            workspace=ManifestWorkspace(
                mode=t.workspace_mode,
                repo_url=t.repo_url,
                base_branch=t.base_branch,
                branch=t.branch,
            ),
            files=[
                ManifestFile(path=p, content=v[-1].content or "", mode=v[-1].mode, sync=True)
                for p, v in self.files[t.key].items()
                if v[-1].content is not None
            ],
            sync_dirs=[skill_dir(t.key, t.slug)],
            stages=[
                ManifestStage(
                    id=sid,
                    name=stages[sid].name,
                    kind=stages[sid].kind,
                    timeout_s=stages[sid].timeout_s,
                    argv=["claude", "-p", "--output-format", "stream-json", "--verbose"],
                    stdin=stages[sid].prompt.replace("{{ticket.key}}", t.key),
                )
                for sid in stage_ids
            ],
            limits=ManifestLimits(max_file_bytes=1_048_576, max_line_bytes=8192),
        )

    def _events(self, run: RunDetail) -> list[RunEvent]:
        events: list[RunEvent] = []
        seq = 0
        base = run.started_at or run.created_at
        for sr in run.stage_runs:
            if sr.status == "pending":
                continue
            seq += 1
            events.append(
                StageStartedEvent(
                    seq=seq, ts=base, stage_id=sr.stage_id, type="stage_started", data=EmptyData()
                )
            )
            for i, text in enumerate(
                [
                    f"[{sr.stage_id}] reading SKILL.md and TASKS.md",
                    f"[{sr.stage_id}] exploring backend/server/agentos",
                    f"[{sr.stage_id}] running tests: 42 passed",
                    f"[{sr.stage_id}] updating TASKS.md",
                ]
            ):
                seq += 1
                events.append(
                    LogEvent(
                        seq=seq,
                        ts=base + timedelta(seconds=20 * (i + 1)),
                        stage_id=sr.stage_id,
                        type="log",
                        data=LogData(stream="stdout", text=text),
                    )
                )
            if sr.status in ("succeeded", "failed", "cancelled"):
                seq += 1
                events.append(
                    StageFinishedEvent(
                        seq=seq,
                        ts=sr.finished_at or base,
                        stage_id=sr.stage_id,
                        type="stage_finished",
                        data=StageFinishedData(
                            status=sr.status, exit_code=sr.exit_code, reason=sr.reason
                        ),
                    )
                )
        return events

    def _chat(self, key: str, author: Person, body: str, at: datetime) -> None:
        msg = ChatMessage(
            id=self.next_chat, ticket_key=key, author=author, body=body, created_at=at
        )
        self.next_chat += 1
        self.chat.setdefault(key, []).append(msg)
        self.tickets[key].chat_count += 1

    def _seed(self) -> None:
        plan_tasks = [
            (False, "Add a diff view to the context tab"),
            (False, "Show the previous version side by side"),
            (False, "Highlight agent-authored versions"),
            (False, "Keyboard shortcut to step through versions"),
        ]
        self._add_ticket(
            "AOS-1",
            "Show run progress on the board",
            priority="high",
            labels=["board"],
            owner=ME_P,
            updated=ago(hours=3),
        )
        self._add_ticket(
            "WORK-11234",
            "Parse the customer settlement message format",
            ticket_type="task",
            labels=["integration"],
            source=SourceLink(
                source="jira",
                external_id="WORK-11234",
                url="https://example.atlassian.net/browse/WORK-11234",
            ),
            updated=ago(hours=1),
        )
        t3 = self._add_ticket(
            "AOS-3",
            "Handle interrupted runs gracefully",
            ticket_type="bug",
            priority="high",
            labels=["runs"],
            owner=ME_P,
            pipeline=default_pipeline(lint=True),
            stage_states=["done", "current", "pending", "pending", "pending"],
            tasks=[
                (True, "Reproduce a worker losing its heartbeat"),
                (True, "Mark runs failed after 90s of silence"),
                (False, "Run API test suite"),
                (False, "Resume-safe claim retries"),
                (False, "Document the reaper rules"),
            ],
            updated=ago(minutes=2),
        )
        self._add_run(t3, "running", [("implement", "running")], started=ago(minutes=6))
        t4 = self._add_ticket(
            "AOS-4",
            "Make context changes easy to compare",
            labels=["context"],
            owner=ME_P,
            stage_states=["current", "pending", "pending", "pending"],
            tasks=plan_tasks,
            updated=ago(minutes=40),
        )
        self._add_run(
            t4, "gated", [("plan", "succeeded")], started=ago(minutes=55), finished=ago(minutes=40)
        )
        t5 = self._add_ticket(
            "AOS-5",
            "Keyboard navigation for the board",
            ticket_type="improvement",
            labels=["a11y"],
            stage_states=["done", "done", "failed", "pending"],
            tasks=[(True, f"Step {i + 1}") for i in range(6)],
            updated=ago(hours=2),
        )
        self._add_run(
            t5,
            "failed",
            [("implement", "succeeded"), ("review", "failed")],
            started=ago(hours=2, minutes=20),
            finished=ago(hours=2),
            error="stage review exited with code 1",
        )
        t6 = self._add_ticket(
            "AOS-6",
            "Retry from an earlier stage",
            labels=["runs"],
            owner=SAM_P,
            stage_states=["current", "stale", "stale", "pending"],
            tasks=[(True, "Re-scope retry states"), (False, "Wire retry button"), (False, "Tests")],
            updated=ago(hours=5),
        )
        self._add_run(
            t6,
            "gated",
            [("plan", "succeeded")],
            started=ago(hours=5, minutes=10),
            finished=ago(hours=5),
        )
        t7 = self._add_ticket(
            "AOS-7",
            "Pin the claude CLI version",
            ticket_type="task",
            priority="low",
            owner=ME_P,
            stage_states=["done", "done", "done", "done"],
            tasks=[(True, "Record version"), (True, "Add to agentos.toml"), (True, "Smoke test")],
            state="done",
            updated=ago(days=1),
        )
        self._add_run(
            t7,
            "succeeded",
            [("implement", "succeeded"), ("review", "succeeded"), ("open-pr", "succeeded")],
            started=ago(days=1, hours=1),
            finished=ago(days=1),
        )
        self._chat("AOS-3", SAM_P, "Is this the one that stalled overnight?", ago(minutes=30))
        self._chat("AOS-3", ME_P, "Yes, the worker lost its heartbeat.", ago(minutes=29))
        self._chat("AOS-3", ME_P, "Re-running implement now.", ago(minutes=28))
        self._chat("AOS-4", SAM_P, "Plan looks good, but step 3 can go.", ago(minutes=35))
        self._chat("AOS-6", ME_P, "@Sam Lee can you sign off the re-scoped plan?", ago(hours=4))

    # ---------------------------------------------------------- tickets

    def get(self, key: str) -> TicketDetail:
        self.settle()
        ticket = self.tickets.get(key)
        if ticket is None:
            raise ApiError(404, "ticket_not_found", f"No ticket {key}")
        return self._derive(ticket, ME.id)

    def list_tickets(self, q: str | None, include_closed: bool) -> list[TicketSummary]:
        self.settle()
        out = []
        for t in self.tickets.values():
            if t.state == "closed" and not include_closed:
                continue
            if q and q.lower() not in f"{t.key} {t.title} {' '.join(t.labels)}".lower():
                continue
            out.append(self.summary(t))
        return out

    def create(self, body: TicketCreate) -> TicketDetail:
        with self.lock:
            key = f"AOS-{self.next_ticket}"
            self.next_ticket += 1
            owner = person(body.owner_id) if body.owner_id is not None else None
            t = self._add_ticket(
                key, body.title, ticket_type=body.type, priority=body.priority, owner=owner
            )
            t.description = body.description
            t.labels = body.labels
            t.workspace_mode = body.workspace_mode
            t.repo_url = body.repo_url if body.workspace_mode == "repo" else None
            t.base_branch = body.base_branch or ("main" if body.workspace_mode == "repo" else None)
            t.branch = f"agent/{key}" if body.workspace_mode == "repo" else None
            t.created_at = t.updated_at = datetime.now(UTC)
            return self._derive(t, ME.id)

    def patch(self, key: str, body: TicketPatch) -> TicketDetail:
        with self.lock:
            t = self.get(key)
            changes = body.model_dump(exclude_unset=True)
            if "owner_id" in changes:
                owner_id = changes.pop("owner_id")
                t.owner = person(owner_id) if owner_id is not None else None
            for field, value in changes.items():
                setattr(t, field, value)
            t.updated_at = datetime.now(UTC)
            return self._derive(t, ME.id)

    def put_pipeline(self, key: str, pipeline: Pipeline, base_version: int) -> TicketDetail:
        with self.lock:
            t = self.get(key)
            if t.active_run_id is not None:
                raise ApiError(409, "run_active", "Pipeline is locked while a run is active")
            if base_version != t.pipeline_version:
                raise ApiError(409, "stale_version", "Pipeline was changed by someone else")
            old = {r.stage_id: r.state for r in t.rail}
            t.pipeline = pipeline
            t.pipeline_version += 1
            t.rail = [
                RailStep(stage_id=s.id, name=s.name, phase=s.phase, state=old.get(s.id, "pending"))
                for s in pipeline.stages
                if s.enabled
            ]
            return self._derive(t, ME.id)

    # ---------------------------------------------------------- files

    def _infos(self, key: str) -> dict[str, list[_Version]]:
        self.get(key)
        return self.files[key]

    def list_files(self, key: str) -> list[FileInfo]:
        return [
            self._info(path, versions)
            for path, versions in sorted(self._infos(key).items())
            if versions[-1].content is not None
        ]

    def _info(self, path: str, versions: list[_Version]) -> FileInfo:
        v = versions[-1]
        return FileInfo(
            path=path,
            mode=v.mode,
            version=len(versions),
            size=len((v.content or "").encode()),
            author=v.author,
            updated_at=v.created_at,
        )

    def get_file(self, key: str, path: str, version: int | None) -> FileContent:
        versions = self._infos(key).get(path)
        if not versions or (version is None and versions[-1].content is None):
            raise ApiError(404, "file_not_found", f"No file {path}")
        n = version or len(versions)
        if not 1 <= n <= len(versions) or versions[n - 1].content is None:
            raise ApiError(404, "version_not_found", f"No version {n} of {path}")
        v = versions[n - 1]
        info = self._info(path, versions[:n])
        return FileContent(**info.model_dump(), content=v.content or "")

    def put_file(self, key: str, path: str, content: str | None, mode: Mode, base: int) -> int:
        with self.lock:
            t = self.get(key)
            if t.active_run_id is not None:
                raise ApiError(409, "bundle_locked", "Context is read-only while a run is active")
            versions = self.files[key].setdefault(path, [])
            current = len(versions) if versions and versions[-1].content is not None else 0
            if base != current:
                raise ApiError(409, "stale_version", f"{path} was changed by someone else")
            versions.append(_Version(content, mode, ME.display_name, datetime.now(UTC)))
            if path.endswith("/TASKS.md") and content is not None:
                done = len(re.findall(r"^\s*- \[x\]", content, re.M | re.I))
                todo = len(re.findall(r"^\s*- \[ \]", content, re.M))
                t.tasks = TaskProgress(done=done, total=done + todo) if done + todo else None
            return len(versions)

    def versions(self, key: str, path: str) -> list[FileVersionInfo]:
        versions = self._infos(key).get(path)
        if not versions:
            raise ApiError(404, "file_not_found", f"No file {path}")
        return [
            FileVersionInfo(
                version=i + 1,
                deleted=v.content is None,
                mode=v.mode,
                size=len((v.content or "").encode()),
                author=v.author,
                created_at=v.created_at,
            )
            for i, v in enumerate(versions)
        ]

    # ---------------------------------------------------------- chat

    def list_chat(
        self, key: str, after_id: int | None, before_id: int | None, limit: int
    ) -> list[ChatMessage]:
        self.get(key)
        msgs = self.chat.get(key, [])
        if after_id is not None:
            return [m for m in msgs if m.id > after_id][:limit]
        if before_id is not None:
            msgs = [m for m in msgs if m.id < before_id]
        return msgs[-limit:]

    def post_chat(self, key: str, body: str) -> ChatMessage:
        with self.lock:
            self.get(key)
            self._chat(key, ME_P, body, datetime.now(UTC))
            return self.chat[key][-1]

    # ---------------------------------------------------------- runs

    def dispatch(self, key: str, from_stage: str | None) -> RunSummary:
        with self.lock:
            t = self.get(key)
            if t.state != "open":
                raise ApiError(409, "invalid_stage", f"{key} is {t.state}; reopen it first")
            if t.active_run_id is not None:
                raise ApiError(409, "run_active", f"{key} already has an active run")
            start = from_stage or t.next_stage
            enabled = [s for s in t.pipeline.stages if s.enabled]
            ids = [s.id for s in enabled]
            if start not in ids:
                raise ApiError(422, "invalid_stage", f"Cannot start from {start}")
            segment: list[str] = []
            for s in enabled[ids.index(start) :]:
                if s.requires_repo and t.workspace_mode == "none":
                    raise ApiError(422, "invalid_stage", f"{s.id} requires a repo")
                segment.append(s.id)
                if s.gate_after:
                    break
            for step in t.rail:
                if step.stage_id == segment[0]:
                    step.state = "current"
                elif step.stage_id in segment:
                    step.state = "pending"
                elif ids.index(step.stage_id) > ids.index(segment[-1]) and step.state == "done":
                    step.state = "stale"
            run = self._add_run(
                t, "queued", [(s, "pending") for s in segment], started=datetime.now(UTC)
            )
            run.started_at = None
            self._sync_latest(run)
            t.updated_at = datetime.now(UTC)
            return RunSummary.model_validate(run.model_dump(exclude={"stage_runs", "manifest"}))

    def list_runs(self, key: str) -> list[RunSummary]:
        self.get(key)
        return [
            RunSummary.model_validate(r.model_dump(exclude={"stage_runs", "manifest"}))
            for r in sorted(self.runs.values(), key=lambda r: -r.id)
            if r.ticket_key == key
        ]

    def get_run(self, run_id: int) -> RunDetail:
        self.settle()
        run = self.runs.get(run_id)
        if run is None:
            raise ApiError(404, "run_not_found", f"No run {run_id}")
        return run

    def cancel(self, run_id: int) -> RunSummary:
        """SPEC §8: queued/dispatched cancel at once; running waits for the worker to ack."""
        with self.lock:
            run = self.get_run(run_id)
            if run.status in ("queued", "dispatched"):
                self._finish_cancel(run)
            elif run.status == "running" and run.cancel_requested_at is None:
                run.cancel_requested_at = datetime.now(UTC)
                self._sync_latest(run)
            return RunSummary.model_validate(run.model_dump(exclude={"stage_runs", "manifest"}))

    def settle(self) -> None:
        """STUB: pretend workers acknowledge cancels ~3 s after they are requested."""
        with self.lock:
            cutoff = datetime.now(UTC) - timedelta(seconds=3)
            for run in self.runs.values():
                if (
                    run.status == "running"
                    and run.cancel_requested_at
                    and run.cancel_requested_at < cutoff
                ):
                    self._finish_cancel(run)

    def _finish_cancel(self, run: RunDetail) -> None:
        run.status = "cancelled"
        run.finished_at = datetime.now(UTC)
        for sr in run.stage_runs:
            if sr.status in ("pending", "running"):
                sr.status = "cancelled"
        t = self.tickets[run.ticket_key]
        t.active_run_id = None
        for step in t.rail:
            if step.state == "current":
                step.state = "failed"
        self._sync_latest(run)

    def _sync_latest(self, run: RunDetail) -> None:
        self.tickets[run.ticket_key].latest_run = RunSummary.model_validate(
            run.model_dump(exclude={"stage_runs", "manifest"})
        )

    def run_events(
        self, run_id: int, after_seq: int | None, before_seq: int | None, limit: int
    ) -> list[RunEvent]:
        self.get_run(run_id)
        events = self.events.get(run_id, [])
        if after_seq is not None:
            return [e for e in events if e.seq > after_seq][:limit]
        if before_seq is not None:
            events = [e for e in events if e.seq < before_seq]
        return events[-limit:]


STORE = StubStore()
