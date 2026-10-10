"""AgentOS worker: claims a run, executes its stages, syncs context back, then exits.

Single file, Python >= 3.11 standard library only (enforced by a test). Served by the server at
GET /api/worker/worker.py so runners always get the matching version. See SPEC §9.

Flow: claim -> workspace (clone, or `git init` in no-repo mode) -> write the manifest files ->
for each stage: run it, sync context files back, then (repo mode) commit, pre-push guard, push ->
flush events -> complete.

Failure paths after the claim:
- cancel (`{"cancel": true}` on an events post): kill the stage tree, still sync/commit/push,
  complete `cancelled`.
- run gone (401/410 on any call): kill the stage tree, no commit, no push, exit 1.
- server unreachable (network errors / 5xx for RETRY_BUDGET_S): kill the stage tree, commit and
  push what exists, exit 1 (the reaper fails the run).
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import http.client
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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

PROTOCOL = 1

# ---------------------------------------------------------------- tunables (tests shrink these)
HTTP_TIMEOUT_S = 30.0  # per request
RETRY_BUDGET_S = 300.0  # network errors and 5xx are retried this long; then "unreachable"
BACKOFF_START_S = 0.5
BACKOFF_CAP_S = 10.0
FLUSH_INTERVAL_S = 1.0  # pending events are posted at least this often...
FLUSH_THRESHOLD = 200  # ...or as soon as this many are buffered
HEARTBEAT_S = 10.0  # an (empty) events post at least this often
POLL_S = 0.2  # how often a running stage checks cancel / timeout / server loss
READER_JOIN_S = 5.0  # after a stage exits, wait this long for its output readers, then abandon
GIT_TIMEOUT_S = 1800.0
KILL_TIMEOUT_S = 30.0

MAX_EVENTS_PER_POST = 500
MAX_EVENTS_PER_RUN = 100_000
EVENT_RESERVE = 1_000  # head-room kept for non-log events once log events are dropped
SHELL_LINE_BYTES = 8192  # line cap for every stream except agent-stage stdout
MAX_PATH_CHARS = 255
MAX_ERROR_CHARS = 2000
CANCELLED = "cancelled"  # stage_finished reason for a plain cancel

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

# Characters cmd.exe interprets; a .cmd/.bat program must not receive them in argv (SPEC §9.3).
CMD_UNSAFE = frozenset('\r\n"&|<>^%!')

_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


def say(message: str) -> None:
    """Worker progress on stderr (the launcher redirects it to a log file). Never pass tokens."""
    stamp = datetime.now(UTC).strftime("%H:%M:%S")
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
_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


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


def read_regular(root: Path, rel: str) -> tuple[bytes | None, str | None]:
    """(content, error) of a regular file. (None, None) when the file doesn't exist."""
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


# ---------------------------------------------------------------- manifest (protocol v1)


class ManifestError(ValueError):
    """The claimed manifest doesn't match protocol v1."""


@dataclass(frozen=True)
class ManifestFile:
    path: str
    content: str
    mode: str  # "replace" | "block"
    sync: bool


@dataclass(frozen=True)
class ManifestStage:
    id: str
    name: str
    kind: str  # "agent" | "shell"
    timeout_s: int
    argv: tuple[str, ...] | None
    stdin: str | None
    command: str | None


@dataclass(frozen=True)
class Manifest:
    run_id: int
    key: str
    slug: str
    title: str
    mode: str  # "repo" | "none"
    repo_url: str | None
    base_branch: str | None
    branch: str | None
    files: tuple[ManifestFile, ...]
    sync_dirs: tuple[str, ...]
    stages: tuple[ManifestStage, ...]
    max_file_bytes: int
    max_line_bytes: int


def _fields(
    value: object, where: str, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{where}: expected an object")
    missing = [k for k in required if k not in value]
    if missing:
        raise ManifestError(f"{where}: missing {', '.join(missing)}")
    extra = sorted(str(k) for k in value if k not in required and k not in optional)
    if extra:
        raise ManifestError(f"{where}: unexpected {', '.join(extra)}")
    return value


def _str(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{where}: expected a string")
    return value


def _opt_str(value: object, where: str) -> str | None:
    return None if value is None else _str(value, where)


def _int(value: object, where: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError(f"{where}: expected an integer")
    if value < minimum:
        raise ManifestError(f"{where}: must be >= {minimum}")
    return value


def _list(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestError(f"{where}: expected a list")
    return value


def _valid_path(value: object, where: str) -> str:
    path = _str(value, where)
    err = path_error(path)
    if err:
        raise ManifestError(f"{where}: invalid path {path!r}: {err}")
    return path


def parse_manifest(raw: object) -> Manifest:
    """Validate a protocol v1 manifest by hand (mirrors agentos.models.Manifest, extra=forbid)."""
    m = _fields(
        raw,
        "manifest",
        ("run_id", "ticket", "workspace", "files", "sync_dirs", "stages", "limits"),
        ("protocol",),
    )
    protocol = m.get("protocol", PROTOCOL)
    if isinstance(protocol, bool) or protocol != PROTOCOL:
        raise ManifestError(f"manifest: unsupported protocol {protocol!r} (worker speaks 1)")
    run_id = _int(m["run_id"], "run_id", 1)

    ticket = _fields(m["ticket"], "ticket", ("key", "slug", "title"))
    key = _str(ticket["key"], "ticket.key")
    if not _KEY_RE.fullmatch(key):
        raise ManifestError(f"ticket.key: invalid key {key!r}")

    ws = _fields(m["workspace"], "workspace", ("mode", "repo_url", "base_branch", "branch"))
    mode = _str(ws["mode"], "workspace.mode")
    if mode not in ("repo", "none"):
        raise ManifestError(f"workspace.mode: expected repo or none, got {mode!r}")
    repo_url = _opt_str(ws["repo_url"], "workspace.repo_url")
    base_branch = _opt_str(ws["base_branch"], "workspace.base_branch")
    branch = _opt_str(ws["branch"], "workspace.branch")
    if mode == "repo":
        if not repo_url or repo_url.startswith("-"):
            raise ManifestError("workspace.repo_url: required in repo mode, must not start with -")
        for name, value in (("base_branch", base_branch), ("branch", branch)):
            problem = "required in repo mode" if value is None else branch_error(value)
            if problem:
                raise ManifestError(f"workspace.{name}: {problem}")

    files: list[ManifestFile] = []
    for i, item in enumerate(_list(m["files"], "files")):
        where = f"files[{i}]"
        f = _fields(item, where, ("path", "content", "mode", "sync"))
        fmode = _str(f["mode"], f"{where}.mode")
        if fmode not in ("replace", "block"):
            raise ManifestError(f"{where}.mode: expected replace or block, got {fmode!r}")
        if not isinstance(f["sync"], bool):
            raise ManifestError(f"{where}.sync: expected a boolean")
        files.append(
            ManifestFile(
                path=_valid_path(f["path"], f"{where}.path"),
                content=_str(f["content"], f"{where}.content"),
                mode=fmode,
                sync=f["sync"],
            )
        )

    sync_dirs = tuple(
        _valid_path(d, f"sync_dirs[{i}]") for i, d in enumerate(_list(m["sync_dirs"], "sync_dirs"))
    )

    stages: list[ManifestStage] = []
    for i, item in enumerate(_list(m["stages"], "stages")):
        where = f"stages[{i}]"
        s = _fields(item, where, ("id", "name", "kind", "timeout_s"), ("argv", "stdin", "command"))
        kind = _str(s["kind"], f"{where}.kind")
        if kind not in ("agent", "shell"):
            raise ManifestError(f"{where}.kind: expected agent or shell, got {kind!r}")
        raw_argv = s.get("argv")
        argv = (
            None
            if raw_argv is None
            else tuple(_str(a, f"{where}.argv[{j}]") for j, a in enumerate(_list(raw_argv, where)))
        )
        command = _opt_str(s.get("command"), f"{where}.command")
        if kind == "agent" and not argv:
            raise ManifestError(f"{where}: an agent stage needs a non-empty argv")
        if kind == "shell" and not command:
            raise ManifestError(f"{where}: a shell stage needs a non-empty command")
        stage_id = _str(s["id"], f"{where}.id")
        if not stage_id:
            raise ManifestError(f"{where}.id: must not be empty")
        stages.append(
            ManifestStage(
                id=stage_id,
                name=_str(s["name"], f"{where}.name"),
                kind=kind,
                timeout_s=_int(s["timeout_s"], f"{where}.timeout_s", 1),
                argv=argv,
                stdin=_opt_str(s.get("stdin"), f"{where}.stdin"),
                command=command,
            )
        )

    limits = _fields(m["limits"], "limits", ("max_file_bytes", "max_line_bytes"))
    return Manifest(
        run_id=run_id,
        key=key,
        slug=_str(ticket["slug"], "ticket.slug"),
        title=_str(ticket["title"], "ticket.title"),
        mode=mode,
        repo_url=repo_url,
        base_branch=base_branch,
        branch=branch,
        files=tuple(files),
        sync_dirs=sync_dirs,
        stages=tuple(stages),
        max_file_bytes=_int(limits["max_file_bytes"], "limits.max_file_bytes", 1),
        max_line_bytes=_int(limits["max_line_bytes"], "limits.max_line_bytes", 1),
    )


# ---------------------------------------------------------------- HTTP


class HttpError(Exception):
    """A 4xx answer (not retried)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"HTTP {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class RunGone(Exception):
    """401/410 after the claim: the run was taken away. Never write to the branch again."""


class Unreachable(Exception):
    """Network errors or 5xx for RETRY_BUDGET_S."""


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
    """JSON POSTs with a bearer token. Retries network errors and 5xx with exponential backoff."""

    def __init__(self, server: str, token: str) -> None:
        self._base = server.rstrip("/")
        self._token = token
        host = urllib.parse.urlsplit(self._base).hostname or ""
        # A loopback server must never go through a configured proxy.
        handlers = [urllib.request.ProxyHandler({})] if host in _LOOPBACK else []
        self._opener = urllib.request.build_opener(*handlers)

    def post(self, path: str, body: dict[str, Any]) -> Any:
        data = json.dumps(body).encode("utf-8")
        deadline = time.monotonic() + RETRY_BUDGET_S
        delay = BACKOFF_START_S
        while True:
            request = urllib.request.Request(  # noqa: S310 -- the scheme is checked in parse_args
                self._base + path,
                data=data,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._token}",
                },
            )
            try:
                with self._opener.open(request, timeout=HTTP_TIMEOUT_S) as resp:
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
                raise Unreachable(f"POST {path}: {problem}")
            say(f"POST {path} failed ({problem}); retrying in {delay:.1f}s")
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, BACKOFF_CAP_S)


class WorkerApi:
    """Worker-token routes. 401/410 mean the run was taken away (RunGone)."""

    def __init__(self, client: Client) -> None:
        self._client = client

    def call(self, path: str, body: dict[str, Any]) -> Any:
        try:
            return self._client.post(path, body)
        except HttpError as exc:
            if exc.status in (401, 410):
                raise RunGone(f"POST {path}: HTTP {exc.status} {exc.code}") from exc
            raise


# ---------------------------------------------------------------- events


class EventSink:
    """Buffers run events and posts them from a background thread (SPEC §9.1).

    Batches go out every FLUSH_INTERVAL_S, as soon as FLUSH_THRESHOLD are buffered, and at least
    every HEARTBEAT_S even when empty. `cancel` is set when the server asks for it; `failure`
    holds RunGone/Unreachable once posting is impossible.
    """

    def __init__(self, api: WorkerApi) -> None:
        self._api = api
        self._cond = threading.Condition()
        self._buf: list[dict[str, Any]] = []
        self._seq = 0
        self._logs_dropped = False
        self._rejected_warned = False
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
            self._buf.append(
                {"seq": self._seq, "ts": _now(), "stage_id": stage_id, "type": type_, "data": data}
            )
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
        """Flush every buffered event, stop the thread, then raise any posting failure."""
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        if self._thread.is_alive():
            self._thread.join()
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
            rejected: HttpError | None = None
            try:
                resp = self._api.call("/api/worker/events", {"events": batch})
            except (RunGone, Unreachable) as exc:
                self.failure = exc
                return
            except HttpError as exc:
                rejected = exc  # a bug, not weather: drop the batch rather than loop on it
            else:
                if isinstance(resp, dict) and resp.get("cancel") is True:
                    self.cancel.set()
            last_post = time.monotonic()
            with self._cond:
                del self._buf[: len(batch)]
            if rejected is not None:
                say(f"events rejected ({rejected}); dropped {len(batch)} events")
                if not self._rejected_warned:
                    self._rejected_warned = True
                    self.emit("warning", None, {"message": f"events rejected: {rejected}"})


# ---------------------------------------------------------------- processes


@dataclass(frozen=True)
class ProcResult:
    exit_code: int | None
    stopped: str | None = None  # "timeout" | "cancel" | "abort"
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
    updates: dict[str, str | None]  # applied to SyncState.known once the server accepted them
    warnings: list[str]

    def add(self, path: str, content: str | None, mode: str) -> None:
        self.files.append({"path": path, "content": content, "mode": mode})
        self.updates[path] = content


def _walk_files(root: Path, rel_dir: str, warnings: list[str]) -> list[str]:
    """POSIX paths of the regular files under `rel_dir`. Never follows links."""
    top, err = resolve_inside(root, rel_dir)
    if err:
        warnings.append(f"sync dir {rel_dir} not synced: {err}")
        return []
    found: list[str] = []
    stack = [(rel_dir, top)]
    while stack:
        rel, here = stack.pop()
        try:
            children = sorted(here.iterdir())
        except FileNotFoundError:
            continue
        except OSError as exc:
            warnings.append(f"not synced: cannot list {rel}: {exc.strerror}")
            continue
        for child in children:
            child_rel = f"{rel}/{child.name}"
            try:
                st = child.lstat()
            except OSError:
                continue
            if is_link(st):
                warnings.append(f"not synced: {child_rel} is a symlink")
            elif stat.S_ISDIR(st.st_mode):
                problem = path_error(child_rel)
                if problem:
                    warnings.append(f"not synced: {child_rel}/: {problem}")
                else:
                    stack.append((child_rel, child))
            elif stat.S_ISREG(st.st_mode):
                found.append(child_rel)
            else:
                warnings.append(f"not synced: {child_rel} is not a regular file")
    return sorted(found)


class SyncState:
    """What the server holds of this run's context, so sync sends only what changed.

    Every sync=true manifest file is sent after each stage (block files: only the block). Other
    files under sync_dirs are sent when new or changed, and as `content: null` once they vanish.
    """

    def __init__(self, manifest: Manifest) -> None:
        last: dict[str, ManifestFile] = {}
        for f in manifest.files:  # the bundle comes after library files and wins
            last[f.path] = f
        self.manifest = manifest
        self.sync_files = {p: f for p, f in last.items() if f.sync}
        self.manifest_lower = {p.lower() for p in last}
        self.known: dict[str, str | None] = {p: f.content for p, f in self.sync_files.items()}
        self.unsynced: set[str] = set()  # manifest files that couldn't be written

    def _manifest_content(self, f: ManifestFile, data: bytes) -> tuple[str, str | None]:
        """(content to sync, why not) for a sync=true manifest file's bytes."""
        if f.mode == "block":
            try:
                content = block_read(data, self.manifest.key)
            except BlockError as exc:
                return "", str(exc)
        else:
            decoded = _decode(data)
            if decoded is None:
                return "", "is not valid UTF-8"
            content = decoded
        if len(content.encode("utf-8")) > self.manifest.max_file_bytes:
            return "", f"is over {self.manifest.max_file_bytes} bytes"
        return content, None

    def collect(self, root: Path) -> SyncBatch:
        m = self.manifest
        batch = SyncBatch([], {}, [])
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
                    batch.add(path, None, "replace")
                continue
            content, err = self._manifest_content(f, data)
            if err:
                batch.warnings.append(f"not synced: {path}: {err}")
                continue
            batch.add(path, content, f.mode)

        seen: set[str] = set()
        for sync_dir in m.sync_dirs:
            for path in _walk_files(root, sync_dir, batch.warnings):
                if path.lower() in self.manifest_lower or path in seen:
                    continue  # manifest files are handled above; library files never sync
                seen.add(path)
                problem = path_error(path)
                if problem:
                    batch.warnings.append(f"not synced: {path}: {problem}")
                    continue
                data, err = read_regular(root, path)
                if err or data is None:
                    batch.warnings.append(f"not synced: {path} {err or 'vanished'}")
                    continue
                if len(data) > m.max_file_bytes:
                    batch.warnings.append(f"not synced: {path} is over {m.max_file_bytes} bytes")
                    continue
                text = _decode(data)
                if text is None:
                    batch.warnings.append(f"not synced: {path} is not valid UTF-8")
                    continue
                if self.known.get(path) != text:
                    batch.add(path, text, "replace")

        for path, last in self.known.items():
            if path not in self.sync_files and path not in seen and last is not None:
                batch.add(path, None, "replace")
        return batch

    def accept(self, batch: SyncBatch) -> None:
        self.known.update(batch.updates)


def write_entry(root: Path, f: ManifestFile, key: str) -> str | None:
    """Materialise one manifest file (SPEC §9.3). Returns why it was skipped, or None."""
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
                block_write(existing, key, f.content) if f.mode == "block" else f.content.encode()
            )
        except BlockError as exc:
            return str(exc)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        return f"cannot write: {exc.strerror or exc}"
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
    """Executes one claimed run: workspace, files, stages, after-stage steps, completion."""

    def __init__(
        self, api: WorkerApi, events: EventSink, manifest: Manifest, workdir: Path
    ) -> None:
        self.api = api
        self.events = events
        self.m = manifest
        self.workdir = workdir
        self.repo = manifest.mode == "repo"
        self.sync = SyncState(manifest)
        self.owns_workdir = False
        self.ready = False  # workspace set up (offline commit + push is possible)
        self.start_head = ""
        self.pushed_head = ""
        self.current_stage: str | None = None
        self.protected = [*dict.fromkeys(f.path for f in manifest.files), *manifest.sync_dirs]
        self._protected_lower = [p.lower() for p in self.protected]

        base = {k: v for k, v in os.environ.items() if k.upper() != "AGENTOS_TOKEN"}
        base.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
        self.git_env = base
        self.stage_env = {
            **base,
            "AGENTOS_RUN_ID": str(manifest.run_id),
            "AGENTOS_TICKET_KEY": manifest.key,
        }
        if manifest.sync_dirs:
            self.stage_env["AGENTOS_SKILL_DIR"] = manifest.sync_dirs[0]

    # ------------------------------------------------------------ top level

    def execute(self) -> int:
        try:
            return self._execute()
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
        self._write_files()
        if self.repo:
            self._protect()
        self.ready = True
        for stage in self.m.stages:
            self.events.check()
            if self.events.cancel.is_set():
                return self._finish("cancelled", stage.id, None)
            status, reason = self._run_stage(stage)
            if status != "succeeded":
                # A plain cancel is not an error; a cancel whose after-stage steps failed is.
                error = None if reason == CANCELLED else reason
                return self._finish(status, stage.id, error)
        return self._finish("completed", None, None)

    def _finish(self, status: str, failed_stage: str | None, error: str | None) -> int:
        self.events.close()
        body = {
            "status": status,
            "failed_stage": failed_stage,
            "error": None if error is None else _clip(error),
        }
        try:
            self.api.call("/api/worker/complete", body)
        except HttpError as exc:
            say(f"complete rejected: {exc}")
            return 1
        say(f"run {self.m.run_id} {status}" + (f": {error}" if error else ""))
        if status in ("completed", "cancelled") and self.owns_workdir:
            try:
                remove_tree(self.workdir)
            except OSError as exc:
                say(f"could not delete the workdir {self.workdir}: {exc}")
        return 0

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
        repo_url, base_branch, branch = self.m.repo_url, self.m.base_branch, self.m.branch
        if repo_url is None or base_branch is None or branch is None:  # parse_manifest ensures
            return "repo mode needs repo_url, base_branch and branch"
        say(f"cloning into {self.workdir}")
        r = self._git("clone", "--filter=blob:none", "--no-tags", "--", repo_url, ".", show=True)
        if r.code:
            return f"git clone failed: {r.tail()}"
        exists = self._git("rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}").code == 0
        start = f"origin/{branch}" if exists else f"origin/{base_branch}"
        r = self._git("checkout", "-q", "-B", branch, start, show=True)
        if r.code:
            return f"git checkout failed: cannot start {branch} from {start}: {r.tail()}"
        r = self._git("rev-parse", "HEAD")
        if r.code:
            return f"git rev-parse HEAD failed: {r.tail()}"
        self.start_head = self.pushed_head = r.out.decode().strip()
        name = os.environ.get("AGENTOS_GIT_NAME") or "AgentOS"
        email = os.environ.get("AGENTOS_GIT_EMAIL") or "agentos@localhost"
        for setting, value in (("user.name", name), ("user.email", email)):
            r = self._git("config", setting, value)
            if r.code:
                return f"git config {setting} failed: {r.tail()}"
        return None

    def _write_files(self) -> None:
        for f in self.m.files:
            problem = write_entry(self.workdir, f, self.m.key)
            if problem:
                self.sync.unsynced.add(f.path)
                self.events.warn(None, f"skipped {f.path}: {problem}")

    def _protect(self) -> None:
        """Keep manifest files and sync dirs out of `git add` (SPEC §9.3 git protection)."""
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
        lines = "".join(f"/{p}\n" for p in [*exclude, *self.m.sync_dirs])
        junk = "".join(f"{p}\n" for p in JUNK_PATTERNS)
        try:
            info = self.workdir / ".git" / "info"
            info.mkdir(parents=True, exist_ok=True)
            with (info / "exclude").open("ab") as fh:
                fh.write(
                    f"\n# agentos context files\n{lines}# agentos build caches\n{junk}".encode()
                )
        except OSError as exc:
            self.events.warn(None, f"cannot write .git/info/exclude: {exc}")

    # ------------------------------------------------------------ stages (SPEC §9.3)

    def _run_stage(self, stage: ManifestStage) -> tuple[str, str | None]:
        self.current_stage = stage.id
        say(f"stage {stage.id} ({stage.kind}) started")
        self.events.emit("stage_started", stage.id, {})
        status, exit_code, reason = self._exec_stage(stage)
        problem = self._after_stage(stage.id)
        if problem:
            if status == "succeeded":
                status, reason = "failed", problem
            else:
                reason = f"{reason}; {problem}" if reason else problem
        reason = None if reason is None else _clip(reason)
        self.events.emit(
            "stage_finished",
            stage.id,
            {"status": status, "exit_code": exit_code, "reason": reason},
        )
        say(f"stage {stage.id} {status}" + (f": {reason}" if reason else ""))
        return status, reason

    def _resolve(self, stage: ManifestStage) -> tuple[list[str], str | None]:
        if stage.kind == "agent":
            argv = list(stage.argv or ())
        else:
            argv = [*shell_prefix(), stage.command or ""]
        program = shutil.which(argv[0], path=self.stage_env.get("PATH"))
        if program is None:
            what = "program" if stage.kind == "agent" else "shell (set AGENTOS_SHELL)"
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
        return None

    def _exec_stage(self, stage: ManifestStage) -> tuple[str, int | None, str | None]:
        argv, problem = self._resolve(stage)
        if problem:
            return "failed", None, problem
        stage_id = stage.id

        def on_line(stream: str, text: str, truncated: bool) -> None:
            self.events.log(stage_id, stream, text, truncated)

        short = min(SHELL_LINE_BYTES, self.m.max_line_bytes)
        result = run_process(
            argv,
            cwd=self.workdir,
            env=self.stage_env,
            stdin=stage.stdin if stage.kind == "agent" else None,
            timeout_s=stage.timeout_s,
            caps=(self.m.max_line_bytes if stage.kind == "agent" else short, short),
            on_line=on_line,
            interrupt=self._interrupt,
        )
        if result.error:
            return "failed", None, result.error
        if result.stopped == "abort":
            self.events.check()
        if result.stopped == "cancel":
            return "cancelled", None, CANCELLED
        if result.stopped == "timeout":
            return "failed", None, f"timed out after {stage.timeout_s}s"
        if result.exit_code == 0:
            return "succeeded", 0, None
        return "failed", result.exit_code, f"exited with code {result.exit_code}"

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
        try:
            self.api.call("/api/worker/sync", {"stage_id": stage_id, "files": batch.files})
        except HttpError as exc:
            self.events.warn(stage_id, f"sync rejected ({exc}): context changes were not saved")
            return
        self.sync.accept(batch)

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
        if f"agentos:begin {self.m.key}".encode() in r.out:
            return "pre-push guard: commits contain the agentos context block"
        return None

    def _push(self, stage_id: str | None) -> str | None:
        r = self._git("rev-parse", "HEAD")
        if r.code:
            return f"git rev-parse HEAD failed: {r.tail()}"
        head = r.out.decode().strip()
        if head == self.pushed_head:
            return None
        if isinstance(self.events.failure, RunGone):  # taken away while we were committing
            raise self.events.failure
        r = self._git("push", "-u", "origin", str(self.m.branch), show=True, stage_id=stage_id)
        if r.code:
            return "push rejected" if b"rejected" in r.out + r.err else f"push failed: {r.tail()}"
        self.pushed_head = head
        return None

    def _save_offline(self) -> None:
        stage_id = self.current_stage or "setup"
        problem = self._commit(stage_id) or self._guard() or self._push(None)
        say(f"could not save the work: {problem}" if problem else "work committed and pushed")

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
            cap = min(SHELL_LINE_BYTES, self.m.max_line_bytes)
            for raw in (result.out + b"\n" + result.err).splitlines():
                if raw.strip() and b"\0" not in raw:
                    text = raw[:cap].decode("utf-8", "replace")
                    self.events.log(stage_id, "system", text, len(raw) > cap)
        return result


# ---------------------------------------------------------------- entry point


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="worker.py", description=__doc__)
    parser.add_argument(
        "--server", required=True, help="AgentOS server URL, e.g. http://127.0.0.1:8000"
    )
    parser.add_argument("--token", help="dispatch token (default: $AGENTOS_TOKEN)")
    parser.add_argument("--workdir", help="workspace directory (default: ./agentos-run-<run_id>)")
    args = parser.parse_args(argv)
    args.token = args.token or os.environ.get("AGENTOS_TOKEN")
    if not args.token:
        parser.error("a dispatch token is required (--token or AGENTOS_TOKEN)")
    if urllib.parse.urlsplit(args.server).scheme not in ("http", "https"):
        parser.error("--server must be an http:// or https:// URL")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    os.environ.pop("AGENTOS_TOKEN", None)  # never handed to git or to stages
    worker_token = secrets.token_urlsafe(32)
    say(f"claiming a run from {args.server}")
    try:
        resp = Client(args.server, args.token).post(
            "/api/worker/claim", {"worker_token": worker_token}
        )
    except HttpError as exc:
        if exc.status in (409, 410):
            say(f"nothing to do: {exc}")
            return 0
        say(f"claim failed: {exc}")
        return 1
    except Unreachable as exc:
        say(f"claim failed: {exc}")
        return 1

    api = WorkerApi(Client(args.server, worker_token))
    events = EventSink(api)
    events.start()
    raw = resp.get("manifest") if isinstance(resp, dict) else None
    try:
        manifest = parse_manifest(raw)
    except ManifestError as exc:
        say(f"invalid manifest: {exc}")
        try:
            events.close()
            api.call(
                "/api/worker/complete",
                {
                    "status": "failed",
                    "failed_stage": None,
                    "error": _clip(f"invalid manifest: {exc}"),
                },
            )
        except (HttpError, RunGone, Unreachable) as again:
            say(f"could not report the failure: {again}")
            return 1
        return 0
    say(f"claimed run {manifest.run_id} ({manifest.key}, {len(manifest.stages)} stage(s))")
    workdir = Path(args.workdir or f"agentos-run-{manifest.run_id}").resolve()
    return Run(api, events, manifest, workdir).execute()


if __name__ == "__main__":
    sys.exit(main())
