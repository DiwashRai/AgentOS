"""Resolve stages to argv/stdin and build the protocol 2 job package (SPEC §7).

Pure. Queueing validates a run with `render_stages` (same code path as the build at dispatch), so
a queued run can't fail to render at dispatch unless the ticket changed in between.

A loop block's stages get every call the worker may make rendered here: later rounds and, for
checks, verdict repairs, with the session ids already in the argv. The worker only appends text
to a prompt (the round's feedback, or why a verdict was invalid), so no user text is ever
scanned for placeholders.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from agentos.errors import ApiError
from agentos.models import (
    VERDICT_FILE,
    VERDICT_NOTES_MAX,
    VERDICT_SCHEMA,
    ContextEntry,
    ContextKind,
    FileEntry,
    JobPackage,
    Loop,
    ManifestInvocation,
    ManifestJob,
    ManifestJournal,
    ManifestLimits,
    ManifestLoop,
    ManifestReport,
    ManifestStage,
    ManifestTicket,
    ManifestWorkspace,
    NoteEntry,
    Stage,
    SyncBack,
    TemplateDefaults,
    VerdictFrom,
)
from agentos.pipeline.render import TicketRef, render_prompt
from agentos.pipeline.runtime import RuntimesConfig, RuntimeSpec, space_defaults
from agentos.pipeline.scaffold import names, skill_dir
from agentos.pipeline.segment import RunPlan, loops_by_stage
from agentos.validate import (
    MAX_AGENT_LINE_BYTES,
    MAX_FILE_BYTES,
    MAX_SYNC_BACK_BYTES,
    SYNC_BACK_DIR,
    path_key,
)

VERDICT_SCHEMA_JSON = json.dumps(VERDICT_SCHEMA, separators=(",", ":"))
FOLLOW_UP = "The checks found problems with the last change. Address every point, then stop."
PREVIOUS_ROUND = "## Previous round\nLast round's findings, for you to confirm they were fixed:\n\n"
REPAIR_HEAD = "Your verdict was invalid: "

NewSession = Callable[[], str]


def new_session_id() -> str:
    return str(uuid.uuid4())


def _substitute(args: Sequence[str], placeholder: str, value: str) -> list[str]:
    """Placeholders replace whole argv elements only: `--model={model}` stays literal."""
    return [value if a == placeholder else a for a in args]


def _with_session(args: Sequence[str], session: str) -> list[str]:
    """`{session}` may be part of an element (`--resume={session}`); the value is a uuid."""
    return [a.replace("{session}", session) for a in args]


@dataclass(frozen=True, slots=True)
class _Agent:
    """An agent stage's runtime and its argv up to the loop groups (argv, model, effort, agent)."""

    spec: RuntimeSpec
    head: list[str]

    def call(
        self, prompt: str, extra: Sequence[str] = ()
    ) -> tuple[list[str], str | None, int | None]:
        """(argv, stdin, prompt_arg): `extra` (session, resume, verdict args) goes before the
        prompt args; the prompt is the stdin, or the argv element at `prompt_arg`."""
        argv = [*self.head, *extra]
        if self.spec.prompt_via == "arg":
            prompt_arg = len(argv) + self.spec.prompt_args.index("{prompt}")
            return argv + _substitute(self.spec.prompt_args, "{prompt}", prompt), None, prompt_arg
        return argv, prompt, None

    def invocation(self, prompt: str, extra: Sequence[str], tail: str) -> ManifestInvocation:
        argv, stdin, prompt_arg = self.call(prompt, extra)
        return ManifestInvocation(argv=argv, stdin=stdin, prompt_arg=prompt_arg, tail=tail)


def _agent(stage: Stage, runtimes: RuntimesConfig, defaults: TemplateDefaults) -> _Agent:
    space = space_defaults(defaults, runtimes)
    name = stage.runtime or space.runtime
    spec = runtimes.runtimes.get(name)
    if spec is None:
        raise ApiError(422, "invalid_stage", f"stage {stage.id}: unknown runtime {name!r}")
    head = list(spec.argv)
    on_space_runtime = name == space.runtime
    model = stage.model or (space.model if on_space_runtime else None)
    if model:
        head += _substitute(spec.model_args, "{model}", model)
    effort = stage.effort or (space.effort if on_space_runtime else None)
    if effort:
        head += _substitute(spec.effort_args, "{effort}", effort)
    if stage.agent:
        head += _substitute(spec.agent_args, "{agent}", stage.agent)
    return _Agent(spec, head)


def resolve_agent(
    stage: Stage, prompt: str, runtimes: RuntimesConfig, *, defaults: TemplateDefaults
) -> tuple[list[str], str | None]:
    """Final argv + stdin for an agent stage: `argv`, then `model_args`, `effort_args`,
    `agent_args` (each if its value resolves), then `prompt_args` (if `prompt_via = "arg"`, else
    the prompt is the stdin). Unknown runtime → 422 `invalid_stage`.

    A null `stage.runtime` / `model` / `effort` takes the Space default (`space_defaults`). The
    Space model and effort belong to the Space runtime: a stage on another runtime gets none."""
    argv, stdin, _ = _agent(stage, runtimes, defaults).call(prompt)
    return argv, stdin


# ------------------------------------------------------------------ loop blocks


def verdict_block(channel: VerdictFrom) -> str:
    """The fixed instructions the server appends to a check's prompt (authors don't write them)."""
    how = "When you are done, give the verdict as your final answer."
    if channel == "file":
        how = f"When you are done, write the verdict to `{VERDICT_FILE}` (nothing else in it)."
    return (
        "## Verdict\n"
        "You are a check in a review loop: your verdict decides whether this round passes.\n"
        '- "pass": nothing needs to change. `notes` may be empty.\n'
        '- "fail": `notes` lists every problem to fix, specific enough to act on (markdown, at '
        f"most {VERDICT_NOTES_MAX} characters).\n"
        f"{how} The verdict is a JSON object matching this JSON Schema: {VERDICT_SCHEMA_JSON}"
    )


def _repair_tail(channel: VerdictFrom) -> str:
    answer = "Answer only with the verdict"
    if channel == "file":
        answer = f"Write only the verdict to `{VERDICT_FILE}` (nothing else in it)"
    return (
        f".\nDon't redo the review. {answer}: a JSON object matching this JSON Schema: "
        f"{VERDICT_SCHEMA_JSON}"
    )


def _check(
    stage: Stage, prompt: str, agent: _Agent, loop: Loop, new_session: NewSession
) -> ManifestStage:
    """An agent check: a fresh session each round unless it resumes (`Stage.resume`), the verdict
    block at the end of every prompt, and (with sessions and `verdict_repairs`) one repair call
    per round that resumes that round's session."""
    spec, rounds = agent.spec, loop.max_rounds
    verdict = _substitute(spec.verdict_args, "{verdict_schema}", VERDICT_SCHEMA_JSON)
    block = verdict_block(spec.verdict_from)
    resume = bool(stage.resume) and spec.sessions
    sessions: list[str] = []
    if spec.sessions:
        sessions = [new_session()] * rounds if resume else [new_session() for _ in range(rounds)]
    first = [*_with_session(spec.session_args, sessions[0]), *verdict] if sessions else verdict
    argv, stdin, _ = agent.call(f"{prompt}\n\n{block}", first)
    later: list[ManifestInvocation] = []
    for n in range(2, rounds + 1):
        if resume:
            extra = _with_session(spec.resume_args, sessions[n - 1])
            head = f"Round {n} of {rounds}: the makers worked on the findings. Check again.\n\n"
        else:
            extra = _with_session(spec.session_args, sessions[n - 1]) if sessions else []
            head = f"{prompt}\n\n"
        later.append(agent.invocation(head + PREVIOUS_ROUND, [*extra, *verdict], f"\n\n{block}"))
    repairs: list[ManifestInvocation] = []
    if sessions and stage.verdict_repairs:
        tail = _repair_tail(spec.verdict_from)
        repairs = [
            agent.invocation(REPAIR_HEAD, [*_with_session(spec.resume_args, s), *verdict], tail)
            for s in sessions
        ]
    return ManifestStage(
        id=stage.id,
        name=stage.name,
        kind="agent",
        timeout_s=stage.timeout_s,
        argv=argv,
        stdin=stdin,
        check=True,
        rounds=later,
        repairs=repairs,
        verdict_from=spec.verdict_from,
        verdict_repairs=stage.verdict_repairs,
    )


def _maker(
    stage: Stage, prompt: str, agent: _Agent, loop: Loop, new_session: NewSession
) -> ManifestStage:
    """A maker: resumes its session in later rounds with a short follow-up (unless `Stage.resume`
    is false or the runtime has no sessions: then the full prompt plus the follow-up, fresh)."""
    spec, rounds = agent.spec, loop.max_rounds
    session = new_session() if spec.sessions and stage.resume is not False else None
    first = _with_session(spec.session_args, session) if session else []
    argv, stdin, _ = agent.call(prompt, first)
    later: list[ManifestInvocation] = []
    for n in range(2, rounds + 1):
        if session:
            extra = _with_session(spec.resume_args, session)
            head = f"Round {n} of {rounds}. {FOLLOW_UP}\n\n"
        else:
            extra = []
            head = f"{prompt}\n\n## Round {n} of {rounds}\n{FOLLOW_UP}\n\n"
        later.append(agent.invocation(head, extra, ""))
    return ManifestStage(
        id=stage.id,
        name=stage.name,
        kind="agent",
        timeout_s=stage.timeout_s,
        argv=argv,
        stdin=stdin,
        rounds=later,
    )


# ------------------------------------------------------------------ stages and the manifest


def render_stages(
    run_id: int,
    ticket: TicketRef,
    stages: Sequence[Stage],
    runtimes: RuntimesConfig,
    *,
    defaults: TemplateDefaults,
    loops: Sequence[Loop] = (),
    new_session: NewSession = new_session_id,
) -> list[ManifestStage]:
    """Render prompts and resolve runtimes; stages inside `loops` (blocks over `stages`) get their
    later-round and repair calls. 422 `render_error` / `invalid_stage` on failure.

    Doesn't re-check the segment (`segment.check_segment` does that at dispatch).
    """
    in_loop = loops_by_stage(stages, loops)
    out: list[ManifestStage] = []
    for stage in stages:
        loop = in_loop.get(stage.id)
        if stage.kind == "shell":
            out.append(
                ManifestStage(
                    id=stage.id,
                    name=stage.name,
                    kind="shell",
                    timeout_s=stage.timeout_s,
                    command=stage.command,
                    check=loop is not None,
                )
            )
            continue
        prompt = render_prompt(stage, ticket, run_id)
        agent = _agent(stage, runtimes, defaults)
        if loop is not None:
            build = _check if stage.check else _maker
            out.append(build(stage, prompt, agent, loop, new_session))
            continue
        argv, stdin, _ = agent.call(prompt)
        out.append(
            ManifestStage(
                id=stage.id,
                name=stage.name,
                kind="agent",
                timeout_s=stage.timeout_s,
                argv=argv,
                stdin=stdin,
            )
        )
    return out


def classify(f: FileEntry, key: str) -> ContextEntry | NoteEntry:
    """A ticket file as a note or a typed context entry. The first rule that matches wins (paths
    compared by `path_key`): the ticket skill's `learnings.log` / `swarm.log` → a note; its
    `TASKS.md` → `plan`; a block-mode file → `instructions` (the block is the ticket key);
    `.claude/agents/**` → `agent`; the ticket skill's `out/**` → `file`; `.claude/skills/<n>/**` →
    `skill` named `<n>`; anything else → `file`. Every ticket file is `access: write`."""
    p = PurePosixPath(f.path)
    k = path_key(f.path)
    d = path_key(skill_dir(key))
    if k in (f"{d}/learnings.log", f"{d}/swarm.log"):
        return NoteEntry(path=f.path, name=p.stem, content=f.content)

    kind: ContextKind = "file"
    name, block = p.name, None
    if k == f"{d}/tasks.md":
        kind, name = "plan", p.stem
    elif f.mode == "block":
        kind, name, block = "instructions", key, key
    elif k.startswith(".claude/agents/"):
        kind, name = "agent", p.stem
    elif k.startswith(".claude/skills/") and not k.startswith(f"{d}/out/") and len(p.parts) > 3:
        kind, name = "skill", p.parts[2]
    return ContextEntry(
        path=f.path,
        kind=kind,
        name=name,
        mode=f.mode,
        block=block,
        access="write",
        content=f.content,
    )


def build_package(
    run_id: int,
    ticket: TicketRef,
    plan: RunPlan,
    bundle: Sequence[FileEntry],
    runtimes: RuntimesConfig,
    *,
    defaults: TemplateDefaults,
    job: ManifestJob,
    journal: ManifestJournal,
    report: ManifestReport,
    new_session: NewSession = new_session_id,
) -> JobPackage:
    """The job package a worker receives, built when the run is dispatched.

    `bundle` is the ticket's current (non-deleted) files, split into `context` and `notes` by
    `classify`. In no-repo mode the workspace's `repo_url`/`base_branch`/`branch` are null.
    """
    n = names(ticket.key, ticket.workspace_mode)
    entries = [classify(f, ticket.key) for f in bundle]
    context = [e for e in entries if isinstance(e, ContextEntry)]
    repo = ticket.workspace_mode == "repo"
    stages = render_stages(
        run_id,
        ticket,
        plan.stages,
        runtimes,
        defaults=defaults,
        loops=plan.loops,
        new_session=new_session,
    )
    return JobPackage(
        protocol=2,
        job=job,
        journal=journal,
        report=report,
        run_id=run_id,
        ticket=ManifestTicket(key=ticket.key, title=ticket.title),
        workspace=ManifestWorkspace(
            mode=ticket.workspace_mode,
            repo_url=ticket.repo_url if repo else None,
            base_branch=ticket.base_branch if repo else None,
            branch=n.branch,
            protect=[n.skill_dir],
            blocks=list(dict.fromkeys(e.block for e in context if e.block is not None)),
        ),
        context=context,
        notes=[e for e in entries if isinstance(e, NoteEntry)],
        sync_back=SyncBack(dir=SYNC_BACK_DIR, max_bytes=MAX_SYNC_BACK_BYTES),
        env={"AGENTOS_SKILL_DIR": n.skill_dir, "AGENTOS_SYNC_BACK": SYNC_BACK_DIR},
        stages=stages,
        limits=ManifestLimits(max_file_bytes=MAX_FILE_BYTES, max_line_bytes=MAX_AGENT_LINE_BYTES),
        loops=[
            ManifestLoop(start=lp.start, end=lp.end, max_rounds=lp.max_rounds) for lp in plan.loops
        ],
        start_stage=plan.start,
        warnings=list(plan.warnings),
    )
