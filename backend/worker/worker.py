"""AgentOS worker: runs one job of a run, reporting it as the run's journal, then exits.

Single file, Python >= 3.11 standard library only (enforced by a test). Served by the server at
GET /api/worker/worker.py so runners always get the matching version. See SPEC §9.

Flow: fetch the job package -> bind (post `started`, wait for its ack) -> workspace (clone, or
`git init` in no-repo mode) -> write its context and notes -> emit its warnings -> for each stage
execution: run it, record a `sync`, then (repo mode) commit, pre-push guard, push, record `pushed`
-> (repo mode) look up the branch's PR with `gh` -> sync_back -> record the `result`. Records go
through EventSink, which resends from `ack + 1` with the instance id `started` bound, to a
Reporter: HttpReporter, or Recorder in mailbox mode.

Publication: `git push -u`, never forced, so a push after the branch moved under the run is
rejected (PUSH_REJECTED; publish_branch's ref write is likewise `force: false` on the last
published commit). That check is the protection. The clone's pre-push hook, which refuses a push
without the worker's marker, only guards against an agent's accidental push: the marker is plain
text in the clone's hook file, and `--no-verify` or `-c core.hooksPath=` skip it. Except on
GitHub Actions (GITHUB_ACTIONS=true with GH_TOKEN), where each stage's work goes through the
Git Data API of the ticket's repo at GITHUB_API_URL (see publish_branch) as one commit: blobs, a
tree (checked against HEAD's), the commit on the branch's remote commit, then one ref write with
`force: false`. Merges and rewrites are refused. Setup first checks the token can write there.
A GitHub failure fails the stage; it is never a lost run or an unreachable server.

Every output line goes out as a log event. An agent stage's output is also printed on stderr as
it arrives, as readable `[stage id] ...` lines (see Echo), so a job console or the launcher's log
file shows the run live; AGENTOS_ECHO=0 turns that off.

Loop blocks (`loops`): the block's stages run in rounds, at most `max_rounds`. Its checks decide
each round, by code: a shell check by its exit code; an agent check by a JSON verdict
({"verdict": "pass"|"fail", "notes"}) read from the CLI's result line or `.agentos/verdict.json`,
checked by `verdict_error`, and repaired by resuming the check's session at most `verdict_repairs`
times. Every check runs each round. No failing check ends the block; otherwise the next round's
calls get the failing checks' notes appended. Still failing after the last round, or a check
without a valid verdict, fails the run. The worker only appends text to prompts, never substitutes.

Binding: 409 (another instance holds the job) exits 0; 401/410 or an unusable package exits 1,
with the workdir untouched. Failure paths after it:
- cancel (`cancel: true` in a journal answer): kill the stage tree, still sync/commit/push,
  result `cancelled`.
- over budget (the job's `budget_s` passed since binding): the same, then result `failed`.
- run gone (401/409/410 on any post, or the job's `not_after` passed before a publish): kill the
  stage tree, no commit, no push, exit 1.
- server unreachable (network errors / 5xx for RETRY_BUDGET_S, or any other 4xx on the journal):
  kill the stage tree, commit and push what exists, exit 1 (the reaper fails the run).
- result rejected (its seq in a journal answer's `rejected`): the run didn't end, so nothing more
  is published and the workdir is kept; exit 1 (the reaper fails the run).

Mailbox mode (`--manifest PATH --outbox PATH --workdir PATH`, for runners that can't reach the
server): no HTTP. The package comes from a file, and the journal is recorded as worker API calls,
one JSONL line each, in the outbox (see Recorder), which the server replays later. Nothing
answers, so no cancel; the only run-gone is `not_after`; a failed outbox write is unreachable.

Client mode (`--client --server URL`, the client token in AGENTOS_CLIENT_TOKEN, for a user's own
machine): long-polls POST /api/worker/lease for the jobs of the runs its user dispatched to their
client and starts this file in HTTP mode as a detached child for each, up to `--max-runs` at once
(see run_client). It backs off while the server is unreachable and stops on any 4xx but a
transient one (401/403 included) and on another protocol.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import hashlib
import http.client
import io
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Protocol

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

PROTOCOL = 2

# ---------------------------------------------------------------- tunables (tests shrink these)
HTTP_TIMEOUT_S = 30.0  # per request
UPLOAD_TIMEOUT_S = 300.0  # per attempt of the sync_back upload (up to sync_back.max_bytes)
RETRY_BUDGET_S = 300.0  # network errors and 5xx are retried this long; then "unreachable"
BACKOFF_START_S = 0.5
BACKOFF_CAP_S = 10.0
FLUSH_INTERVAL_S = 1.0  # pending events are posted at least this often...
FLUSH_THRESHOLD = 200  # ...or as soon as this many are buffered
HEARTBEAT_S = 10.0  # an (empty) journal post at least this often
POLL_S = 0.2  # how often a running stage checks cancel / timeout / server loss
READER_JOIN_S = 5.0  # after a stage exits, wait this long for its output readers, then abandon
GIT_TIMEOUT_S = 1800.0
KILL_TIMEOUT_S = 30.0

# Client mode (see run_client).
CLIENT_POLL_WAIT_S = 25  # the server holds a lease this long at most while no run is queued
CLIENT_POLL_TIMEOUT_S = 40.0  # one lease's HTTP timeout: above the server's hold
CLIENT_BACKOFF_CAP_S = 60.0  # failed leases back off up to this, and are retried forever
CLIENT_REAP_S = 1.0  # with every slot busy, look for finished workers this often
CLIENT_HEARTBEAT_S = 30.0  # with every slot busy, a no-take lease this often keeps it online
WORKSPACE_MAX_AGE_S = 7 * 24 * 3600.0  # older run workspaces and logs go at client startup
CLIENT_RUNS_DIR = "runs"  # under --workdir-root: the run workspaces and logs, all the client owns
# Lease answers worth retrying; any other 4xx (a revoked token, a newer protocol) stops the client.
CLIENT_TRANSIENT_4XX = frozenset({408, 425, 429})

MAX_EVENTS_PER_POST = 500
MAX_EVENTS_PER_RUN = 100_000
EVENT_RESERVE = 1_000  # head-room kept for non-log events once log events are dropped
SHELL_LINE_BYTES = 8192  # line cap for every stream except agent-stage stdout
MAX_PATH_CHARS = 255
MAX_ERROR_CHARS = 2000
CANCELLED = "cancelled"  # stage_finished reason for a plain cancel
OVER_BUDGET = "time budget exceeded"  # the job's budget_s ran out
PUSH_REJECTED = "push rejected: the branch moved during the stage; did the agent push?"
# Installed as the clone's only hook: refuses any push without the worker's per-run marker. A
# guardrail against an agent's accidental push, not a boundary: the marker is readable in this
# file and `--no-verify` skips the hook. The never-forced push (PUSH_REJECTED) is the protection.
PRE_PUSH = '#!/bin/sh\n[ "$AGENTOS_PUSH" = "{}" ] && exit 0\necho "agentos: no push" >&2\nexit 1\n'

# Loop blocks and verdicts. `.agentos/` belongs to AgentOS: never committed, never synced.
AGENTOS_DIR = ".agentos"
VERDICT_FILE = ".agentos/verdict.json"  # where a `file`-channel check writes its verdict
VERDICT_FILE_BYTES = 256 * 1024
VERDICT_NOTES_MAX = 20_000  # agentos.models.VERDICT_NOTES_MAX
SHELL_CHECK_LINES = 200  # a failing shell check's notes: at most this many last output lines
FEEDBACK_MAX = 40_000  # the combined notes the next round's calls get

# Tool caches nobody commits on purpose, added to .git/info/exclude so `git add -A` skips them
# even when the target repo has no .gitignore. Exclude rules never touch tracked files, and the
# repo's own .gitignore (including `!` negations) takes precedence.
JUNK_PATTERNS = (
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".venv/",
    "node_modules/",
    ".DS_Store",
    "Thumbs.db",
)

# End-of-run PR lookup (repo mode): ask the CLI that opened the PR for the branch's PR, so the link
# never depends on what an agent printed. `{branch}` is replaced; the program is resolved on the
# stage PATH. AGENTOS_PR_LOOKUP=0 in the worker's own environment skips the lookup.
PR_LOOKUP = ("gh", "pr", "view", "{branch}", "--json", "url", "--jq", ".url")
PR_LOOKUP_TIMEOUT_S = 30.0
MAX_PR_URL_CHARS = 500  # the server's ResultData.pr_url max_length

# GitHub API publication on Actions (see api_publish).
GITHUB_RETRY_BUDGET_S = 300.0  # rate limits, network errors and 5xx are retried this long
GITHUB_MIN_WAIT_S = 1.0  # the shortest pause GitHub's rate-limit answers get
GITHUB_API_VERSION = "2022-11-28"
INLINE_MAX_BYTES = 1024 * 1024  # text up to this goes inline in the tree; larger is a blob
MAX_GITHUB_MESSAGE_CHARS = 500

# Characters cmd.exe interprets; a .cmd/.bat program must not receive them in argv (SPEC §9.3).
CMD_UNSAFE = frozenset('\r\n"&|<>^%!')

_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

_STDERR_LOCK = threading.Lock()  # say() and the output readers' echo (Echo) share stderr


def say(message: str) -> None:
    """Worker progress on stderr (the launcher redirects it to a log file). Never pass tokens."""
    stamp = datetime.now(UTC).strftime("%H:%M:%S")
    with _STDERR_LOCK:
        print(f"[worker {stamp}] {message}", file=sys.stderr, flush=True)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clip(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _decode(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


# ---------------------------------------------------------------- path rules (SPEC §6)

_SEGMENT_RE = re.compile(r"[A-Za-z0-9._ @+-]+")
_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)
_BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")


def path_error(path: str) -> str | None:
    """Why `path` breaks the SPEC §6 path rules, or None. Mirrors the server's validate_path."""
    if not path:
        return "empty path"
    if len(path) > MAX_PATH_CHARS:
        return f"longer than {MAX_PATH_CHARS} characters"
    if unicodedata.normalize("NFC", path) != path:
        return "not NFC-normalised"
    for seg in path.split("/"):
        if not seg:
            return "empty segment (absolute path, '//' or trailing '/')"
        if seg == "..":
            return "'..' segment"
        if not _SEGMENT_RE.fullmatch(seg):
            return f"segment {seg!r} has characters outside [A-Za-z0-9._ @+-]"
        if seg.lower() == ".git":
            return "'.git' segment"
        if seg.split(".")[0].rstrip(" ").upper() in _RESERVED:
            return f"segment {seg!r} is a Windows reserved name"
        if seg.endswith((".", " ")):
            return f"segment {seg!r} ends in '.' or a space"
    return None


def branch_error(name: str) -> str | None:
    """Why `name` is not an acceptable branch name (SPEC §6 base_branch rule), or None."""
    if not _BRANCH_RE.fullmatch(name):
        return "must match [A-Za-z0-9][A-Za-z0-9._/-]{0,199}"
    if ".." in name or "//" in name:
        return "contains '..' or '//'"
    if name.endswith(("/", ".", ".lock")):
        return "ends in '/', '.' or '.lock'"
    return None


def pr_url_error(url: str) -> str | None:
    """Why `url` is not a PR link the server would store, or None. Mirrors validate_pr_url."""
    if len(url) > MAX_PR_URL_CHARS:
        return f"longer than {MAX_PR_URL_CHARS} characters"
    if any(c.isspace() or not c.isprintable() for c in url):
        return "contains whitespace or control characters"
    if not url.startswith("https://"):
        return "not an https:// URL"
    try:
        netloc = urllib.parse.urlsplit(url).netloc
    except ValueError:
        return "not a URL"
    if not netloc or "@" in netloc:
        return "no host, or credentials in the URL"
    return None


def pr_lookup_result(
    program: str, result: ProcResult, out: list[str], err: list[str]
) -> tuple[str | None, str]:
    """(PR URL, "") from a finished PR lookup, or (None, why not). `out` and `err` are its
    non-blank stdout and stderr lines, stripped."""
    if result.error:
        return None, result.error
    if result.stopped == "timeout":
        return None, f"{program} timed out after {PR_LOOKUP_TIMEOUT_S:g}s"
    if result.stopped is not None:
        return None, "the server went away"
    if result.exit_code != 0:
        detail = f": {_clip(err[-1], 200)}" if err else ""
        return None, f"{program} exited with code {result.exit_code}{detail}"

    if len(out) != 1:
        return None, f"expected one URL line from {program}, got {len(out)} lines"
    problem = pr_url_error(out[0])
    if problem:
        return None, f"{program} printed {_clip(out[0], 200)!r}: {problem}"
    return out[0], ""


_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: symlinks and junctions on Windows


def is_link(st: os.stat_result) -> bool:
    """A symlink, or on Windows any reparse point (junctions included)."""
    return stat.S_ISLNK(st.st_mode) or bool(
        int(getattr(st, "st_file_attributes", 0)) & _REPARSE_POINT
    )


def resolve_inside(root: Path, rel: str) -> tuple[Path, str | None]:
    """(absolute path, error) for `rel` under `root`. Refuses invalid paths and any existing
    component that is a symlink, so a write or read can never leave the workspace."""
    err = path_error(rel)
    if err:
        return root, err
    parts = rel.split("/")
    current = root
    for i, part in enumerate(parts):
        current = current / part
        try:
            st = current.lstat()
        except FileNotFoundError:
            break
        except OSError as exc:
            return root, f"cannot stat {'/'.join(parts[: i + 1])}: {exc.strerror}"
        if is_link(st):
            return root, f"{'/'.join(parts[: i + 1])} is a symlink"
        if i < len(parts) - 1 and not stat.S_ISDIR(st.st_mode):
            return root, f"{'/'.join(parts[: i + 1])} is not a directory"
    return root.joinpath(*parts), None


def existing_entry(root: Path, rel: str) -> str | None:
    """The path of whatever already sits at `rel` under `root`, matched ignoring case at every
    level as on a case-insensitive filesystem, or None. Any entry counts, a dangling symlink
    included; only real folders are descended into."""
    here, found = root, []
    parts = rel.split("/")
    for i, part in enumerate(parts):
        try:
            with os.scandir(here) as entries:
                match = next((e for e in entries if e.name.lower() == part.lower()), None)
        except OSError:
            return None
        if match is None:
            return None
        found.append(match.name)
        if i == len(parts) - 1:
            return "/".join(found)
        if not match.is_dir(follow_symlinks=False):
            return None
        here = Path(match.path)
    return None


def read_regular(root: Path, rel: str, limit: int | None = None) -> tuple[bytes | None, str | None]:
    """(content, error) of a regular file of at most `limit` bytes. (None, None) when the file
    doesn't exist."""
    target, err = resolve_inside(root, rel)
    if err:
        return None, err
    try:
        st = target.lstat()
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"cannot stat: {exc.strerror}"
    if is_link(st):
        return None, "is a symlink"
    if not stat.S_ISREG(st.st_mode):
        return None, "is not a regular file"
    if limit is not None and st.st_size > limit:
        return None, f"is over {limit} bytes"
    try:
        return target.read_bytes(), None
    except OSError as exc:
        return None, f"cannot read: {exc.strerror}"


# ---------------------------------------------------------------- block mode (SPEC §6)
#
# A block file holds, somewhere in it, exactly these bytes (written with LF):
#     <!-- agentos:begin KEY -->\n{content}\n<!-- agentos:end KEY -->\n
# The read-back is the bytes between "begin line + its newline" and "newline + end line", so any
# content (LF, CRLF, no trailing newline, empty) round-trips byte for byte. If an editor converts
# the whole file to CRLF, the separator newline is taken from the begin line.


class BlockError(ValueError):
    """A block-mode file whose agentos markers are missing or malformed."""


def block_markers(key: str) -> tuple[bytes, bytes]:
    return f"<!-- agentos:begin {key} -->".encode(), f"<!-- agentos:end {key} -->".encode()


def _marker_lines(data: bytes, marker: bytes) -> list[tuple[int, int]]:
    """(start, stop) of every line that is exactly `marker`; `stop` includes the newline."""
    found: list[tuple[int, int]] = []
    i = data.find(marker)
    while i >= 0:
        j = i + len(marker)
        if i == 0 or data[i - 1] == 0x0A:
            if data.startswith(b"\r\n", j):
                found.append((i, j + 2))
            elif data.startswith(b"\n", j):
                found.append((i, j + 1))
            elif j == len(data):
                found.append((i, j))
        i = data.find(marker, i + 1)
    return found


def _block_span(data: bytes, key: str) -> tuple[int, int, int, int] | None:
    """(block start, content start, content end, block stop) for `key`; None if no markers."""
    begin, end = block_markers(key)
    begins, ends = _marker_lines(data, begin), _marker_lines(data, end)
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1:
        raise BlockError("expected exactly one begin and one end marker line")
    (b_start, b_stop), (e_start, e_stop) = begins[0], ends[0]
    if e_start < b_stop:
        raise BlockError("the end marker comes before the begin marker")
    inner = data[b_stop:e_start]
    if not inner:
        raise BlockError("no newline between the markers")
    newline = b"\r\n" if data[b_stop - 2 : b_stop] == b"\r\n" else b"\n"
    sep = newline if inner.endswith(newline) else b"\n"
    return b_start, b_stop, e_start - len(sep), e_stop


def block_read(data: bytes, key: str) -> str:
    """The block content for `key` in a file's bytes. Raises BlockError if missing/malformed."""
    span = _block_span(data, key)
    if span is None:
        raise BlockError("agentos markers are missing")
    text = _decode(data[span[1] : span[2]])
    if text is None:
        raise BlockError("the block is not valid UTF-8")
    return text


def block_write(existing: bytes | None, key: str, content: str) -> bytes:
    """`existing` with any stale block for `key` removed and a fresh block appended."""
    begin, end = block_markers(key)
    body = content.encode("utf-8")
    if _marker_lines(body, begin) or _marker_lines(body, end):
        raise BlockError("the content contains an agentos marker line")
    data = existing or b""
    span = _block_span(data, key)
    if span is not None:
        start, stop = span[0], span[3]
        # Drop the separator newline this function put before the block.
        if data[start - 2 : start] == b"\r\n":
            start -= 2
        elif start > 0 and data[start - 1] == 0x0A:
            start -= 1
        data = data[:start] + data[stop:]
    block = begin + b"\n" + body + b"\n" + end + b"\n"
    return data + b"\n" + block if data else block


# ---------------------------------------------------------------- the job package (protocol 2)


class PackageError(ValueError):
    """The job's package isn't protocol 2, or its loop blocks don't fit its stages."""


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

    argv: list[str]
    stdin: str | None
    prompt_arg: int | None
    tail: str

    def build(self, text: str) -> tuple[list[str], str | None]:
        """(argv, stdin) with `text`, then `tail`, appended to the prompt. Nothing else changes."""
        argv = list(self.argv)
        if self.prompt_arg is None:
            return argv, (self.stdin or "") + text + self.tail
        argv[self.prompt_arg] += text + self.tail
        return argv, None


@dataclass(frozen=True)
class Stage:
    id: str
    name: str
    kind: str  # "agent" | "shell"
    timeout_s: int
    argv: list[str] | None  # agent: round 1's call (with stdin), used exactly as given
    stdin: str | None
    command: str | None  # shell: every round's call
    check: bool = False  # in a loop block: the stage decides the round
    rounds: tuple[Invocation, ...] = ()  # agent stages in a block: rounds 2..max_rounds
    repairs: tuple[Invocation, ...] = ()  # agent checks: one repair call per round, or none
    verdict_from: str | None = None  # agent checks: "structured_output" | "file"
    verdict_repairs: int = 0  # agent checks: at most this many repairs per round


@dataclass(frozen=True)
class Loop:
    start: str  # stage ids; the block is every stage from start to end
    end: str
    max_rounds: int


@dataclass(frozen=True)
class Package:
    """A protocol 2 job package (agentos.models.JobPackage). Paths are checked where they are
    written (resolve_inside), the workspace where it is set up."""

    protocol: int
    job: dict[str, Any]  # id, run_id, segment, segments, budget_s, not_after
    journal: dict[str, int]  # first_seq: the seq of the job's `started` record
    report: dict[str, Any]  # mode (live | file), url
    run_id: int
    ticket: dict[str, str]
    workspace: dict[str, Any]  # mode, repo_url, base_branch, branch, protect, blocks
    context: tuple[ContextFile, ...]
    notes: tuple[ContextFile, ...]
    sync_back: dict[str, Any]
    env: dict[str, str]
    stages: tuple[Stage, ...]
    limits: dict[str, int]
    loops: tuple[Loop, ...] = ()
    start_stage: str | None = None  # where round 1 starts; None = the first stage
    warnings: tuple[str, ...] = ()  # emitted as warning events once the workspace is ready

    @property
    def key(self) -> str:
        return self.ticket["key"]

    @property
    def files(self) -> tuple[ContextFile, ...]:
        return (*self.context, *self.notes)


def _check_loop_stage(stage: Stage, loop: Loop, where: str) -> None:
    """A loop block stage's calls must cover every round (and every repair)."""
    if stage.kind == "shell":
        if stage.rounds or stage.repairs:
            raise PackageError(f"{where}: a shell stage takes no rounds or repairs")
        return
    if len(stage.rounds) != loop.max_rounds - 1:
        raise PackageError(
            f"{where}.rounds: expected {loop.max_rounds - 1} (max_rounds - 1), "
            f"got {len(stage.rounds)}"
        )
    if not stage.check:
        if stage.repairs:
            raise PackageError(f"{where}.repairs: only an agent check is repaired")
        return
    if stage.verdict_from is None:
        raise PackageError(f"{where}.verdict_from: an agent check needs structured_output or file")
    if len(stage.repairs) not in (0, loop.max_rounds):
        raise PackageError(
            f"{where}.repairs: expected 0 or {loop.max_rounds} (max_rounds), "
            f"got {len(stage.repairs)}"
        )


def _check_loops(p: Package) -> None:
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
        if type(loop.max_rounds) is not int or loop.max_rounds < 1:
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
        if j not in covered and (stage.check or stage.rounds or stage.repairs):
            raise PackageError(f"stages[{j}]: check, rounds and repairs need a loop block")

    if p.start_stage is not None:
        at = index.get(p.start_stage)
        if at is None:
            raise PackageError(f"start_stage: no stage {p.start_stage!r}")
        if at and not any(index[lp.start] == 0 and at <= index[lp.end] for lp in p.loops):
            raise PackageError("start_stage: must be the first stage, or in a block from there")


def load_package(raw: object) -> Package:
    """The job's package, built straight from its JSON. Checks only the protocol and the loop
    shape (_check_loops); anything else malformed is a PackageError too."""
    protocol = raw.get("protocol") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or protocol != PROTOCOL:
        raise PackageError(f"unsupported protocol {protocol!r} (worker speaks {PROTOCOL})")

    try:
        stages = tuple(
            Stage(
                **{
                    **s,
                    "rounds": tuple(Invocation(**c) for c in s.get("rounds", ())),
                    "repairs": tuple(Invocation(**c) for c in s.get("repairs", ())),
                }
            )
            for s in raw["stages"]
        )
        package = Package(
            **{
                **raw,
                "context": tuple(ContextFile(**e) for e in raw["context"]),
                "notes": tuple(ContextFile(**n) for n in raw["notes"]),
                "stages": stages,
                "loops": tuple(Loop(**lp) for lp in raw.get("loops", ())),
                "warnings": tuple(raw.get("warnings", ())),
            }
        )
    except (KeyError, TypeError) as exc:
        raise PackageError(f"malformed package: {exc!r}") from exc

    _check_loops(package)
    return package


# ---------------------------------------------------------------- verdicts (loop block checks)


@dataclass(frozen=True)
class Verdict:
    """A check's valid verdict in one round."""

    verdict: str  # "pass" | "fail"
    notes: str


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    for kind, name in ((bool, "boolean"), (str, "string"), (list, "array"), (dict, "object")):
        if isinstance(value, kind):
            return name
    return "number" if isinstance(value, int | float) else type(value).__name__


def verdict_error(value: object) -> str | None:
    """Why `value` is not a valid verdict, or None. The shape is agentos.models.VERDICT_SCHEMA
    ({"verdict": "pass"|"fail", "notes": str}, nothing else), plus the notes rules."""
    if not isinstance(value, dict):
        return f"expected a JSON object, got {_json_type(value)}"
    for key in ("verdict", "notes"):
        if key not in value:
            return f"missing key: {key}"
    extra = sorted(str(k) for k in value if k not in ("verdict", "notes"))
    if extra:
        return f"unexpected key: {extra[0]}"
    verdict, notes = value["verdict"], value["notes"]
    if not isinstance(verdict, str) or verdict not in ("pass", "fail"):
        return f'verdict: expected "pass" or "fail", got {_clip(json.dumps(verdict), 100)}'
    if not isinstance(notes, str):
        return f"notes: expected a string, got {_json_type(notes)}"
    if len(notes) > VERDICT_NOTES_MAX:
        return f"notes: longer than {VERDICT_NOTES_MAX} characters"
    if verdict == "fail" and not notes.strip():
        return 'notes: must not be empty when the verdict is "fail"'
    return None


def result_verdict(result: dict[str, Any] | None) -> tuple[Any, str | None]:
    """(value, error): the `structured_output` of a CLI's last stream-json result line."""
    if result is None:
        return None, "the CLI printed no result line"
    value = result.get("structured_output")
    if value is None:
        subtype = result.get("subtype")
        detail = f" (subtype: {_clip(str(subtype), 100)})" if subtype else ""
        return None, f"the CLI's result has no structured_output{detail}"
    return value, None


def file_verdict(root: Path) -> tuple[Any, str | None]:
    """(value, error): the JSON in the workspace's verdict file (a regular file, no symlinks)."""
    data, err = read_regular(root, VERDICT_FILE, limit=VERDICT_FILE_BYTES)
    if err:
        return None, f"{VERDICT_FILE}: {err}"
    if data is None:
        return None, f"{VERDICT_FILE} was not written"
    text = _decode(data)
    if text is None:
        return None, f"{VERDICT_FILE} is not valid UTF-8"
    try:
        return json.loads(text.removeprefix("\ufeff")), None
    except (ValueError, RecursionError) as exc:
        return None, f"{VERDICT_FILE} is not valid JSON: {exc}"


def shell_verdict(code: int, lines: list[str]) -> Verdict:
    """A shell check's verdict: exit 0 passes; otherwise the end of its output is the notes."""
    if code == 0:
        return Verdict("pass", "")
    output = "\n".join(lines[-SHELL_CHECK_LINES:])
    if not output.strip():
        return Verdict("fail", f"Exited with code {code} (no output).")
    head = f"Exited with code {code}. The last lines of its output:\n"
    return Verdict("fail", head + output[len(head) - VERDICT_NOTES_MAX :])


def combine_feedback(failing: list[tuple[str, str]]) -> str:
    """The (stage name, notes) of a round's failing checks, as the next round's feedback."""
    text = "\n\n".join(f"### {name}\n{notes.strip()}" for name, notes in failing)
    if len(text) <= FEEDBACK_MAX:
        return text
    marker = "\n[truncated]"
    return text[: FEEDBACK_MAX - len(marker)] + marker


# ---------------------------------------------------------------- HTTP


class HttpError(Exception):
    """A 4xx answer (not retried)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"HTTP {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class RunGone(Exception):
    """401/410 from the journal: the run was taken away. Never write to the branch again."""


class AlreadyClaimed(RunGone):
    """409 from the journal: the job is bound to another worker instance."""


class ResultRejected(RunGone):
    """The server consumed the `result` record without applying it: the run didn't end, so the
    job failed to complete. Like a gone run: nothing more is published, the workdir is kept."""


class Unreachable(Exception):
    """Network errors or 5xx for RETRY_BUDGET_S (or the journal can't be written any more)."""


def _http_error(exc: urllib.error.HTTPError) -> HttpError:
    code, message = "http_error", str(exc.reason)
    try:
        body = json.loads(exc.read() or b"null")
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            code = str(body["error"].get("code", code))
            message = str(body["error"].get("message", message))
    except (OSError, ValueError):
        pass
    finally:
        exc.close()
    return HttpError(exc.code, code, message)


class Client:
    """JSON requests and raw PUTs with a bearer token, plus the worker instance's id when given.
    Retries network errors and 5xx with exponential backoff."""

    def __init__(self, server: str, token: str, instance: str = "") -> None:
        self._base = server.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        if instance:
            self._headers["X-AgentOS-Instance"] = instance
        host = urllib.parse.urlsplit(self._base).hostname or ""
        # A loopback server must never go through a configured proxy.
        handlers = [urllib.request.ProxyHandler({})] if host in _LOOPBACK else []
        self._opener = urllib.request.build_opener(*handlers)

    def post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        timeout_s: float | None = None,
        retry_budget_s: float | None = None,
    ) -> Any:
        """POST `body` and return the decoded answer. `timeout_s` (default HTTP_TIMEOUT_S) bounds
        each attempt; failures are retried for `retry_budget_s` (default RETRY_BUDGET_S, 0 = one
        attempt), then raise Unreachable. A 4xx raises HttpError at once."""
        data = json.dumps(body).encode("utf-8")
        return self._send("POST", path, data, "application/json", timeout_s, retry_budget_s)

    def get(self, path: str) -> Any:
        """GET and return the decoded answer, retried like `post`."""
        return self._send("GET", path, None, "application/json", None, None)

    def put(self, path: str, data: bytes) -> Any:
        """PUT raw bytes (a zip), retried like `post`; each attempt gets UPLOAD_TIMEOUT_S."""
        return self._send("PUT", path, data, "application/zip", UPLOAD_TIMEOUT_S, None)

    def _send(
        self,
        method: str,
        path: str,
        data: bytes | None,
        content_type: str,
        timeout_s: float | None,
        retry_budget_s: float | None,
    ) -> Any:
        timeout = HTTP_TIMEOUT_S if timeout_s is None else timeout_s
        deadline = time.monotonic() + (RETRY_BUDGET_S if retry_budget_s is None else retry_budget_s)
        delay = BACKOFF_START_S
        while True:
            request = urllib.request.Request(  # noqa: S310 -- the scheme is checked in parse_args
                self._base + path,
                data=data,
                method=method,
                headers={**self._headers, "Content-Type": content_type},
            )
            try:
                with self._opener.open(request, timeout=timeout) as resp:
                    raw = resp.read()
                return json.loads(raw) if raw.strip() else None
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    raise _http_error(exc) from None
                exc.close()
                problem = f"HTTP {exc.code}"
            except (OSError, http.client.HTTPException, ValueError) as exc:
                problem = f"{type(exc).__name__}: {exc}"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Unreachable(f"{method} {path}: {problem}")
            say(f"{method} {path} failed ({problem}); retrying in {delay:.1f}s")
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, BACKOFF_CAP_S)


@dataclass(frozen=True)
class Ack:
    """A Reporter's answer: the highest seq the destination holds (records after it are sent
    again), whether a cancel was requested, and the seqs it consumed as invalid."""

    seq: int
    cancel: bool
    rejected: tuple[int, ...]


class Reporter(Protocol):
    """Where the journal goes: the server (HttpReporter) or the mailbox outbox (Recorder)."""

    def send(self, records: list[dict[str, Any]]) -> Ack:
        """Deliver records in seq order; [] is a heartbeat. Raises RunGone (AlreadyClaimed
        included) or Unreachable."""
        ...

    def close(self) -> None: ...


class HttpReporter:
    """Live mode: posts records to /api/worker/journal. 401/410 raise RunGone, 409
    AlreadyClaimed; any other 4xx stops reporting as Unreachable, since the server would wait
    forever at the gap a dropped batch leaves."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def send(self, records: list[dict[str, Any]]) -> Ack:
        try:
            resp = self._client.post("/api/worker/journal", {"records": records})
        except HttpError as exc:
            if exc.status == 409:
                raise AlreadyClaimed(str(exc)) from exc
            if exc.status in (401, 410):
                raise RunGone(str(exc)) from exc
            raise Unreachable(f"the journal was refused: {exc}") from exc
        return Ack(resp["ack"], resp["cancel"] is True, tuple(resp["rejected"]))

    def close(self) -> None:
        """Nothing to release: every post is a request of its own."""


class Recorder:
    """Mailbox mode: writes the journal to the outbox as the worker API calls the server replays.

    Line 1 is `{"outbox": 1, "run_id": N}`, then one flushed `{"path", "body"}` line per call:
    consecutive display records as one events call (events numbered 1..N), `sync` as a sync call,
    `result` as complete; nothing else is written. Every record is acked at once, never with a
    cancel. A failed write raises Unreachable (and every later send too), so the run aborts."""

    def __init__(self, outbox: Path, run_id: int) -> None:
        self._lock = threading.Lock()
        self._events = 0  # the last event seq written
        self._fh: IO[bytes] | None = outbox.open("xb")  # never overwrite an earlier recording
        self._closed = "the outbox is closed"
        try:
            self._write(self._fh, {"outbox": 1, "run_id": run_id})
        except OSError:
            self.close()
            raise

    @staticmethod
    def _write(fh: IO[bytes], line: dict[str, Any]) -> None:
        fh.write(json.dumps(line).encode("utf-8") + b"\n")
        fh.flush()

    def send(self, records: list[dict[str, Any]]) -> Ack:
        calls: list[dict[str, Any]] = []
        for record in records:
            kind, data = record["type"], record["data"]
            if kind == "sync":
                body = {"stage_id": record["stage_id"], **data}
                calls.append({"path": "/api/worker/sync", "body": body})
            elif kind == "result":
                body = {k: v for k, v in data.items() if k != "pr_url" or v is not None}
                calls.append({"path": "/api/worker/complete", "body": body})
            elif kind not in ("started", "pushed", "sync_back"):
                if not calls or calls[-1]["path"] != "/api/worker/events":
                    calls.append({"path": "/api/worker/events", "body": {"events": []}})
                self._events += 1
                event = {"seq": self._events, "ts": record["ts"], "stage_id": record["stage_id"]}
                calls[-1]["body"]["events"].append({**event, "type": kind, "data": data})

        with self._lock:
            for call in calls:
                if self._fh is None:
                    raise Unreachable(f"record {call['path']}: {self._closed}")
                try:
                    self._write(self._fh, call)
                except OSError as exc:
                    self._shut(f"cannot write the outbox: {exc}")
                    raise Unreachable(f"record {call['path']}: {self._closed}") from exc
        return Ack(records[-1]["seq"] if records else 0, False, ())

    def close(self) -> None:
        with self._lock:
            self._shut("the outbox is closed")

    def _shut(self, why: str) -> None:
        """Close the file; every later call raises Unreachable with `why`."""
        if self._fh is not None:
            fh, self._fh, self._closed = self._fh, None, why
            with contextlib.suppress(OSError):
                fh.close()


# ---------------------------------------------------------------- events


class EventSink:
    """Numbers journal records from `seq` on and sends them through a Reporter from a background
    thread (SPEC §9.1), keeping each until it is acked: every send starts at `ack + 1`.

    Batches go out every FLUSH_INTERVAL_S, as soon as FLUSH_THRESHOLD are buffered, and at least
    every HEARTBEAT_S even when empty. `cancel` is set when the server asks for it; `failure`
    holds RunGone/Unreachable once sending is impossible.
    """

    def __init__(self, reporter: Reporter, job: str, seq: int) -> None:
        self._reporter = reporter
        self._job = job
        self._cond = threading.Condition()
        self._buf: list[dict[str, Any]] = []  # every record not acked yet, in seq order
        self._seq = seq - 1  # the last seq given out
        self._logs_dropped = False
        self._stopping = False
        self._thread = threading.Thread(target=self._loop, name="agentos-events", daemon=True)
        self.cancel = threading.Event()
        self.failure: RunGone | Unreachable | None = None

    def start(self) -> None:
        self._thread.start()

    def emit(self, type_: str, stage_id: str | None, data: dict[str, Any]) -> None:
        with self._cond:
            if type_ == "log" and self._seq >= MAX_EVENTS_PER_RUN - EVENT_RESERVE:
                if self._logs_dropped:
                    return
                self._logs_dropped = True
                type_ = "warning"
                data = {"message": "too many events in this run: further log output is dropped"}
            self._seq += 1
            record = {"seq": self._seq, "ts": _now(), "job": self._job, "type": type_}
            self._buf.append({**record, "stage_id": stage_id, "data": data})
            if len(self._buf) >= FLUSH_THRESHOLD:
                self._cond.notify_all()

    def log(self, stage_id: str | None, stream: str, text: str, truncated: bool = False) -> None:
        self.emit("log", stage_id, {"stream": stream, "text": text, "truncated": truncated})

    def warn(self, stage_id: str | None, message: str) -> None:
        say(f"warning: {message}")
        self.emit("warning", stage_id, {"message": message})

    def check(self) -> None:
        """Raise RunGone/Unreachable if the event poster hit one."""
        if self.failure is not None:
            raise self.failure

    def close(self) -> None:
        """Send what is buffered, stop the thread, close the Reporter, then raise any failure."""
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        if self._thread.is_alive():
            self._thread.join()
        self._reporter.close()
        self.check()

    def _loop(self) -> None:
        last_post = time.monotonic()
        while True:
            with self._cond:
                self._cond.wait_for(
                    lambda: self._stopping or len(self._buf) >= FLUSH_THRESHOLD,
                    timeout=FLUSH_INTERVAL_S,
                )
                batch = self._buf[:MAX_EVENTS_PER_POST]
                stopping = self._stopping
            if not batch and (stopping or time.monotonic() - last_post < HEARTBEAT_S):
                if stopping:
                    return
                continue
            try:
                ack = self._reporter.send(batch)
            except (RunGone, Unreachable) as exc:
                self.failure = exc
                return

            last_post = time.monotonic()
            if ack.cancel:
                self.cancel.set()
            if batch and ack.seq < batch[0]["seq"]:  # nothing applied: a resend would loop
                self.failure = Unreachable(f"record {batch[0]['seq']} is never acked ({ack})")
                return

            refused = [r for r in batch if r["type"] == "result" and r["seq"] in ack.rejected]
            if refused:
                self.failure = ResultRejected(f"the server rejected result {refused[0]['seq']}")
                return

            with self._cond:
                self._buf = [r for r in self._buf if r["seq"] > ack.seq]


# ---------------------------------------------------------------- live echo

# Every agent call's output is also printed on the worker's stderr as it arrives (the Actions job
# console, the client's run log) in a short readable form; the log events still carry
# each line as it was. AGENTOS_ECHO=0 in the worker's own environment turns it off.
ECHO_LINE_CHARS = 200  # a tool call, a tool result, a line nothing recognises
ECHO_TEXT_CHARS = 2000  # one block of agent text or reasoning
ECHO_DETAIL_KEYS = (  # the tool argument shown with a tool call, most telling first
    "file_path",
    "path",
    "notebook_path",
    "command",
    "pattern",
    "url",
    "query",
    "skill",
    "intent",
    "description",
    "prompt",
)
# Event types with nothing worth a console line: Claude stream-json, then Copilot JSONL.
ECHO_QUIET = frozenset(
    {
        "rate_limit_event",
        "stream_event",
        "user.message",
        "assistant.turn_start",
        "assistant.turn_end",
        "assistant.usage",
        "assistant.message_delta",
        "assistant.reasoning_delta",
        "tool.execution_partial_result",
        "tool.execution_progress",
        "session.usage_info",
    }
)
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _json_object(text: str) -> dict[str, Any] | None:
    if not text.lstrip().startswith("{"):
        return None
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _echo_clean(line: str) -> str:
    """One line without ANSI colours or control characters (a stray CR would start a new line in
    the job console, one that the stage prefix doesn't guard)."""
    return _CONTROL_RE.sub(" ", _ANSI_RE.sub("", line)).rstrip()


def _echo_text(text: str, label: str = "") -> list[str]:
    """A block of text cut to ECHO_TEXT_CHARS, blank lines dropped; a label goes on the first
    line and indents the rest."""
    lines = [s for s in map(_echo_clean, _clip(text.strip(), ECHO_TEXT_CHARS).splitlines()) if s]
    if not lines or not label:
        return lines
    return [f"{label}: {lines[0].strip()}", *(f"  {s}" for s in lines[1:])]


def _echo_short(text: str, label: str = "") -> list[str]:
    """One line: the first non-blank line of `text` cut to ECHO_LINE_CHARS, plus how many lines
    followed it. Blank text gives just the label."""
    lines = [s.strip() for s in map(_echo_clean, text.splitlines()) if s.strip()]
    if not lines:
        return [label] if label else []
    n = len(lines) - 1
    more = f" (+{n} line{'s' if n != 1 else ''})" if n else ""
    line = _clip(lines[0], ECHO_LINE_CHARS) + more
    return [f"{label}: {line}" if label else line]


def _echo_tool(name: object, args: object) -> str:
    """`tool NAME: DETAIL`, the detail being the most telling argument (a path, a command)."""
    label = "tool " + (_clip(_echo_clean(_as_str(name)).strip(), ECHO_LINE_CHARS) or "?")
    if isinstance(args, dict):
        for key in ECHO_DETAIL_KEYS:
            detail = _echo_short(_as_str(args.get(key)))
            if detail:
                return f"{label}: {detail[0]}"
        todos = args.get("todos")
        if isinstance(todos, list):
            return f"{label}: {len(todos)} todos"
    return label


def _content_blocks(message: object) -> list[dict[str, Any]]:
    content = message.get("content") if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _tool_result_text(content: object) -> str:
    """A Claude tool_result's content: a string, or a list of parts whose text parts count."""
    if isinstance(content, list):
        parts = (p.get("text") for p in content if isinstance(p, dict) and p.get("type") == "text")
        return "\n".join(_as_str(t) for t in parts)
    return _as_str(content)


class Echo:
    """Prints one agent call's output on stderr as it arrives, each line prefixed `[stage id]`.

    Claude stream-json and Copilot JSONL events become agent text, reasoning, tool calls, tool
    results and the closing line; known bookkeeping events print nothing, and anything else
    (plain text, other JSON) prints as plain text, cut short. An OnLine callback."""

    def __init__(self, stage_id: str) -> None:
        self.prefix = f"[{stage_id}] "
        self.reasoning = ""  # the last reasoning shown: Copilot can send it twice
        self.broken = False

    def __call__(self, _stream: str, text: str, truncated: bool) -> None:
        if self.broken:
            return
        try:
            lines = self.lines(text, truncated)
        except Exception as exc:  # cosmetic: a formatting bug must never stop the output reader
            self.broken = True
            lines = [f"(echo stopped: {type(exc).__name__})"]
        if lines:
            block = "".join(f"{self.prefix}{line}\n" for line in lines)
            with _STDERR_LOCK, contextlib.suppress(OSError, ValueError, AttributeError):
                sys.stderr.write(block)
                sys.stderr.flush()

    def lines(self, text: str, truncated: bool = False) -> list[str]:
        """The console lines (without the prefix) for one line of output."""
        event = None if truncated else _json_object(text)
        kind = event.get("type") if event is not None else None
        if event is None or not isinstance(kind, str):
            return _echo_short(text)
        raw = event.get("data")
        data: dict[str, Any] = raw if isinstance(raw, dict) else {}
        match kind:
            # Claude Code: --output-format stream-json --verbose
            case "system":
                return self._started(event.get("model")) if event.get("subtype") == "init" else []
            case "assistant":
                return self._claude_message(event.get("message"))
            case "user":
                return self._claude_tool_results(event.get("message"))
            case "result":
                return self._claude_result(event)
            # Copilot CLI: --output-format json, {"type": "<area>.<event>", "data": {...}}
            case "session.start":
                return self._started(data.get("selectedModel"))
            case "assistant.message":
                reasoning = self._reasoning(data.get("reasoningText"))
                return [*reasoning, *_echo_text(_as_str(data.get("content")))]
            case "assistant.reasoning":
                return self._reasoning(data.get("content"))
            case "assistant.intent":
                return _echo_short(_as_str(data.get("intent")), "intent")
            case "tool.execution_start":
                return [_echo_tool(data.get("toolName"), data.get("arguments"))]
            case "tool.execution_complete":
                return self._copilot_tool_result(data)
            case "session.error":
                return _echo_short(_as_str(data.get("message")), "error")
            case "abort":
                return _echo_short(_as_str(data.get("reason")), "aborted")
            case "session.shutdown":
                n = data.get("totalPremiumRequests")
                if type(n) not in (int, float):
                    return ["done"]
                return [f"done: {n:g} premium request{'s' if n != 1 else ''}"]
        if kind in ECHO_QUIET or event.get("ephemeral") is True:
            return []
        return _echo_short(text)

    def _started(self, model: object) -> list[str]:
        name = _echo_clean(_as_str(model)).strip()
        return [f"session started ({_clip(name, ECHO_LINE_CHARS)})" if name else "session started"]

    def _reasoning(self, text: object) -> list[str]:
        value = _as_str(text).strip()
        if not value or value == self.reasoning:
            return []
        self.reasoning = value
        return _echo_text(value, "thinking")

    def _claude_message(self, message: object) -> list[str]:
        out: list[str] = []
        for block in _content_blocks(message):
            kind = _as_str(block.get("type"))
            if kind == "text":
                out += _echo_text(_as_str(block.get("text")))
            elif kind == "thinking":
                out += self._reasoning(block.get("thinking"))
            elif kind.endswith("tool_use"):  # tool_use, server_tool_use, mcp_tool_use
                out.append(_echo_tool(block.get("name"), block.get("input")))
        return out

    def _claude_tool_results(self, message: object) -> list[str]:
        out: list[str] = []
        for block in _content_blocks(message):
            if block.get("type") == "tool_result":
                label = "tool error" if block.get("is_error") is True else "result"
                out += _echo_short(_tool_result_text(block.get("content")), label)
        return out

    def _claude_result(self, event: dict[str, Any]) -> list[str]:
        subtype = _as_str(event.get("subtype"))
        failed = event.get("is_error") is True or subtype not in ("", "success")
        parts = [subtype if subtype not in ("", "success") else "error" if failed else "success"]
        turns = event.get("num_turns")
        if type(turns) is int:
            parts.append(f"{turns} turn{'s' if turns != 1 else ''}")
        cost = event.get("total_cost_usd")
        if type(cost) in (int, float):
            parts.append(f"${cost:.4f}")
        line = "done: " + ", ".join(parts)
        detail = _echo_short(_as_str(event.get("result"))) if failed else []
        return [f"{line}: {detail[0]}" if detail else line]

    def _copilot_tool_result(self, data: dict[str, Any]) -> list[str]:
        if data.get("success") is False:
            error = data.get("error")
            message = error.get("message") if isinstance(error, dict) else error
            return _echo_short(_as_str(message), "tool error")
        result = data.get("result")
        content = result.get("content") if isinstance(result, dict) else result
        return _echo_short(_as_str(content), "result")


# ---------------------------------------------------------------- processes


@dataclass(frozen=True)
class ProcResult:
    exit_code: int | None
    stopped: str | None = None  # "timeout" | "cancel" | "budget" | "abort"
    error: str | None = None  # the program could not be started


OnLine = Callable[[str, str, bool], None]  # (stream, text, truncated)


if sys.platform == "win32":

    class _BasicLimits(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        )

    class _IoCounters(ctypes.Structure):
        _fields_ = tuple(
            (name, ctypes.c_uint64)
            for name in ("Reads", "Writes", "Others", "ReadBytes", "WriteBytes", "OtherBytes")
        )

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )

    _K32: ctypes.WinDLL | None = None

    def _kernel32() -> ctypes.WinDLL:
        global _K32  # one kernel32 handle per process, prototypes set once
        if _K32 is None:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.restype = ctypes.c_void_p
            k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
            k32.SetInformationJobObject.argtypes = (
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.POINTER(_ExtendedLimits),
                ctypes.c_uint32,
            )
            k32.OpenProcess.restype = ctypes.c_void_p
            k32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
            k32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
            k32.TerminateJobObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            k32.CloseHandle.argtypes = (ctypes.c_void_p,)
            _K32 = k32
        return _K32

    def _job_for(pid: int) -> int | None:
        """A job object (killed when closed) holding `pid`, or None if that isn't possible."""
        k32 = _kernel32()
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        process = k32.OpenProcess(0x0100 | 0x0001, 0, pid)  # PROCESS_SET_QUOTA | _TERMINATE
        assigned = bool(process) and bool(k32.AssignProcessToJobObject(job, process))
        if process:
            k32.CloseHandle(process)
        if not (ok and assigned):
            k32.CloseHandle(job)
            return None
        return int(job)


class _Job:
    """Windows: a job object holding the stage tree, so orphaned children die with it (taskkill
    /T can't find a child whose parent already exited). A no-op elsewhere (killpg covers it)."""

    def __init__(self, pid: int) -> None:
        self._handle: int | None = None
        if sys.platform == "win32":
            try:
                self._handle = _job_for(pid)
            except OSError:
                self._handle = None

    def kill(self) -> None:
        if sys.platform == "win32" and self._handle is not None:
            _kernel32().TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if sys.platform == "win32" and self._handle is not None:
            _kernel32().CloseHandle(self._handle)
        self._handle = None


def _spawn(
    argv: list[str], cwd: Path, env: dict[str, str], with_stdin: bool
) -> subprocess.Popen[bytes]:
    stdin = subprocess.PIPE if with_stdin else subprocess.DEVNULL
    pipe = subprocess.PIPE
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        return subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=stdin, stdout=pipe, stderr=pipe, creationflags=flags
        )
    return subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=stdin, stdout=pipe, stderr=pipe, start_new_session=True
    )


def kill_tree(proc: subprocess.Popen[bytes], job: _Job) -> None:
    """Kill the stage and everything it started (its process group / job)."""
    if sys.platform == "win32":
        if proc.poll() is None:
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True,
                    timeout=KILL_TIMEOUT_S,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
        job.kill()
    else:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)


def _pump(pipe: IO[bytes], stream: str, cap: int, on_line: OnLine) -> None:
    """Emit each line of `pipe`; lines longer than `cap` bytes are cut and flagged."""
    skipping = False  # inside the remainder of a cut line
    while True:
        try:
            chunk = pipe.readline(cap + 2)
        except (OSError, ValueError):
            return
        if not chunk:
            return
        complete = chunk.endswith(b"\n")
        if skipping:
            skipping = not complete
            continue
        line = chunk
        if complete:
            line = line[:-1]
            if line.endswith(b"\r"):
                line = line[:-1]
        truncated = len(line) > cap
        if truncated:
            line = line[:cap]
            skipping = not complete
        on_line(stream, line.decode("utf-8", "replace"), truncated)


def _feed(pipe: IO[bytes], data: bytes) -> None:
    with contextlib.suppress(OSError, ValueError):
        pipe.write(data)
    with contextlib.suppress(OSError, ValueError):
        pipe.close()


def run_process(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin: str | None,
    timeout_s: float,
    caps: tuple[int, int],
    on_line: OnLine,
    interrupt: Callable[[], str | None],
) -> ProcResult:
    """Run one stage program in its own process group and stream its output.

    `caps` are the (stdout, stderr) line caps in bytes. `interrupt` is polled every POLL_S and
    returns "cancel"/"abort" to stop the stage. Whatever happens, the whole tree is killed at the
    end (background children included) and output readers are abandoned after READER_JOIN_S.
    """
    try:
        proc = _spawn(argv, cwd, env, stdin is not None)
    except OSError as exc:
        return ProcResult(None, error=f"cannot start {argv[0]}: {exc}")
    job = _Job(proc.pid)
    readers: list[threading.Thread] = []
    stopped: str | None = None
    try:
        for pipe, stream, cap in (
            (proc.stdout, "stdout", caps[0]),
            (proc.stderr, "stderr", caps[1]),
        ):
            if pipe is not None:
                reader = threading.Thread(
                    target=_pump, args=(pipe, stream, cap, on_line), daemon=True
                )
                reader.start()
                readers.append(reader)
        # Readers first, then stdin: a child that fills its stdout before reading stdin can't
        # deadlock us. The writer is a thread so a child that never reads stdin can't either.
        if stdin is not None and proc.stdin is not None:
            threading.Thread(
                target=_feed, args=(proc.stdin, stdin.encode("utf-8")), daemon=True
            ).start()
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                proc.wait(timeout=POLL_S)
                break
            except subprocess.TimeoutExpired:
                pass
            stopped = interrupt()
            if stopped is None and time.monotonic() >= deadline:
                stopped = "timeout"
            if stopped is not None:
                break
    finally:
        kill_tree(proc, job)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=KILL_TIMEOUT_S)
        join_deadline = time.monotonic() + READER_JOIN_S
        for reader in readers:
            reader.join(max(0.0, join_deadline - time.monotonic()))
        job.close()
    abandoned = sum(r.is_alive() for r in readers)
    if abandoned:
        say(f"abandoned {abandoned} output reader(s) still blocked on an orphan-held pipe")
    return ProcResult(proc.returncode, stopped=stopped)


def cmd_guard_error(program: str, argv: list[str]) -> str | None:
    """SPEC §9.3 Windows guard: cmd.exe would mangle (or execute) these characters."""
    if program.lower().endswith((".cmd", ".bat")) and any(
        ch in CMD_UNSAFE for arg in argv for ch in arg
    ):
        return "use prompt_via=stdin or a native binary"
    return None


def shell_prefix() -> list[str]:
    """The shell argv for shell stages; AGENTOS_SHELL overrides (e.g. `bash -c`)."""
    override = os.environ.get("AGENTOS_SHELL", "").strip()
    if override:
        if os.name == "nt":
            parts = shlex.split(override, posix=False)
            return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p for p in parts]
        return shlex.split(override)
    if os.name == "nt":
        return ["pwsh", "-NoProfile", "-Command"]
    return ["bash", "-lc"]


def remove_tree(path: Path) -> None:
    """shutil.rmtree that also removes read-only files (git packs on Windows)."""

    def retry(func: Callable[..., object], target: str, _exc: object) -> None:
        Path(target).chmod(stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


# ---------------------------------------------------------------- context sync (SPEC §9.4.1)


@dataclass
class SyncBatch:
    files: list[dict[str, Any]]
    warnings: list[str]


def _walk_files(root: Path, rel_dir: str) -> tuple[list[str], list[str]]:
    """POSIX paths of the regular files under `rel_dir`, and of what was skipped there: links,
    special files, folders that can't be listed, and files or folders whose path breaks the path
    rules. Never follows links."""
    top, err = resolve_inside(root, rel_dir)
    if err:
        return [], [rel_dir]
    found: list[str] = []
    skipped: list[str] = []
    stack = [(rel_dir, top)]
    while stack:
        rel, here = stack.pop()
        try:
            children = sorted(here.iterdir())
        except FileNotFoundError:
            continue
        except OSError:
            skipped.append(f"{rel}/")
            continue
        for child in children:
            child_rel = f"{rel}/{child.name}"
            try:
                st = child.lstat()
            except OSError:
                continue
            if path_error(child_rel) or is_link(st):
                # A name that isn't valid UTF-8 is shown escaped, so the warning can be sent.
                skipped.append(child_rel.encode("utf-8", "backslashreplace").decode("utf-8"))
            elif stat.S_ISREG(st.st_mode):
                found.append(child_rel)
            elif stat.S_ISDIR(st.st_mode):
                stack.append((child_rel, child))
            else:
                skipped.append(child_rel)
    return sorted(found), sorted(skipped)


class SyncState:
    """What this run has sent of its context (`known`), so sync sends only what changed.

    After each stage it reads exactly the write context entries and the notes (block files: only
    the block) and sends those that changed since they were last sent; a deleted replace file goes
    as `content: null`. Nothing else is sent: other files under `workspace.protect` are named in
    one "not kept" warning.
    """

    def __init__(self, package: Package) -> None:
        self.package = package
        self.sync_files = {f.path: f for f in package.files if f.access == "write"}
        self.declared_lower = {f.path.lower() for f in package.files}
        self.known: dict[str, str | None] = {p: f.content for p, f in self.sync_files.items()}
        self.unsynced: set[str] = set()  # declared files that couldn't be written
        self.rewritten: set[str] = set()  # notes already warned about

    def _declared_content(self, f: ContextFile, data: bytes) -> tuple[str, str | None]:
        """(content to sync, why not) for a declared write file's bytes."""
        limit = self.package.limits["max_file_bytes"]
        if f.mode == "block":
            try:
                content = block_read(data, str(f.block))
            except BlockError as exc:
                return "", str(exc)
        else:
            decoded = _decode(data)
            if decoded is None:
                return "", "is not valid UTF-8"
            content = decoded
        if len(content.encode("utf-8")) > limit:
            return "", f"is over {limit} bytes"
        return content, None

    def collect(self, root: Path) -> SyncBatch:
        batch = SyncBatch([], [])
        for path, f in self.sync_files.items():
            if path in self.unsynced:
                continue
            data, err = read_regular(root, path)
            if err:
                batch.warnings.append(f"not synced: {path} {err}")
                continue
            if data is None:
                if f.mode == "block":
                    batch.warnings.append(f"not synced: {path} is gone, and its agentos block too")
                elif self.known.get(path) is not None:
                    batch.files.append({"path": path, "content": None, "mode": "replace"})
                    self.known[path] = None
                continue
            content, err = self._declared_content(f, data)
            if err:
                batch.warnings.append(f"not synced: {path}: {err}")
                continue
            if (
                f.kind == "note"
                and path not in self.rewritten
                and not content.startswith(f.content)
            ):
                self.rewritten.add(path)
                batch.warnings.append(f"{path} was rewritten, not appended to")
            if self.known.get(path) != content:
                batch.files.append({"path": path, "content": content, "mode": f.mode})
                self.known[path] = content

        not_kept: list[str] = []
        for folder in self.package.workspace["protect"]:
            files, skipped = _walk_files(root, folder)
            not_kept += [p for p in files if p.lower() not in self.declared_lower] + skipped
        if not_kept:
            more = f" and {len(not_kept) - 20} more" if len(not_kept) > 20 else ""
            batch.warnings.append(f"not kept: {', '.join(not_kept[:20])}{more}")
        return batch


def write_entry(root: Path, f: ContextFile) -> str | None:
    """Materialise one context file or note (SPEC §9.3). Returns why it was skipped, or None."""
    target, err = resolve_inside(root, f.path)
    if err:
        return err
    try:
        existing: bytes | None = None
        try:
            st = target.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(st.st_mode):
                return "the target exists and is not a regular file"
            if f.mode == "block":
                existing = target.read_bytes()
        try:
            data = (
                block_write(existing, str(f.block), f.content)
                if f.mode == "block"
                else f.content.encode()
            )
        except BlockError as exc:
            return str(exc)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        return f"cannot write: {exc.strerror or exc}"
    return None


# ---------------------------------------------------------------- GitHub API publication
#
# On GitHub Actions (GITHUB_ACTIONS=true, GH_TOKEN set) the branch is published through the Git
# Data API (blobs, trees, commits, refs) instead of `git push`, because CI may only write to GitHub
# through the REST API there. Everywhere else the worker pushes with git.

_REPO_PART_RE = re.compile(r"[A-Za-z0-9._-]+")
_SCP_RE = re.compile(r"(?:[^@/:]+@)?([^@/:]+):(.+)")
_DIFF_META_RE = re.compile(r":([0-7]{6}) ([0-7]{6}) ([0-9a-f]{40,64}) ([0-9a-f]{40,64}) ([AMDT])")
_BLOB_MODES = frozenset({"100644", "100755", "120000"})
_SUBMODULE_MODE = "160000"


class PublishError(Exception):
    """Publication failed; the message is the stage's failure reason."""


def api_publish(env: Mapping[str, str]) -> bool:
    """Publish through the GitHub API: on Actions with a job token, else `git push`."""
    return env.get("GITHUB_ACTIONS") == "true" and bool(env.get("GH_TOKEN", "").strip())


def _host(netloc_host: str | None, port: int | None) -> str:
    host = (netloc_host or "").lower()
    return f"{host}:{port}" if port is not None else host


def github_repo(repo_url: str, server_url: str) -> tuple[str | None, str | None]:
    """("owner/repo", None) for an https or scp-style URL on `server_url`'s host, else (None,
    why). Userinfo, a trailing `/` and `.git` are dropped; an error never echoes userinfo."""
    try:
        server = urllib.parse.urlsplit(server_url)
        server_host = _host(server.hostname, server.port)
        if "://" in repo_url:
            url = urllib.parse.urlsplit(repo_url)
            if url.scheme.lower() != "https":
                return None, "the repo URL must be https or scp-style (git@host:owner/repo)"
            if url.query or url.fragment:
                return None, "the repo URL has a query or fragment"
            host, path = _host(url.hostname, url.port), url.path
        else:
            match = _SCP_RE.fullmatch(repo_url)
            if match is None:
                return None, "the repo URL must be https or scp-style (git@host:owner/repo)"
            host, path = match[1].lower(), match[2]
    except ValueError:
        return None, "the repo URL or GITHUB_SERVER_URL cannot be parsed"

    if not server_host or host != server_host:
        return None, f"the repo host {host or '(none)'} is not {server_host or '(none)'}"

    parts = path.strip("/").removesuffix(".git").split("/")
    if len(parts) != 2 or not all(
        _REPO_PART_RE.fullmatch(p) and p not in (".", "..") for p in parts
    ):
        return None, "the repo URL's path is not owner/repo"
    return f"{parts[0]}/{parts[1]}", None


@dataclass(frozen=True)
class DiffEntry:
    """One `git diff-tree -r -z --no-renames` line. A missing side has mode 000000."""

    old_mode: str
    new_mode: str
    old_sha: str
    new_sha: str
    status: str
    path: str

    @property
    def deleted(self) -> bool:
        return self.status == "D"

    @property
    def needs_content(self) -> bool:
        """A changed file's new content must reach GitHub (inline or as a blob)."""
        return (
            not self.deleted and self.new_mode != _SUBMODULE_MODE and self.old_sha != self.new_sha
        )


def parse_diff_tree(out: bytes) -> list[DiffEntry]:
    fields = out.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise PublishError("push failed: unexpected git diff-tree output")
    entries: list[DiffEntry] = []
    for meta, raw_path in zip(fields[::2], fields[1::2], strict=True):
        match = _DIFF_META_RE.fullmatch(meta.decode("ascii", "replace"))
        path = _decode(raw_path)
        if match is None or path is None:
            raise PublishError("push failed: unexpected git diff-tree output (or a non-UTF-8 path)")
        old_mode, new_mode, old_sha, new_sha, status = match.groups()
        entries.append(DiffEntry(old_mode, new_mode, old_sha, new_sha, status, path))
    return entries


def inline_text(data: bytes) -> str | None:
    """`data` as a tree entry's inline `content`: UTF-8, no NUL, at most INLINE_MAX_BYTES."""
    if len(data) > INLINE_MAX_BYTES or b"\0" in data:
        return None
    return _decode(data)


def tree_entry(d: DiffEntry, data: bytes | None) -> tuple[dict[str, Any], bytes | None]:
    """The `POST /git/trees` entry for `d`, plus the bytes to upload as a blob first (None when
    the entry needs no upload). `data` is the new content when `d.needs_content`."""
    mode = d.old_mode if d.deleted else d.new_mode
    if mode == _SUBMODULE_MODE:
        kind = "commit"
    elif mode in _BLOB_MODES:
        kind = "blob"
    else:
        raise PublishError(f"push failed: {d.path} has an unsupported mode {mode}")
    entry: dict[str, Any] = {"path": d.path, "mode": mode, "type": kind}

    if d.deleted:
        entry["sha"] = None
        return entry, None
    if not d.needs_content:
        entry["sha"] = d.new_sha
        return entry, None
    if data is None:
        raise PublishError(f"push failed: no content for {d.path}")
    text = inline_text(data)
    if text is not None:
        entry["content"] = text
        return entry, None
    entry["sha"] = d.new_sha
    return entry, data


def retry_wait(headers: Mapping[str, str], now: float) -> float | None:
    """Seconds GitHub asks us to wait (`Retry-After`, or an exhausted rate limit's reset), at
    least GITHUB_MIN_WAIT_S (a reset already past by our clock must not mean an instant retry),
    or None when it asks for nothing."""
    after = headers.get("retry-after", "").strip()
    if after.isdigit():
        return max(GITHUB_MIN_WAIT_S, float(after))
    reset = headers.get("x-ratelimit-reset", "").strip()
    if headers.get("x-ratelimit-remaining", "").strip() == "0" and reset.isdigit():
        return max(GITHUB_MIN_WAIT_S, float(reset) - now)
    return None


def classify(status: int | None, message: str, wait: float | None, ref_write: bool) -> str:
    """What a failed GitHub call means: "rejected" (push rejected), "retry", "check" (a ref write
    whose outcome is unknown: read the ref back) or "failed" (push failed: GitHub's message).
    `status` is None for a timeout or a network error."""
    low = message.lower()
    if status in (403, 429) and (wait is not None or status == 429):
        return "retry"
    if status == 422 and ("not a fast forward" in low or "reference already exists" in low):
        return "rejected"
    if status is None or status >= 500:
        return "check" if ref_write else "retry"
    if status == 422 and ref_write:
        return "check"
    return "failed"


def scrub(text: str, secrets_: tuple[str, ...]) -> str:
    """Redact the secrets first (so a cut never leaves half of one), then one line, clipped."""
    for secret in secrets_:
        if secret:
            text = text.replace(secret, "***")
    return _clip(" ".join(text.split()), MAX_GITHUB_MESSAGE_CHARS)


class GitHubError(Exception):
    """A GitHub call failed for good. `action` is classify()'s answer; the message is GitHub's,
    scrubbed and clipped. Never a RunGone or Unreachable: it only ever fails the stage."""

    def __init__(self, status: int | None, message: str, action: str) -> None:
        super().__init__(f"HTTP {status}: {message}" if status is not None else message)
        self.status = status
        self.message = message
        self.action = action


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urllib would forward Authorization to any host it is redirected to: follow nothing."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


class GitHub:
    """The Git Data API of one repo with the job's token. Separate from Client/WorkerApi on
    purpose: a GitHub failure is a stage failure, never a lost run or an unreachable server."""

    def __init__(self, api_url: str, token: str, repo: str) -> None:
        self.repo = repo
        self._base = f"{api_url.rstrip('/')}/repos/{repo}"
        self._token = token
        self._opener = urllib.request.build_opener(_NoRedirect())

    def call(self, method: str, path: str, body: Any = None, *, ref_write: bool = False) -> Any:
        """Send one request under /repos/<owner>/<repo> and return the decoded answer. What
        classify() calls "retry" is retried within GITHUB_RETRY_BUDGET_S; anything else raises
        GitHubError."""
        data = None if body is None else json.dumps(body).encode("utf-8")
        deadline = time.monotonic() + GITHUB_RETRY_BUDGET_S
        delay = BACKOFF_START_S
        while True:
            request = urllib.request.Request(  # noqa: S310 -- GITHUB_API_URL is checked to be https
                self._base + path,
                data=data,
                method=method,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                    "User-Agent": "agentos-worker",
                    "X-GitHub-Api-Version": GITHUB_API_VERSION,
                },
            )
            status: int | None = None
            wait: float | None = None
            try:
                with self._opener.open(request, timeout=HTTP_TIMEOUT_S) as resp:
                    raw = resp.read()
                return json.loads(raw) if raw.strip() else None
            except urllib.error.HTTPError as exc:
                status, message = exc.code, self._message(exc)
                headers = {k.lower(): v for k, v in exc.headers.items()}
                wait = retry_wait(headers, time.time())
            except (OSError, http.client.HTTPException, ValueError) as exc:
                message = f"{type(exc).__name__}: {exc}"
            message = scrub(message, (self._token,))

            action = classify(status, message, wait, ref_write)
            remaining = deadline - time.monotonic()
            pause = delay if wait is None else wait
            if action != "retry" or pause > remaining:
                raise GitHubError(status, message, action)
            say(f"GitHub {method} {path} failed ({message}); retrying in {pause:.1f}s")
            time.sleep(pause)
            delay = min(delay * 2, BACKOFF_CAP_S)

    @staticmethod
    def _message(exc: urllib.error.HTTPError) -> str:
        if 300 <= exc.code < 400:
            return "GitHub redirected the request: fix the ticket's repo URL"
        message = str(exc.reason)
        try:
            body = json.loads(exc.read() or b"null")
            if isinstance(body, dict) and isinstance(body.get("message"), str):
                message = body["message"]
        except (OSError, ValueError):
            pass
        finally:
            exc.close()
        return message


class GitRunner(Protocol):
    """Runs git in the workspace (Run._git)."""

    def __call__(self, *args: str, stdin: bytes | None = None) -> GitResult: ...


def _git_out(git: GitRunner, *args: str, stdin: bytes | None = None) -> bytes:
    r = git(*args, stdin=stdin)
    if r.code:
        raise PublishError(f"push failed: git {args[0]} failed: {r.tail()}")
    return r.out


def _read_blobs(git: GitRunner, shas: list[str]) -> dict[str, bytes]:
    """Blob contents from the object DB (`git cat-file --batch`), never the working tree."""
    if not shas:
        return {}
    out = _git_out(git, "cat-file", "--batch", stdin="".join(f"{s}\n" for s in shas).encode())
    blobs: dict[str, bytes] = {}
    pos = 0
    for sha in shas:
        end = out.find(b"\n", pos)
        header = out[pos:end].split() if end >= 0 else []
        if len(header) != 3 or header[1] != b"blob" or not header[2].isdigit():
            raise PublishError(f"push failed: cannot read blob {sha[:12]}")
        size = int(header[2])
        blobs[sha] = out[end + 1 : end + 1 + size]
        pos = end + 1 + size + 1
    return blobs


def _github(github: GitHub, method: str, path: str, body: Any) -> Any:
    try:
        return github.call(method, path, body)
    except GitHubError as exc:
        raise PublishError(f"push failed: {exc.message}") from None


def _head_commit(git: GitRunner) -> tuple[str, dict[str, str]]:
    """HEAD's message and author: what the one published commit carries."""
    out = _git_out(git, "log", "-1", "--format=%an%x00%ae%x00%aI%x00%B")
    parts = [p for p in (_decode(f) for f in out.split(b"\0", 3)) if p is not None]
    if len(parts) != 4:
        raise PublishError("push failed: HEAD's message or author is not UTF-8")

    name, email, date, message = parts
    return message.rstrip("\n") + "\n", {"name": name, "email": email, "date": date}


def _tree_entries(git: GitRunner, github: GitHub, base: str, head: str) -> list[dict[str, Any]]:
    """The tree entries that turn `base`'s tree into `head`'s, uploading what can't go inline."""
    diffs = parse_diff_tree(_git_out(git, "diff-tree", "-r", "-z", "--no-renames", base, head))
    blobs = _read_blobs(git, list(dict.fromkeys(d.new_sha for d in diffs if d.needs_content)))

    entries: list[dict[str, Any]] = []
    uploaded: set[str] = set()
    for d in diffs:
        entry, upload = tree_entry(d, blobs.get(d.new_sha))
        if upload is not None and d.new_sha not in uploaded:
            content = base64.b64encode(upload).decode("ascii")
            blob = _github(github, "POST", "/git/blobs", {"content": content, "encoding": "base64"})
            if blob.get("sha") != d.new_sha:
                raise PublishError(f"publish mismatch: blob {d.path}")
            uploaded.add(d.new_sha)
        entries.append(entry)
    return entries


def _write_ref(github: GitHub, branch: str, sha: str, exists: bool) -> None:
    """Move the branch to `sha`, never forced. An unknown outcome is settled by reading it back."""
    quoted = urllib.parse.quote(branch, safe="/")
    body: dict[str, Any]
    try:
        if exists:
            body = {"sha": sha, "force": False}
            github.call("PATCH", f"/git/refs/heads/{quoted}", body, ref_write=True)
        else:
            body = {"ref": f"refs/heads/{branch}", "sha": sha}
            github.call("POST", "/git/refs", body, ref_write=True)
    except GitHubError as exc:
        failure = exc
    else:
        return

    if failure.action == "rejected":
        raise PublishError(PUSH_REJECTED)
    if failure.action == "check":
        try:
            ref = github.call("GET", f"/git/ref/heads/{quoted}")
        except GitHubError:
            ref = None
        if isinstance(ref, dict) and ref.get("object", {}).get("sha") == sha:
            return
    raise PublishError(f"push failed: {failure.message}")


def publish_branch(
    git: GitRunner,
    github: GitHub,
    branch: str,
    pushed_head: str,
    exists: bool,
    before_ref: Callable[[], None] = lambda: None,
) -> tuple[str, int]:
    """Publish HEAD to `branch` through the Git Data API as ONE commit on `pushed_head` (the
    branch's remote commit): the changes as a tree on its tree, with HEAD's message and author,
    then one ref write (`exists`: PATCH, else POST). `before_ref` runs right before the ref
    write. Merge commits are refused: squashed, a merged base would read as the branch's own
    work. Returns (the branch's new remote sha, local commits it covers); raises PublishError."""
    head = _git_out(git, "rev-parse", "HEAD").decode().strip()
    r = git("merge-base", "--is-ancestor", pushed_head, head)
    if r.code == 1:
        raise PublishError(PUSH_REJECTED)
    if r.code:
        raise PublishError(f"push failed: git merge-base failed: {r.tail()}")

    span = f"{pushed_head}..{head}"
    if _git_out(git, "rev-list", "--merges", span).strip():
        raise PublishError("push failed: merge commits can't be published through the GitHub API")
    count = int(_git_out(git, "rev-list", "--count", span))

    tree = _git_out(git, "rev-parse", f"{head}^{{tree}}").decode().strip()
    entries = _tree_entries(git, github, pushed_head, head)
    if entries:
        base = _git_out(git, "rev-parse", f"{pushed_head}^{{tree}}").decode().strip()
        answer = _github(github, "POST", "/git/trees", {"base_tree": base, "tree": entries})
        if answer.get("sha") != tree:
            raise PublishError("publish mismatch: GitHub's tree differs from HEAD's")

    message, author = _head_commit(git)
    body = {"message": message, "tree": tree, "parents": [pushed_head], "author": author}
    sha = str(_github(github, "POST", "/git/commits", body)["sha"])

    before_ref()
    _write_ref(github, branch, sha, exists)
    return sha, count


def track_published(git: GitRunner, branch: str, sha: str) -> str | None:
    """After a publish: move the local branch onto the remote commit `sha` (same tree) and make
    git see it as pushed (remote-tracking ref, upstream). Returns why it failed, or None."""
    head = git("rev-parse", "HEAD")
    if head.code or head.out.decode().strip() != sha:
        r = git("fetch", "-q", "--no-tags", "origin", sha)
        if r.code:
            return f"published, fetch failed: {r.tail()}"
        r = git("reset", "-q", "--soft", sha)
        if r.code:
            return f"published, fetch failed: git reset: {r.tail()}"
    for args in (
        ("update-ref", f"refs/remotes/origin/{branch}", sha),
        ("config", f"branch.{branch}.remote", "origin"),
        ("config", f"branch.{branch}.merge", f"refs/heads/{branch}"),
    ):
        r = git(*args)
        if r.code:
            return f"published, fetch failed: git {args[0]}: {r.tail()}"
    return None


# ---------------------------------------------------------------- the run


@dataclass(frozen=True)
class GitResult:
    code: int
    out: bytes
    err: bytes

    def tail(self) -> str:
        text = (self.err or self.out).decode("utf-8", "replace").strip()
        return text.splitlines()[-1] if text else f"exit code {self.code}"

    def names(self) -> list[str]:
        """NUL-separated path output (`-z`)."""
        return [p for p in self.out.decode("utf-8", "replace").split("\0") if p]


class Run:
    """Executes one bound job: workspace, files, stages, after-stage steps, the result. It reports
    through `events`; `upload` (live mode only) takes the sync_back zip."""

    def __init__(
        self, events: EventSink, package: Package, workdir: Path, upload: Client | None
    ) -> None:
        self.events = events
        self.upload = upload
        self.m = package
        self.workdir = workdir
        self.repo = package.workspace["mode"] == "repo"
        self.branch = str(package.workspace["branch"])
        self.max_line_bytes = package.limits["max_line_bytes"]
        self.sync = SyncState(package)
        self.owns_workdir = False
        self.ready = False  # workspace set up (offline commit + push is possible)
        self.start_head = ""
        self.pushed_head = ""
        self.current_stage: str | None = None
        self.index = {s.id: i for i, s in enumerate(package.stages)}
        self.protected = [
            *dict.fromkeys(f.path for f in package.files),
            *package.workspace["protect"],
            AGENTOS_DIR,
        ]
        self._protected_lower = [p.lower() for p in self.protected]
        self.echo = os.environ.get("AGENTOS_ECHO", "").strip() != "0"  # see Echo
        self.api_mode = self.repo and api_publish(os.environ)  # publish through the GitHub API
        self.github: GitHub | None = None  # set up by _setup_github in API mode
        self.branch_exists = False  # the branch was on the remote at clone or has been published
        self.not_after = datetime.fromisoformat(package.job["not_after"])  # never publish after
        self.deadline = time.monotonic() + package.job["budget_s"]

        base = {k: v for k, v in os.environ.items() if k.upper() != "AGENTOS_TOKEN"}
        base.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
        self.push_marker = secrets.token_hex(16)  # in the worker's git env, not the stages'
        self.git_env = {**base, "AGENTOS_PUSH": self.push_marker}
        self.stage_env = {
            **base,
            **package.env,
            "AGENTOS_RUN_ID": str(package.run_id),
            "AGENTOS_TICKET_KEY": package.key,
        }

    # ------------------------------------------------------------ top level

    def execute(self) -> int:
        try:
            return self._execute()
        except ResultRejected as exc:
            say(f"could not complete ({exc}): the workdir is kept, the reaper ends the run")
            return 1
        except RunGone as exc:
            say(f"the run was taken away ({exc}): no commit, no push")
            return 1
        except Unreachable as exc:
            say(f"server unreachable ({exc}): saving the work, then exiting")
            if self.repo and self.ready:
                self._save_offline()
            return 1
        except Exception as exc:
            say(f"worker error:\n{traceback.format_exc()}")
            try:
                self._finish("failed", self.current_stage, f"worker error: {exc}")
            except Exception as again:
                say(f"could not report the failure: {again}")
            return 1

    def _execute(self) -> int:
        problem = self._prepare_workdir()
        if problem:
            return self._finish("failed", None, problem)
        problem = self._setup_workspace()
        if problem:
            return self._finish("failed", None, problem)
        clash = [
            theirs
            for f in self.m.files
            if f.kind == "agent" and (theirs := existing_entry(self.workdir, f.path)) is not None
        ]
        if clash:
            return self._finish("failed", None, f"the repo already has agent {', '.join(clash)}")
        self._write_files()
        sync_back, problem = resolve_inside(self.workdir, self.m.sync_back["dir"])
        try:
            if not problem:
                sync_back.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            problem = str(exc)
        if problem:
            self.events.warn(None, f"cannot create {self.m.sync_back['dir']}: {problem}")
        if self.repo:
            self._protect()
        self.ready = True
        for message in self.m.warnings:
            self.events.warn(None, message)
        stages = self.m.stages
        blocks = {self.index[loop.start]: loop for loop in self.m.loops}
        entry = self.index[self.m.start_stage] if self.m.start_stage else 0
        i = 0
        while i < len(stages):
            loop = blocks.get(i)
            if loop is None:
                done, _ = self._step(stages[i], 1, None, "")
                i += 1
            else:
                last = self.index[loop.end]
                done = self._run_loop(loop, i, last, entry if i <= entry <= last else i)
                i = last + 1
            if done is not None:
                return done
        return self._finish("completed", None, None)

    def _run_loop(self, loop: Loop, first: int, last: int, entry: int) -> int | None:
        """Run a loop block in rounds until no check fails. Round 1 starts at `entry`, later
        rounds at the block start. The finished run's exit code, or None to carry on after it."""
        feedback = ""
        for round_ in range(1, loop.max_rounds + 1):
            failing: list[tuple[Stage, str]] = []
            for stage in self.m.stages[(entry if round_ == 1 else first) : last + 1]:
                done, verdict = self._step(stage, round_, loop, feedback)
                if done is not None:
                    return done
                if verdict is not None and verdict.verdict == "fail":
                    failing.append((stage, verdict.notes))
            if not failing:
                return None
            n = loop.max_rounds
            if round_ == n:
                error = f"Checks still failing after {n} round{'s' if n != 1 else ''}"
                return self._finish("failed", failing[0][0].id, error)
            feedback = combine_feedback([(stage.name, notes) for stage, notes in failing])
            say(f"round {round_} of {n}: {len(failing)} check(s) failed, going round again")
        return None

    def _step(
        self, stage: Stage, round_: int, loop: Loop | None, feedback: str
    ) -> tuple[int | None, Verdict | None]:
        """One stage execution. (exit code, None) once the run is finished, else (None, the
        check's verdict if it gave one)."""
        self.events.check()
        if self.events.cancel.is_set():
            return self._finish("cancelled", stage.id, None), None
        if time.monotonic() >= self.deadline:
            return self._finish("failed", stage.id, OVER_BUDGET), None
        status, reason, verdict = self._run_stage(stage, round_, loop, feedback)
        if verdict is None and status != "succeeded":
            # A plain cancel is not an error; a cancel whose after-stage steps failed is.
            return self._finish(status, stage.id, None if reason == CANCELLED else reason), None
        return None, verdict

    def _finish(self, status: str, failed_stage: str | None, error: str | None) -> int:
        pr_url = self._find_pr()
        if self.ready:
            self._send_sync_back()
        error = None if error is None else _clip(error)
        result = {"status": status, "failed_stage": failed_stage, "error": error, "pr_url": pr_url}
        self.events.emit("result", None, result)
        self.events.close()
        say(f"run {self.m.run_id} {status}" + (f": {error}" if error else ""))
        if status in ("completed", "cancelled") and self.owns_workdir:
            try:
                remove_tree(self.workdir)
            except OSError as exc:
                say(f"could not delete the workdir {self.workdir}: {exc}")
        return 0

    def _send_sync_back(self) -> None:
        """Upload the sync_back folder's regular files as one zip, unless it is empty or over
        `sync_back.max_bytes`, then record `sync_back`. Files stream into the zip in chunks, and
        zipping stops as soon as the zip passes the cap. Only RunGone (401/410) escapes; any
        other failure is a warning."""
        folder, cap = self.m.sync_back["dir"], self.m.sync_back["max_bytes"]
        files, skipped = _walk_files(self.workdir, folder)
        if not files and not skipped:
            return
        if self.upload is None:
            self.events.warn(None, f"{folder} is not returned in mailbox mode")
            return
        if skipped:
            self.events.warn(
                None,
                _clip(
                    f"{folder}: skipped (not a regular file, or a bad name): {', '.join(skipped)}"
                ),
            )
        if not files:
            return

        buf = io.BytesIO()
        try:
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
                for path in files:
                    src = self.workdir / path
                    info = zipfile.ZipInfo.from_file(
                        src, path[len(folder) + 1 :], strict_timestamps=False
                    )
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with src.open("rb") as fh, zf.open(info, "w") as dst:
                        while (chunk := fh.read(1 << 20)) and buf.tell() <= cap:
                            dst.write(chunk)
                    if buf.tell() > cap:
                        break
        except (OSError, ValueError) as exc:  # ValueError: e.g. a name zipfile can't encode
            self.events.warn(None, _clip(f"{folder} not sent: {exc!r}"))
            return
        if buf.tell() > cap:
            self.events.warn(None, f"{folder} not sent: the zip is over {cap} bytes")
            return
        data = buf.getvalue()

        digest = hashlib.sha256(data).hexdigest()
        path = f"/api/worker/jobs/{self.m.job['id']}/sync-back?sha256={digest}&files={len(files)}"
        try:
            self.upload.put(path, data)
        except (HttpError, Unreachable) as exc:
            if isinstance(exc, HttpError) and exc.status in (401, 410):
                raise RunGone(str(exc)) from exc
            self.events.warn(None, f"{folder} not sent: {exc}")
            return
        sent = {"sha256": digest, "bytes": len(data), "files": len(files)}
        self.events.emit("sync_back", None, sent)

    # ------------------------------------------------------------ workspace (SPEC §9.2)

    def _prepare_workdir(self) -> str | None:
        wd = self.workdir
        try:
            try:
                st = wd.lstat()
            except FileNotFoundError:
                wd.mkdir(parents=True)
            else:
                if is_link(st) or not stat.S_ISDIR(st.st_mode):
                    return f"workdir {wd} exists and is not a directory"
                if any(wd.iterdir()):
                    return f"workdir {wd} is not empty"
        except OSError as exc:
            return f"cannot create workdir {wd}: {exc}"
        self.owns_workdir = True
        return None

    def _setup_workspace(self) -> str | None:
        if not self.repo:
            r = self._git("init", "-q", show=True)
            return f"git init failed: {r.tail()}" if r.code else None
        ws = self.m.workspace
        repo_url, base_branch, branch = ws["repo_url"], ws["base_branch"], ws["branch"]
        if not repo_url or repo_url.startswith("-"):
            return "workspace.repo_url: required in repo mode, must not start with -"
        for name, value in (("base_branch", base_branch), ("branch", branch)):
            problem = "required in repo mode" if value is None else branch_error(value)
            if problem:
                return f"workspace.{name}: {problem}"
        if self.api_mode:
            problem = self._setup_github(repo_url)
            if problem:
                return problem
        say(f"cloning into {self.workdir}")
        r = self._git("clone", "--filter=blob:none", "--no-tags", "--", repo_url, ".", show=True)
        if r.code:
            return f"git clone failed: {r.tail()}"
        exists = self._git("rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}").code == 0
        self.branch_exists = exists
        start = f"origin/{branch}" if exists else f"origin/{base_branch}"
        r = self._git("checkout", "-q", "-B", branch, start, show=True)
        if r.code:
            return f"git checkout failed: cannot start {branch} from {start}: {r.tail()}"
        r = self._git("rev-parse", "HEAD")
        if r.code:
            return f"git rev-parse HEAD failed: {r.tail()}"
        self.start_head = self.pushed_head = r.out.decode().strip()
        # The accidental-push guardrail (PRE_PUSH); local, so a global hooksPath doesn't replace it.
        hooks = self.workdir / ".git" / "agentos-hooks"
        try:
            hooks.mkdir()
            (hooks / "pre-push").write_bytes(PRE_PUSH.format(self.push_marker).encode())
            (hooks / "pre-push").chmod(0o755)
        except OSError as exc:
            return f"cannot install the pre-push hook: {exc}"
        name = os.environ.get("AGENTOS_GIT_NAME") or "AgentOS"
        email = os.environ.get("AGENTOS_GIT_EMAIL") or "agentos@localhost"
        path = hooks.as_posix()
        for key, value in (("user.name", name), ("user.email", email), ("core.hooksPath", path)):
            r = self._git("config", key, value)
            if r.code:
                return f"git config {key} failed: {r.tail()}"
        return None

    def _setup_github(self, repo_url: str) -> str | None:
        """API mode: check the env, then that the token can write to the ticket's repo by
        posting an empty blob there (harmless: blobs are content-addressed)."""
        api_url = os.environ.get("GITHUB_API_URL", "").strip()
        server_url = os.environ.get("GITHUB_SERVER_URL", "").strip()
        for name, value in (("GITHUB_API_URL", api_url), ("GITHUB_SERVER_URL", server_url)):
            if not value:
                return f"GitHub API publication needs {name}"
        if urllib.parse.urlsplit(api_url).scheme != "https":
            return "GITHUB_API_URL must be https"
        repo, why = github_repo(repo_url, server_url)
        if repo is None:
            return f"cannot publish through the GitHub API: {why}"

        github = GitHub(api_url, os.environ.get("GH_TOKEN", "").strip(), repo)
        try:
            github.call("POST", "/git/blobs", {"content": "", "encoding": "utf-8"})
        except GitHubError as exc:
            if exc.status in (401, 403, 404) and exc.action != "retry":
                runs_in = os.environ.get("GITHUB_REPOSITORY") or "unknown"
                return (
                    f"cannot write to {repo} through the GitHub API ({exc}): the job's token "
                    f"only writes to the repo the workflow runs in ({runs_in})"
                )
            return f"cannot write to {repo} through the GitHub API: {exc}"

        say(f"publishing through the GitHub API to {repo}")
        self.github = github
        return None

    def _write_files(self) -> None:
        for f in self.m.files:
            problem = write_entry(self.workdir, f)
            if problem:
                self.sync.unsynced.add(f.path)
                self.events.warn(None, f"skipped {f.path}: {problem}")

    def _protect(self) -> None:
        """Keep context files, notes, `workspace.protect` and `.agentos/` out of `git add`."""
        r = self._git("ls-files", "-z")
        if r.code:
            self.events.warn(None, f"git ls-files failed: {r.tail()}")
            return
        tracked = r.names()
        by_lower = {t.lower(): t for t in tracked}
        tracked_set = set(tracked)
        skip: list[str] = []
        exclude: list[str] = []
        for path in dict.fromkeys(f.path for f in self.m.files):
            hit = path if path in tracked_set else by_lower.get(path.lower())
            if hit is None:
                exclude.append(path)
            else:
                skip.append(hit)
        if skip:
            r = self._git(
                "update-index",
                "--skip-worktree",
                "-z",
                "--stdin",
                stdin=b"\0".join(p.encode() for p in skip),
            )
            if r.code:
                self.events.warn(None, f"git update-index --skip-worktree failed: {r.tail()}")
        lines = "".join(f"/{p}\n" for p in [*exclude, *self.m.workspace["protect"]])
        junk = "".join(f"{p}\n" for p in JUNK_PATTERNS)
        try:
            info = self.workdir / ".git" / "info"
            info.mkdir(parents=True, exist_ok=True)
            with (info / "exclude").open("ab") as fh:
                fh.write(
                    f"\n# agentos context files\n{lines}/{AGENTOS_DIR}/\n"
                    f"# agentos build caches\n{junk}".encode()
                )
        except OSError as exc:
            self.events.warn(None, f"cannot write .git/info/exclude: {exc}")

    # ------------------------------------------------------------ stages (SPEC §9.3)

    def _run_stage(
        self, stage: Stage, round_: int, loop: Loop | None, feedback: str
    ) -> tuple[str, str | None, Verdict | None]:
        """One stage execution: its call(s), then the after-stage steps. (status, reason, the
        check's verdict); the verdict is dropped when the after-stage steps fail the stage."""
        self.current_stage = stage.id
        label = f", round {round_} of {loop.max_rounds}" if loop else ""
        say(f"stage {stage.id} ({stage.kind}) started{label}")
        self.events.emit("stage_started", stage.id, {"round": round_})
        verdict: Verdict | None = None
        if loop is not None and stage.check:
            status, exit_code, reason, verdict = self._exec_check(
                stage, round_, loop.max_rounds, feedback
            )
        else:
            status, exit_code, reason = self._exec(stage, *self._call(stage, round_, feedback))
        problem = self._after_stage(stage.id)
        if problem:
            verdict = None
            if status == "succeeded":
                status, reason = "failed", problem
            else:
                reason = f"{reason}; {problem}" if reason else problem
        reason = None if reason is None else _clip(reason)
        self.events.emit(
            "stage_finished",
            stage.id,
            {"status": status, "exit_code": exit_code, "reason": reason, "round": round_},
        )
        say(f"stage {stage.id} {status}" + (f": {reason}" if reason else ""))
        return status, reason, verdict

    def _call(self, stage: Stage, round_: int, feedback: str) -> tuple[list[str], str | None]:
        """(argv, stdin) of `stage` in `round_`: round 1 as given, later rounds with feedback."""
        if stage.kind == "shell":
            return [*shell_prefix(), stage.command or ""], None
        if round_ == 1:
            return list(stage.argv or ()), stage.stdin
        return stage.rounds[round_ - 2].build(feedback)

    def _resolve(self, kind: str, argv: list[str]) -> tuple[list[str], str | None]:
        program = shutil.which(argv[0], path=self.stage_env.get("PATH"))
        if program is None:
            what = "program" if kind == "agent" else "shell (set AGENTOS_SHELL)"
            return [], f"{what} not found: {argv[0]}"
        if os.name == "nt":
            problem = cmd_guard_error(program, argv)
            if problem:
                return [], problem
        return [program, *argv[1:]], None

    def _interrupt(self) -> str | None:
        if self.events.failure is not None:
            return "abort"
        if self.events.cancel.is_set():
            return "cancel"
        return "budget" if time.monotonic() >= self.deadline else None

    def _exec(
        self,
        stage: Stage,
        argv: list[str],
        stdin: str | None,
        collect: OnLine | None = None,
    ) -> tuple[str, int | None, str | None]:
        """One call of `stage`; its output goes out as log events (and to `collect`), and an
        agent's also to stderr (Echo). The exit code is None when the call didn't run to its end
        (not started, timeout, cancel)."""
        argv, problem = self._resolve(stage.kind, argv)
        if problem:
            return "failed", None, problem
        stage_id = stage.id
        echo = Echo(stage_id) if self.echo and stage.kind == "agent" else None

        def on_line(stream: str, text: str, truncated: bool) -> None:
            self.events.log(stage_id, stream, text, truncated)
            if collect is not None:
                collect(stream, text, truncated)
            if echo is not None:
                echo(stream, text, truncated)

        short = min(SHELL_LINE_BYTES, self.max_line_bytes)
        result = run_process(
            argv,
            cwd=self.workdir,
            env=self.stage_env,
            stdin=stdin,
            timeout_s=stage.timeout_s,
            caps=(self.max_line_bytes if stage.kind == "agent" else short, short),
            on_line=on_line,
            interrupt=self._interrupt,
        )
        if result.error:
            return "failed", None, result.error
        if result.stopped == "abort":
            self.events.check()
        if result.stopped == "cancel":
            return "cancelled", None, CANCELLED
        if result.stopped == "budget":
            return "failed", None, OVER_BUDGET
        if result.stopped == "timeout":
            return "failed", None, f"timed out after {stage.timeout_s}s"
        if result.exit_code == 0:
            return "succeeded", 0, None
        return "failed", result.exit_code, f"exited with code {result.exit_code}"

    # ------------------------------------------------------------ loop block checks

    def _exec_check(
        self, stage: Stage, round_: int, max_rounds: int, feedback: str
    ) -> tuple[str, int | None, str | None, Verdict | None]:
        """A check's call(s) and verdict: (status, exit code, reason, verdict). No verdict means
        the stage failed or was cancelled: an invalid verdict is never a pass, nor a fail."""
        argv, stdin = self._call(stage, round_, feedback)
        if stage.kind == "shell":
            lines: deque[str] = deque(maxlen=SHELL_CHECK_LINES)
            status, code, reason = self._exec(stage, argv, stdin, lambda _s, t, _c: lines.append(t))
            if code is None:
                return status, None, reason, None
            verdict = shell_verdict(code, list(lines))
            return self._decided(stage, round_, max_rounds, verdict, "exit_code", 0, code)
        attempt, error = 0, ""
        while True:
            status, code, reason, found = self._verdict_call(stage, argv, stdin)
            if code is None:  # not started, timed out or cancelled: never repaired
                return status, None, reason, None
            if isinstance(found, Verdict):
                source = stage.verdict_from or "file"
                return self._decided(stage, round_, max_rounds, found, source, attempt, code)
            error = _clip(found)
            if not stage.repairs or attempt >= stage.verdict_repairs:
                break
            attempt += 1
            say(f"stage {stage.id}: invalid verdict ({error}), repair {attempt}")
            data = {"round": round_, "attempt": attempt, "error": error}
            self.events.emit("verdict_repair", stage.id, data)
            self.events.check()
            if self.events.cancel.is_set():
                return "cancelled", code, CANCELLED, None
            if time.monotonic() >= self.deadline:
                return "failed", code, OVER_BUDGET, None
            argv, stdin = stage.repairs[round_ - 1].build(error)
        if stage.repairs and stage.verdict_repairs:
            after = f" after {attempt} repair{'s' if attempt != 1 else ''}"
        else:
            after = ""
        return "failed", code, f"{stage.name} gave no valid verdict{after}: {error}", None

    def _verdict_call(
        self, stage: Stage, argv: list[str], stdin: str | None
    ) -> tuple[str, int | None, str | None, Verdict | str]:
        """One call of an agent check, and its valid verdict or why there is none. The status and
        reason are the call's, for when it didn't run to its end (exit code None)."""
        from_file = stage.verdict_from != "structured_output"
        if from_file:
            self._clear_verdict(stage.id)
        results: list[dict[str, Any]] = []  # the last result line only

        def collect(stream: str, text: str, truncated: bool) -> None:
            if stream == "stdout" and not truncated and text.startswith("{"):
                with contextlib.suppress(ValueError, RecursionError):
                    line = json.loads(text)
                    if isinstance(line, dict) and line.get("type") == "result":
                        results[:] = [line]

        status, code, reason = self._exec(stage, argv, stdin, None if from_file else collect)
        if code is None:
            return status, None, reason, ""
        if code != 0:
            return status, code, reason, f"exited with code {code}"
        if from_file:
            value, error = file_verdict(self.workdir)
        else:
            value, error = result_verdict(results[-1] if results else None)
        error = error or verdict_error(value)
        return status, code, reason, error or Verdict(value["verdict"], value["notes"])

    def _clear_verdict(self, stage_id: str) -> None:
        """Delete the verdict file (and make `.agentos/`), so a stale verdict is never read."""
        target, err = resolve_inside(self.workdir, VERDICT_FILE)
        if err is None:
            try:
                target.parent.mkdir(exist_ok=True)
                target.unlink(missing_ok=True)
            except OSError as exc:
                err = exc.strerror or str(exc)
        if err:
            self.events.warn(stage_id, f"cannot clear {VERDICT_FILE}: {err}")

    def _decided(
        self,
        stage: Stage,
        round_: int,
        max_rounds: int,
        verdict: Verdict,
        source: str,
        repairs: int,
        code: int,
    ) -> tuple[str, int | None, str | None, Verdict | None]:
        """Emit a check's valid verdict. A "fail" fails the stage only in the last round (the
        board blames that check); either way the round's other checks still run."""
        self.events.emit(
            "verdict",
            stage.id,
            {
                "round": round_,
                "verdict": verdict.verdict,
                "notes": verdict.notes,
                "source": source,
                "repairs": repairs,
            },
        )
        if verdict.verdict == "pass":
            return "succeeded", code, None, verdict
        if round_ < max_rounds:
            return "succeeded", code, f"verdict: fail (round {round_} of {max_rounds})", verdict
        return (
            "failed",
            code,
            f"verdict: fail in the last round ({round_} of {max_rounds})",
            verdict,
        )

    # ------------------------------------------------------------ after each stage (SPEC §9.4)

    def _after_stage(self, stage_id: str) -> str | None:
        self.events.check()
        self._sync(stage_id)
        if not self.repo:
            return None
        return self._commit(stage_id) or self._guard() or self._push(stage_id)

    def _sync(self, stage_id: str) -> None:
        batch = self.sync.collect(self.workdir)
        for warning in batch.warnings:
            self.events.warn(stage_id, warning)
        self.events.emit("sync", stage_id, {"files": batch.files})

    def _is_protected(self, path: str) -> bool:
        low = path.lower()
        return any(low == p or low.startswith(p + "/") for p in self._protected_lower)

    def _commit(self, stage_id: str) -> str | None:
        r = self._git("add", "-A")
        if r.code:
            return f"git add failed: {r.tail()}"
        # Unstage every context path, whatever .gitignore negations (`!*.md`) let through.
        pathspecs = b"\0".join(p.encode() for p in self.protected)
        r = self._git(
            "reset", "-q", "--pathspec-from-file=-", "--pathspec-file-nul", stdin=pathspecs
        )
        if r.code:
            return f"git reset failed: {r.tail()}"
        r = self._git("diff", "--cached", "--name-only", "--no-renames", "-z")
        if r.code:
            return f"git diff --cached failed: {r.tail()}"
        staged = r.names()
        leaked = [p for p in staged if self._is_protected(p)]
        if leaked:
            return f"refusing to commit context files: {', '.join(leaked[:5])}"
        if not staged:
            return None
        message = f"{self.m.key} [{stage_id}] wip"
        r = self._git(
            "-c", "commit.gpgsign=false", "commit", "--no-verify", "-q", "-m", message, show=True
        )
        return f"git commit failed: {r.tail()}" if r.code else None

    def _guard(self) -> str | None:
        """Pre-push guard (SPEC §9.4.3): catches agents that committed context on their own."""
        span = f"{self.start_head}..HEAD"
        r = self._git("diff", "--name-only", "--no-renames", "-z", span)
        if r.code:
            return f"pre-push guard: git diff failed: {r.tail()}"
        leaked = [p for p in r.names() if self._is_protected(p)]
        if leaked:
            return f"pre-push guard: commits touch context files: {', '.join(leaked[:5])}"
        r = self._git("log", "-p", "--no-color", "--no-ext-diff", "--no-textconv", span)
        if r.code:
            return f"pre-push guard: git log failed: {r.tail()}"
        if any(f"agentos:begin {b}".encode() in r.out for b in self.m.workspace["blocks"]):
            return "pre-push guard: commits contain the agentos context block"
        return None

    def _push(self, stage_id: str | None) -> str | None:
        r = self._git("rev-parse", "HEAD")
        if r.code:
            return f"git rev-parse HEAD failed: {r.tail()}"
        head = r.out.decode().strip()
        if head == self.pushed_head:
            return None
        self._check_gone()
        if self.github is not None:
            return self._publish(stage_id, self.github)
        r = self._git("push", "-u", "origin", self.branch, show=True, stage_id=stage_id)
        if r.code:
            return PUSH_REJECTED if b"rejected" in r.out + r.err else f"push failed: {r.tail()}"
        count = self._git("rev-list", "--count", f"{self.pushed_head}..{head}")
        pushed = {"branch": self.branch, "head": head, "commits": int(count.out or 0)}
        self.events.emit("pushed", None, {**pushed, "method": "git"})
        self.pushed_head = head
        return None

    def _check_gone(self) -> None:
        """Before every publish: never once the run was taken away, nor after `not_after`."""
        if isinstance(self.events.failure, RunGone):  # taken away while we were committing
            raise self.events.failure
        if datetime.now(UTC) >= self.not_after:
            raise RunGone(f"the job's not_after ({self.m.job['not_after']}) has passed")

    def _publish(self, stage_id: str | None, github: GitHub) -> str | None:
        """API mode's push: publish through the Git Data API, then make the local branch the
        published one. A GitHub failure is the stage's failure reason."""
        branch = self.branch
        try:
            sha, count = publish_branch(
                self._git, github, branch, self.pushed_head, self.branch_exists, self._check_gone
            )
        except PublishError as exc:
            return str(exc)
        self.pushed_head = sha
        self.branch_exists = True
        pushed = {"branch": branch, "head": sha, "commits": count, "method": "api"}
        self.events.emit("pushed", None, pushed)

        message = (
            f"published {count} commit(s) to {branch} as one through the GitHub API ({sha[:12]})"
        )
        say(message)
        self.events.log(stage_id, "system", message)
        return track_published(self._git, branch, sha)

    def _save_offline(self) -> None:
        stage_id = self.current_stage or "setup"
        try:
            problem = self._commit(stage_id) or self._guard() or self._push(None)
        except RunGone as exc:  # past not_after
            problem = str(exc)
        say(f"could not save the work: {problem}" if problem else "work committed and pushed")

    # ------------------------------------------------------------ PR lookup (end of a repo run)

    def _find_pr(self) -> str | None:
        """The PR URL for the branch, or None. Repo mode with a workspace only, never once the
        server is gone, and never fatal: a miss is one `system` log line saying why."""
        if not (self.repo and self.ready) or self.events.failure is not None:
            return None
        if os.environ.get("AGENTOS_PR_LOOKUP", "").strip() == "0":
            return None
        url, why = self._lookup_pr()
        message = f"PR: {url}" if url else f"no PR URL: {why}"
        say(message)
        self.events.log(None, "system", message)
        return url

    def _lookup_pr(self) -> tuple[str | None, str]:
        branch = self.branch
        argv = [arg.replace("{branch}", branch) for arg in PR_LOOKUP]
        program = shutil.which(argv[0], path=self.stage_env.get("PATH"))
        if program is None:
            return None, f"{argv[0]} not found"
        if os.name == "nt" and cmd_guard_error(program, argv):
            return None, f"{argv[0]} is a .cmd/.bat shim and the arguments are unsafe for it"
        out: list[str] = []
        err: list[str] = []

        def on_line(stream: str, text: str, _truncated: bool) -> None:
            if text.strip():
                (out if stream == "stdout" else err).append(text.strip())

        cap = min(SHELL_LINE_BYTES, self.max_line_bytes)
        result = run_process(
            [program, *argv[1:]],
            cwd=self.workdir,
            env=self.stage_env,
            stdin=None,
            timeout_s=PR_LOOKUP_TIMEOUT_S,
            caps=(cap, cap),
            on_line=on_line,
            interrupt=lambda: "abort" if self.events.failure is not None else None,
        )
        return pr_lookup_result(argv[0], result, out, err)

    # ------------------------------------------------------------ git

    def _git(
        self,
        *args: str,
        stdin: bytes | None = None,
        show: bool = False,
        stage_id: str | None = None,
    ) -> GitResult:
        """Run git in the workdir. `show` (and any failure) streams its output as system logs."""
        cmd = ["git", "-c", "core.quotePath=false", *args]
        try:
            if sys.platform == "win32":
                p = subprocess.run(
                    cmd,
                    cwd=self.workdir,
                    env=self.git_env,
                    input=stdin,
                    stdin=None if stdin is not None else subprocess.DEVNULL,
                    capture_output=True,
                    timeout=GIT_TIMEOUT_S,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                p = subprocess.run(
                    cmd,
                    cwd=self.workdir,
                    env=self.git_env,
                    input=stdin,
                    stdin=None if stdin is not None else subprocess.DEVNULL,
                    capture_output=True,
                    timeout=GIT_TIMEOUT_S,
                    check=False,
                )
            result = GitResult(p.returncode, p.stdout, p.stderr)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = GitResult(-1, b"", str(exc).encode())
        if show or result.code:
            cap = min(SHELL_LINE_BYTES, self.max_line_bytes)
            for raw in (result.out + b"\n" + result.err).splitlines():
                if raw.strip() and b"\0" not in raw:
                    text = raw[:cap].decode("utf-8", "replace")
                    self.events.log(stage_id, "system", text, len(raw) > cap)
        return result


# ---------------------------------------------------------------- client mode
#
# `--client` runs on a user's own machine: it long-polls the server for the runs its user
# dispatched to "My client" and starts this same file as a detached worker for each, so a Ctrl+C
# in the client's console never kills a run.

_ENV_PREFIX = "AGENTOS_"
_ENV_BLANK_RE = re.compile(r"[ \t]*(?:#[^\r\n]*)?(?:\r?\n|\Z)")
_ENV_ENTRY_RE = re.compile(
    r"[ \t]*(?:export[ \t]+)?(?P<key>(?!['\"])[^=#\s]+)[ \t]*"
    r"(?:=[ \t]*(?:'(?P<single>(?:\\'|[^'])*)'"
    r'|"(?P<double>(?:\\"|[^"])*)"'
    r"|(?P<bare>(?!['\"])[^\r\n]*)))?"
    r"[ \t]*(?:#[^\r\n]*)?(?:\r?\n|\Z)"
)
_SINGLE_ESCAPE_RE = re.compile(r"\\([\\'])")
_DOUBLE_ESCAPE_RE = re.compile(r"\\([\\'\"abfnrtv])")
_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
# <run_id> workspaces, <run_id>.log logs and <job id>.lock run worker locks
_RUN_ENTRY_RE = re.compile(r"[0-9]+(?:\.log)?|[0-9]+-[0-9]+-[0-9a-f]+\.lock")
_JOB_ID_RE = re.compile(r"[0-9]+-[0-9]+-[0-9a-f]+")  # a job id the client puts in a file name


class EnvFileError(ValueError):
    """An env file line outside the supported subset."""


def parse_env(text: str) -> dict[str, str | None]:
    """The python-dotenv subset a client's `--env-file` uses: `KEY=value` lines, `#`
    comments (whole-line, or after whitespace in an unquoted value), an optional `export `,
    single quotes (literal but `\\'` and `\\\\`), double quotes (with `\\n`-style escapes), quoted
    values across lines, and a bare `KEY` meaning "unset KEY" (None). `${VAR}` is not expanded."""
    values: dict[str, str | None] = {}
    pos, line = 0, 1
    while pos < len(text):
        blank = _ENV_BLANK_RE.match(text, pos)
        if blank is not None:
            pos, line = blank.end(), line + blank.group(0).count("\n")
            continue

        entry = _ENV_ENTRY_RE.match(text, pos)
        if entry is None:
            raise EnvFileError(f"line {line}: expected KEY=value, KEY or a comment")
        single, double, bare = entry.group("single", "double", "bare")
        if single is not None:
            value: str | None = _SINGLE_ESCAPE_RE.sub(lambda m: m.group(1), single)
        elif double is not None:
            value = _DOUBLE_ESCAPE_RE.sub(lambda m: _ESCAPES.get(m.group(1), m.group(1)), double)
        elif bare is not None:
            value = re.sub(r"\s+#.*", "", bare).rstrip()
        else:
            value = None
        values[entry.group("key")] = value
        pos, line = entry.end(), line + entry.group(0).count("\n")
    return values


def read_env_file(path: Path) -> dict[str, str | None]:
    """parse_env of a UTF-8 file (a leading BOM is ignored). Raises OSError or EnvFileError."""
    return parse_env(path.read_text(encoding="utf-8-sig"))


def _env_set(env: dict[str, str], key: str, value: str | None) -> None:
    """Set (or with None, remove) `key`; Windows env names are case-insensitive."""
    if sys.platform == "win32":
        for existing in [k for k in env if k.upper() == key.upper()]:
            del env[existing]
    else:
        env.pop(key, None)
    if value is not None:
        env[key] = value


def child_env(
    job_token: str, env_file: Mapping[str, str | None], base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """A run worker's environment: `base` (default: ours) minus every AGENTOS_* var, then the env
    file's values (None removes the key), then unbuffered UTF-8 output and AGENTOS_TOKEN. The
    token is never on the command line."""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if not k.upper().startswith(_ENV_PREFIX)}
    for key, value in env_file.items():
        _env_set(env, key, value)
    _env_set(env, "PYTHONUNBUFFERED", "1")  # live log
    _env_set(env, "PYTHONIOENCODING", "utf-8")
    _env_set(env, "AGENTOS_TOKEN", job_token)
    return env


class Child(Protocol):
    """A started run worker, as the client loop sees it."""

    pid: int

    def poll(self) -> int | None: ...

    def wait(self) -> int: ...


Spawn = Callable[[list[str], Path, dict[str, str], Path], Child]  # (argv, cwd, env, log file)


def _take_lock(lock: IO[bytes], wait: bool) -> bool:
    """Take an exclusive OS lock on an open file (`wait`: until it is free). False if not free."""
    lock.seek(0)
    try:
        if sys.platform == "win32":
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
    except OSError:
        if wait:
            raise
        return False
    return True


def hold_lock(path: Path) -> IO[bytes]:
    """Open `path` (created if missing) and take an exclusive OS lock on it, waiting while
    another holder has it (a client looking: `lock_held`). The lock lasts while the file stays
    open, and the OS releases it when the process ends, however it ends."""
    lock = path.open("a+b")
    try:
        _take_lock(lock, wait=True)
    except BaseException:
        lock.close()
        raise
    return lock


def release_lock(lock: IO[bytes]) -> None:
    """Release a lock `hold_lock` (or `_take_lock`) took, and close its file."""
    lock.seek(0)
    if sys.platform == "win32":
        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    lock.close()


def lock_held(path: Path) -> bool:
    """Whether a process holds `path`'s lock (`hold_lock`): try to take it without waiting."""
    try:
        lock = path.open("a+b")
    except OSError:
        return False  # gone, or not openable: nobody holds it
    with lock:
        if not _take_lock(lock, wait=False):
            return True
        release_lock(lock)
    return False


def held_jobs(runs: Path) -> set[str]:
    """The job ids whose run worker holds its `<job id>.lock` in `runs`: alive, whichever client
    started it (a restarted client sees the workers that outlived it; a reused pid can't fool a
    lock the OS released)."""
    try:
        locks = [p for p in runs.glob("*.lock") if _JOB_ID_RE.fullmatch(p.stem)]
    except OSError:
        return set()
    return {p.stem for p in locks if lock_held(p)}


class Children:
    """The client's started workers that still run, by job id, plus the jobs `held` reports alive
    (workers an earlier client started). A thread per started worker waits on its process: one
    that exits while the client loop waits (`waiting`: a lease in flight, or a busy client's
    pause) is reported at once by `report` (the job ids still running), so the server hears it
    ended without waiting for the next lease.

    Every list comes with a `seq` taken under the same lock as it is built, so a later-built list
    has a higher seq: the report and the loop's lease travel apart and may arrive out of order,
    and the server judges by seq, not by arrival."""

    def __init__(
        self, report: Callable[[int, list[str]], object], held: Callable[[], set[str]]
    ) -> None:
        self._report = report
        self._held = held
        self._lock = threading.Lock()
        self._running: dict[str, Child] = {}
        self._waiting = False
        self._seq = 0

    def add(self, job_id: str, child: Child) -> None:
        with self._lock:
            self._running[job_id] = child
        threading.Thread(target=self._watch, args=(job_id, child), daemon=True).start()

    @contextlib.contextmanager
    def waiting(self) -> Iterator[tuple[int, list[str]]]:
        """Drop the exited workers and yield a seq and the running ones' job ids, then the held
        ones'; until the block ends, each exit is reported as it happens."""
        held = self._held()
        with self._lock:
            self._running = {job: c for job, c in self._running.items() if c.poll() is None}
            self._waiting = True
            self._seq += 1
            listing = (self._seq, [*self._running, *sorted(held - self._running.keys())])
        try:
            yield listing
        finally:
            with self._lock:
                self._waiting = False

    def _watch(self, job_id: str, child: Child) -> None:
        code = child.wait()
        say(f"job {job_id}: its worker (pid {child.pid}) exited with code {code}")
        held = self._held() - {job_id}  # its process ended, whatever its lock says yet
        with self._lock:
            if not self._waiting or self._running.pop(job_id, None) is None:
                return  # the loop's next lease leaves it out
            self._seq += 1
            seq, alive = self._seq, [*self._running, *sorted(held - self._running.keys())]
        try:
            self._report(seq, alive)
        except (HttpError, Unreachable, ValueError) as exc:
            say(f"could not report the end of job {job_id} ({exc}); the next lease will")


def spawn_detached(
    argv: list[str], cwd: Path, env: dict[str, str], log: Path
) -> subprocess.Popen[bytes]:
    """Start `argv` in its own process group (Windows: also without a console) with stdout and
    stderr appended to `log`, so it outlives this process and a Ctrl+C in its console."""
    with log.open("ab") as out:
        # The child gets its own copy of the log handle; ours closes when this block exits.
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            return subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
        return subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def git_repo_containing(path: Path) -> Path | None:
    """The nearest existing ancestor of `path` (itself included) that holds a `.git` entry."""
    for candidate in (path, *path.parents):
        if candidate.exists() and (candidate / ".git").exists():
            return candidate
    return None


def clean_workdir_root(root: Path, now: float) -> list[Path]:
    """Delete the run workspaces (`<run_id>`), logs (`<run_id>.log`) and free worker locks
    (`<job id>.lock`) directly under the client's own `root/CLIENT_RUNS_DIR`, last modified more
    than WORKSPACE_MAX_AGE_S before `now` (a POSIX timestamp). Nothing outside that folder is
    looked at, and anything else in it is left alone. Returns what was deleted."""
    runs = root / CLIENT_RUNS_DIR
    try:
        children = sorted(runs.iterdir())
    except OSError as exc:
        say(f"cannot list {runs} for old workspaces: {exc}")
        return []

    cutoff = now - WORKSPACE_MAX_AGE_S
    removed: list[Path] = []
    for child in children:
        if not _RUN_ENTRY_RE.fullmatch(child.name):
            continue
        if child.suffix == ".lock" and lock_held(child):
            continue  # its worker still runs
        try:
            st = child.lstat()
            if st.st_mtime >= cutoff:
                continue
            if stat.S_ISDIR(st.st_mode) and not is_link(st):
                remove_tree(child)
            else:
                child.unlink()
        except OSError as exc:
            say(f"cannot delete the old workspace {child}: {exc}")
            continue
        removed.append(child)
    return removed


def run_client(
    server: str,
    token: str,
    root: Path,
    max_runs: int,
    env_file: Path | None,
    spawn: Spawn,
) -> int:
    """Client mode: lease this user's jobs and start a detached worker for each, at most
    `max_runs` at once, listing the running ones as `alive` on every lease: those it started, and
    those whose `<job id>.lock` is held (`held_jobs`: every worker holds its lock for its whole
    life, so workers that outlived an earlier client are listed too and count against
    `max_runs`). A worker that exits while a lease waits is reported at once by a no-take lease
    (`Children`). With every slot busy
    it sends a no-take lease every CLIENT_HEARTBEAT_S, so the server still sees it online. Network
    errors, 5xx, CLIENT_TRANSIENT_4XX and malformed answers back off (up to CLIENT_BACKOFF_CAP_S)
    forever; any other 4xx or another protocol exits 1. Started workers outlive the client.

    Everything it writes goes under `<root>/runs` (CLIENT_RUNS_DIR): each worker runs there with
    `--workdir <root>/runs/<run_id>` and `--lock-file <root>/runs/<job id>.lock`, logs to
    `<run_id>.log` and gets its job token in child_env."""
    root = root.expanduser().resolve()
    repo = git_repo_containing(root)
    if repo is not None:
        say(f"--workdir-root {root} is inside the git repo {repo}; choose a directory outside it")
        return 2
    try:
        env_values = read_env_file(env_file) if env_file is not None else {}
    except (OSError, EnvFileError) as exc:
        say(f"cannot read the env file {env_file}: {exc}")
        return 2
    runs = root / CLIENT_RUNS_DIR
    try:
        runs.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        say(f"cannot create {runs}: {exc}")
        return 1

    removed = clean_workdir_root(root, time.time())
    if removed:
        say(f"deleted {len(removed)} run workspace(s) and log(s) older than 7 days")

    client = Client(server, token)
    me = Path(__file__).resolve()
    sha256: str | None = hashlib.sha256(me.read_bytes()).hexdigest()  # None once mentioned
    # This process's id on every lease: only the instance that last listed a job can end it by
    # leaving it out, so another process with this token (or on another root) never does.
    instance = secrets.token_urlsafe(32)
    children = Children(
        lambda seq, alive: client.post(
            "/api/worker/lease",
            {"instance": instance, "seq": seq, "wait_s": 0, "take": False, "alive": alive},
            retry_budget_s=0,
        ),
        lambda: held_jobs(runs),
    )
    delay = BACKOFF_START_S
    answered = time.monotonic()  # when the server last answered a lease
    say(f"client: leasing jobs from {server}, at most {max_runs} at once, in {runs}")
    try:
        while True:
            problem: str | None = None
            with children.waiting() as (seq, alive):
                busy = len(alive) >= max_runs
                if busy and time.monotonic() - answered < CLIENT_HEARTBEAT_S:
                    time.sleep(CLIENT_REAP_S)
                    continue

                wait_s = 0 if busy else CLIENT_POLL_WAIT_S
                body = {
                    "instance": instance,
                    "seq": seq,
                    "wait_s": wait_s,
                    "take": not busy,
                    "alive": alive,
                }
                try:
                    resp = client.post(
                        "/api/worker/lease", body, timeout_s=CLIENT_POLL_TIMEOUT_S, retry_budget_s=0
                    )
                    answered = time.monotonic()
                    if resp["protocol"] != PROTOCOL:
                        say(f"the server speaks another protocol than {PROTOCOL}; update worker.py")
                        return 1
                    job = resp["job"]
                    leased = job and (str(job["job_id"]), int(job["run_id"]), str(job["job_token"]))
                    if leased and not _JOB_ID_RE.fullmatch(leased[0]):
                        raise ValueError(f"a job id that isn't one: {leased[0]!r}")
                except HttpError as exc:
                    if exc.status not in CLIENT_TRANSIENT_4XX:
                        say(f"the server refused this client ({exc}); stopping")
                        return 1
                    problem = str(exc)
                except (Unreachable, ValueError) as exc:
                    problem = str(exc)
                except (KeyError, TypeError) as exc:  # an answer that isn't a lease answer
                    problem = f"a malformed answer ({exc!r})"

            if problem is None:
                delay = BACKOFF_START_S
                if sha256 is not None and resp.get("worker_sha256") != sha256:
                    say(f"this worker.py differs from the server's ({server}/api/worker/worker.py)")
                    sha256 = None
                if not leased:
                    continue
                job_id, run_id, job_token = leased
                workdir = runs / str(run_id)
                argv = [sys.executable, str(me), "--server", server, "--workdir", str(workdir)]
                argv += ["--lock-file", str(runs / f"{job_id}.lock")]
                log = runs / f"{run_id}.log"
                try:
                    child = spawn(argv, runs, child_env(job_token, env_values), log)
                except OSError as exc:
                    say(f"run {run_id}: cannot start its worker: {exc}")
                    continue
                children.add(job_id, child)
                say(f"run {run_id}: started its worker (pid {child.pid}), log {log}")
                continue

            say(f"lease failed ({problem}); leasing again in {delay:.1f}s")
            time.sleep(delay)
            delay = min(delay * 2, CLIENT_BACKOFF_CAP_S)
    except KeyboardInterrupt:
        say("client stopped; started workers keep running")
        return 130


# ---------------------------------------------------------------- entry point


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Exactly one mode: HTTP (--server [--token]), mailbox (--manifest --outbox --workdir) or
    client (--client --server, the client token in $AGENTOS_CLIENT_TOKEN)."""
    parser = argparse.ArgumentParser(prog="worker.py", description=__doc__)
    parser.add_argument("--server", help="AgentOS server URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("--token", help="job token (default: $AGENTOS_TOKEN)")
    parser.add_argument("--workdir", help="workspace directory (default: ./agentos-run-<run_id>)")
    parser.add_argument(
        "--lock-file", help="hold an OS lock on this file while running (a client's run worker)"
    )
    parser.add_argument("--manifest", help="mailbox mode: the run's manifest.json")
    parser.add_argument("--outbox", help="mailbox mode: the outbox.jsonl to create")
    parser.add_argument(
        "--client",
        action="store_true",
        help="client mode: run my runs on this machine (token: $AGENTOS_CLIENT_TOKEN)",
    )
    parser.add_argument("--max-runs", type=int, help="client mode: runs at once (default: 1)")
    parser.add_argument(
        "--workdir-root",
        help="client mode: where run workspaces and logs go (default: ~/.agentos/workspaces)",
    )
    parser.add_argument(
        "--env-file", help="client mode: KEY=value lines added to every run worker's environment"
    )
    args = parser.parse_args(argv)
    client_only = (args.max_runs, args.workdir_root, args.env_file)
    if args.client:
        used = [args.token, args.workdir, args.lock_file, args.manifest, args.outbox]
        if any(value is not None for value in used):
            parser.error(
                "--client can't be used with --token, --workdir, --lock-file, --manifest or "
                "--outbox"
            )
        if args.server is None:
            parser.error("--client needs --server")
        if urllib.parse.urlsplit(args.server).scheme not in ("http", "https"):
            parser.error("--server must be an http:// or https:// URL")
        if not os.environ.get("AGENTOS_CLIENT_TOKEN", "").strip():
            parser.error("client mode needs a client token in AGENTOS_CLIENT_TOKEN")
        args.max_runs = 1 if args.max_runs is None else args.max_runs
        if not 1 <= args.max_runs <= 100:  # a lease lists at most 100 `alive` jobs
            parser.error("--max-runs must be between 1 and 100")
        args.workdir_root = args.workdir_root or "~/.agentos/workspaces"
        args.mailbox = False
        return args
    if any(value is not None for value in client_only):
        parser.error("--max-runs, --workdir-root and --env-file need --client")
    args.mailbox = args.manifest is not None or args.outbox is not None
    if args.mailbox:
        if args.server is not None or args.token is not None:
            parser.error("--manifest/--outbox (mailbox mode) can't be used with --server/--token")
        if not (args.manifest and args.outbox and args.workdir):
            parser.error("mailbox mode needs --manifest, --outbox and --workdir")
        return args
    if args.server is None:
        parser.error("the following arguments are required: --server")
    args.token = args.token or os.environ.get("AGENTOS_TOKEN")
    if not args.token:
        parser.error("a job token is required (--token or AGENTOS_TOKEN)")
    if urllib.parse.urlsplit(args.server).scheme not in ("http", "https"):
        parser.error("--server must be an http:// or https:// URL")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    os.environ.pop("AGENTOS_TOKEN", None)  # never handed to git or to stages
    client_token = os.environ.pop("AGENTOS_CLIENT_TOKEN", "").strip()  # nor to run workers
    if args.client:
        env_file = Path(args.env_file) if args.env_file else None
        root = Path(args.workdir_root)
        return run_client(args.server, client_token, root, args.max_runs, env_file, spawn_detached)
    if args.mailbox:
        return run_mailbox(Path(args.manifest), Path(args.outbox), Path(args.workdir))
    if args.lock_file is None:
        return run_live(args)

    # A client's run worker: its client lists the job as alive while this lock is held.
    try:
        lock = hold_lock(Path(args.lock_file))
    except OSError as exc:
        say(f"cannot lock {args.lock_file}: {exc}")
        return 1
    try:
        return run_live(args)
    finally:
        release_lock(lock)


def run_live(args: argparse.Namespace) -> int:
    """Live mode: fetch the job, then bind it to a new instance id by posting `started` and
    waiting for its ack, all before the workdir is touched."""
    client = Client(args.server, args.token, secrets.token_urlsafe(32))
    reporter = HttpReporter(client)
    say(f"fetching the job from {args.server}")
    try:
        package = load_package(client.get("/api/worker/job"))
        sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        started = {"seq": package.journal["first_seq"], "ts": _now(), "job": package.job["id"]}
        data = {"protocol": PROTOCOL, "worker_sha256": sha256}
        reporter.send([{**started, "type": "started", "stage_id": None, "data": data}])
    except AlreadyClaimed as exc:
        say(f"nothing to do: {exc}")
        return 0
    except HttpError as exc:
        say(f"cannot fetch the job: {exc}")
        return 0 if exc.status == 409 else 1
    except (PackageError, RunGone, Unreachable) as exc:
        say(f"cannot start the job: {exc}")
        return 1

    say(f"bound to job {package.job['id']} ({package.key}, {len(package.stages)} stage(s))")
    events = EventSink(reporter, package.job["id"], package.journal["first_seq"] + 1)
    events.start()
    workdir = Path(args.workdir or f"agentos-run-{package.run_id}").resolve()
    return Run(events, package, workdir, client).execute()


def run_mailbox(manifest_path: Path, outbox: Path, workdir: Path) -> int:
    """Mailbox mode: the package is a file and the journal goes to the outbox. A package
    that can't be used exits 1 before the outbox exists (the server then fails the run)."""
    try:
        package = load_package(json.loads(manifest_path.read_bytes()))
    except (OSError, ValueError) as exc:  # ValueError: bad JSON, bad UTF-8 or a PackageError
        say(f"invalid package {manifest_path}: {exc}")
        return 1
    try:
        recorder = Recorder(outbox, package.run_id)
    except FileExistsError:
        say(f"the outbox {outbox} already exists: refusing to overwrite it")
        return 1
    except OSError as exc:
        say(f"cannot create the outbox {outbox}: {exc}")
        return 1
    say(f"mailbox run {package.run_id} ({package.key}, {len(package.stages)} stage(s))")
    events = EventSink(recorder, package.job["id"], package.journal["first_seq"] + 1)
    events.start()
    try:
        return Run(events, package, workdir.resolve(), None).execute()
    finally:
        recorder.close()


if __name__ == "__main__":
    sys.exit(main())
