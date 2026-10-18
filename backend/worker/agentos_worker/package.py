"""The job package (protocol 2, agentos.models.JobPackage) as frozen records. A stage is
`AgentStage | ShellStage`; `check_loops` checks the loop shape the stage loop relies on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal

from agentos_worker.fit import Misfit

PROTOCOL = 2


class PackageError(ValueError):
    """The job's package isn't protocol 2, doesn't fit its types, or its loop blocks don't fit
    its stages."""


@dataclass(frozen=True)
class ContextFile:
    """A `context` entry, or a note (a write-access replace file)."""

    path: str
    name: str
    content: str
    kind: str = "note"  # "skill" | "plan" | "instructions" | "agent" | "file" | "note"
    mode: str = "replace"  # "replace" | "block"
    block: str | None = None  # block mode: the block's name
    access: str = "write"  # "write" (synced back) | "read"


@dataclass(frozen=True)
class Invocation:
    """Another call of a loop stage's agent: a later round, or a verdict repair. The prompt is
    `argv[prompt_arg]`, or `stdin` when `prompt_arg` is None."""

    argv: tuple[str, ...]
    stdin: str | None
    prompt_arg: int | None
    tail: str

    def __post_init__(self) -> None:
        if not self.argv:
            raise Misfit("argv", "expected a program and its arguments, got none")
        if self.prompt_arg is not None and not 0 <= self.prompt_arg < len(self.argv):
            raise Misfit("prompt_arg", f"expected an index into argv (0 to {len(self.argv) - 1})")

    def build(self, text: str) -> tuple[list[str], str | None]:
        """(argv, stdin) with `text`, then `tail`, appended to the prompt. Nothing else changes."""
        argv = list(self.argv)
        if self.prompt_arg is None:
            return argv, (self.stdin or "") + text + self.tail
        argv[self.prompt_arg] += text + self.tail
        return argv, None


@dataclass(frozen=True)
class AgentCheck:
    """What makes an agent stage a loop block's check: its JSON verdict is read from
    `verdict_from`, and an invalid one is repaired at most `verdict_repairs` times a round by
    that round's call in `repairs` (none, and 0 repairs: the runtime has no sessions)."""

    verdict_from: Literal["structured_output", "file"]
    verdict_repairs: int
    repairs: tuple[Invocation, ...]


@dataclass(frozen=True)
class AgentStage:
    """An agent's call: round 1 is `argv` with `stdin`, used exactly as given. In a loop block,
    `rounds` are the calls of rounds 2..max_rounds, and `check` makes it decide the round."""

    kind: ClassVar[str] = "agent"  # for messages; branch on the type

    id: str
    name: str
    timeout_s: int
    argv: tuple[str, ...]
    stdin: str | None
    rounds: tuple[Invocation, ...]
    check: AgentCheck | None


@dataclass(frozen=True)
class ShellStage:
    """A shell command, the same every round. In a loop block, `check` makes its exit code
    decide the round."""

    kind: ClassVar[str] = "shell"

    id: str
    name: str
    timeout_s: int
    command: str
    check: bool


Stage = AgentStage | ShellStage


@dataclass(frozen=True)
class Loop:
    start: str  # stage ids; the block is every stage from start to end
    end: str
    max_rounds: int


@dataclass(frozen=True)
class Job:
    id: str
    run_id: int
    segment: int
    segments: int
    budget_s: int
    not_after: datetime  # nothing is published after it


@dataclass(frozen=True)
class Journal:
    first_seq: int  # the seq of the job's `started` record


@dataclass(frozen=True)
class Report:
    mode: Literal["live", "file"]
    url: str | None


@dataclass(frozen=True)
class Ticket:
    key: str
    title: str


@dataclass(frozen=True)
class Workspace:
    """Checked where it is set up (Run._setup_workspace), not here."""

    mode: Literal["repo", "none"]
    repo_url: str | None
    base_branch: str | None
    branch: str | None
    protect: tuple[str, ...]  # folders kept out of git besides the declared files
    blocks: tuple[str, ...]  # every block name in `context`


@dataclass(frozen=True)
class SyncBackSpec:
    dir: str
    max_bytes: int


@dataclass(frozen=True)
class Limits:
    max_file_bytes: int
    max_line_bytes: int


@dataclass(frozen=True)
class Package:
    """A protocol 2 job package (agentos.models.JobPackage). Paths are checked where they are
    written (resolve_inside), the workspace where it is set up."""

    protocol: int
    job: Job
    journal: Journal
    report: Report
    run_id: int
    ticket: Ticket
    workspace: Workspace
    context: tuple[ContextFile, ...]
    notes: tuple[ContextFile, ...]
    sync_back: SyncBackSpec
    env: dict[str, str]
    stages: tuple[Stage, ...]
    limits: Limits
    loops: tuple[Loop, ...] = ()
    start_stage: str | None = None  # where round 1 starts; None = the first stage
    warnings: tuple[str, ...] = ()  # emitted as warning events once the workspace is ready

    @property
    def files(self) -> tuple[ContextFile, ...]:
        return (*self.context, *self.notes)


def _check_loop_stage(stage: Stage, loop: Loop, where: str) -> None:
    """An agent stage in a loop block has a call for every round, and an agent check a repair
    call for every round or none."""
    match stage:
        case AgentStage(rounds=rounds, check=check):
            if len(rounds) != loop.max_rounds - 1:
                raise PackageError(
                    f"{where}.rounds: expected {loop.max_rounds - 1} (max_rounds - 1), "
                    f"got {len(rounds)}"
                )
            if check is not None and len(check.repairs) not in (0, loop.max_rounds):
                raise PackageError(
                    f"{where}.repairs: expected 0 or {loop.max_rounds} (max_rounds), "
                    f"got {len(check.repairs)}"
                )


def _block_only(stage: Stage) -> bool:
    """Whether the stage has what only a loop block's stages have: a check, or later rounds."""
    match stage:
        case AgentStage():
            return stage.check is not None or bool(stage.rounds)
        case ShellStage():
            return stage.check


def check_loops(p: Package) -> None:
    """The shape the stage loop relies on: unique stage ids; loop blocks over known stages, in
    order and not overlapping; loop extras only on block stages; a start stage that is the first
    stage or inside the block that starts there."""
    stages = p.stages
    index = {s.id: i for i, s in enumerate(stages)}
    if len(index) != len(stages):
        raise PackageError("stages: duplicate stage ids")
    covered: set[int] = set()
    after = 0  # the first stage index the next block may start at
    for i, loop in enumerate(p.loops):
        where = f"loops[{i}]"
        if loop.max_rounds < 1:
            raise PackageError(f"{where}.max_rounds: expected an integer >= 1")
        for name, stage_id in (("start", loop.start), ("end", loop.end)):
            if stage_id not in index:
                raise PackageError(f"{where}.{name}: no stage {stage_id!r}")
        first, last = index[loop.start], index[loop.end]
        if first > last:
            raise PackageError(f"{where}: start {loop.start!r} comes after end {loop.end!r}")
        if first < after:
            raise PackageError(f"{where}: overlaps or comes before the previous loop")
        after = last + 1
        for j in range(first, last + 1):
            _check_loop_stage(stages[j], loop, f"stages[{j}]")
            covered.add(j)
    for j, stage in enumerate(stages):
        if j not in covered and _block_only(stage):
            raise PackageError(f"stages[{j}]: check, rounds and repairs need a loop block")

    if p.start_stage is not None:
        at = index.get(p.start_stage)
        if at is None:
            raise PackageError(f"start_stage: no stage {p.start_stage!r}")
        if at and not any(index[lp.start] == 0 and at <= index[lp.end] for lp in p.loops):
            raise PackageError("start_stage: must be the first stage, or in a block from there")
