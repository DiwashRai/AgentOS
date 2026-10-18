"""One bound job (`Run`): workspace, files, stages and loop blocks, the after-stage steps
(sync, commit, guard, push), the PR lookup and the result.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import time
import traceback
import urllib.parse
import zipfile
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentos_worker import tunables
from agentos_worker.console import say
from agentos_worker.echo import Echo
from agentos_worker.events import EventSink
from agentos_worker.files import (
    existing_entry,
    is_link,
    remove_tree,
    resolve_inside,
    walk_files,
    write_entry,
)
from agentos_worker.fit import Unfit
from agentos_worker.git import GitResult
from agentos_worker.github import GitHub, GitHubError, publish_branch, track_published
from agentos_worker.package import AgentCheck, AgentStage, Loop, Package, ShellStage, Stage
from agentos_worker.paths import branch_error, pr_url_error
from agentos_worker.process import (
    OnLine,
    ProcResult,
    cmd_guard_error,
    run_process,
    shell_prefix,
)
from agentos_worker.publication import PUSH_REJECTED, PublishError, api_publish, github_repo
from agentos_worker.sync import SyncState
from agentos_worker.text import clip
from agentos_worker.verdict_reading import checked_verdict, file_verdict, result_verdict
from agentos_worker.verdicts import (
    SHELL_CHECK_LINES,
    VERDICT_FILE,
    Verdict,
    combine_feedback,
    shell_verdict,
)
from agentos_worker.worker_api import Client, HttpError, ResultRejected, RunGone, Unreachable

SHELL_LINE_BYTES = 8192  # line cap for every stream except agent-stage stdout


CANCELLED = "cancelled"  # stage_finished reason for a plain cancel
OVER_BUDGET = "time budget exceeded"  # the job's budget_s ran out


# The clone's hooks: each runs the user's own hook of that name (from their core.hooksPath, else
# .git/hooks), so their hooks apply as if they committed themselves. pre-push first refuses any
# push without the worker's per-run marker: a guardrail against an agent's accidental push, not a
# boundary (the marker is readable in this file and `--no-verify` skips the hook). The
# never-forced push (PUSH_REJECTED) is the protection.
HOOK_NAMES = (
    "applypatch-msg",
    "pre-applypatch",
    "post-applypatch",
    "pre-commit",
    "pre-merge-commit",
    "prepare-commit-msg",
    "commit-msg",
    "post-commit",
    "pre-rebase",
    "post-checkout",
    "post-merge",
    "pre-push",
    "pre-auto-gc",
    "post-rewrite",
    "reference-transaction",
)
PUSH_GUARD = '[ "$AGENTOS_PUSH" = "{}" ] || {{ echo "agentos: no push" >&2; exit 1; }}\n'
HOOK_SHIM = '#!/bin/sh\n{}h={}\n[ -x "$h" ] && exec "$h" "$@"\nexit 0\n'


# `.agentos/` belongs to AgentOS: never committed, never synced.
AGENTOS_DIR = ".agentos"


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
        detail = f": {clip(err[-1], 200)}" if err else ""
        return None, f"{program} exited with code {result.exit_code}{detail}"

    if len(out) != 1:
        return None, f"expected one URL line from {program}, got {len(out)} lines"
    problem = pr_url_error(out[0])
    if problem:
        return None, f"{program} printed {clip(out[0], 200)!r}: {problem}"
    return out[0], ""


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
        self.repo = package.workspace.mode == "repo"
        self.branch = str(package.workspace.branch)
        self.max_line_bytes = package.limits.max_line_bytes
        self.sync = SyncState(package)
        self.owns_workdir = False
        self.ready = False  # workspace set up (offline commit + push is possible)
        self.start_head = ""
        self.pushed_head = ""
        self.current_stage: str | None = None
        self.index = {s.id: i for i, s in enumerate(package.stages)}
        self.protected = [
            *dict.fromkeys(f.path for f in package.files),
            *package.workspace.protect,
            AGENTOS_DIR,
        ]
        self._protected_lower = [p.lower() for p in self.protected]
        self.echo = os.environ.get("AGENTOS_ECHO", "").strip() != "0"  # see Echo
        self.api_mode = self.repo and api_publish(os.environ)  # publish through the GitHub API
        self.github: GitHub | None = None  # set up by _setup_github in API mode
        self.branch_exists = False  # the branch was on the remote at clone or has been published
        self.deadline = time.monotonic() + package.job.budget_s

        base = {k: v for k, v in os.environ.items() if k.upper() != "AGENTOS_TOKEN"}
        base.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
        self.push_marker = secrets.token_hex(16)  # in the worker's git env, not the stages'
        self.git_env = {**base, "AGENTOS_PUSH": self.push_marker}
        self.stage_env = {
            **base,
            **package.env,
            "AGENTOS_RUN_ID": str(package.run_id),
            "AGENTOS_TICKET_KEY": package.ticket.key,
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
        except Exception as exc:  # noqa: BLE001 the run's top-level handler: report any failure
            say(f"worker error:\n{traceback.format_exc()}")
            try:
                self._finish("failed", self.current_stage, f"worker error: {exc}")
            except Exception as again:  # noqa: BLE001 last resort: reporting the failure failed too
                say(f"could not report the failure: {again}")
            return 1
        finally:  # a run that ended without `_finish` closing the sink must not leave it posting
            self.events.abandon()

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
        sync_back, problem = resolve_inside(self.workdir, self.m.sync_back.dir)
        try:
            if not problem:
                sync_back.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            problem = str(exc)
        if problem:
            self.events.warn(None, f"cannot create {self.m.sync_back.dir}: {problem}")
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
        error = None if error is None else clip(error)
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
        folder, cap = self.m.sync_back.dir, self.m.sync_back.max_bytes
        files, skipped = walk_files(self.workdir, folder)
        if not files and not skipped:
            return
        if self.upload is None:
            self.events.warn(None, f"{folder} is not returned in mailbox mode")
            return
        if skipped:
            self.events.warn(
                None,
                clip(
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
            self.events.warn(None, clip(f"{folder} not sent: {exc!r}"))
            return
        if buf.tell() > cap:
            self.events.warn(None, f"{folder} not sent: the zip is over {cap} bytes")
            return
        data = buf.getvalue()

        digest = hashlib.sha256(data).hexdigest()
        path = f"/api/worker/jobs/{self.m.job.id}/sync-back?sha256={digest}&files={len(files)}"
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
        repo_url, base_branch, branch = ws.repo_url, ws.base_branch, ws.branch
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
        r = self._git("checkout", "-q", "-B", self.branch, start, show=True)
        if r.code:
            return f"git checkout failed: cannot start {branch} from {start}: {r.tail()}"
        r = self._git("rev-parse", "HEAD")
        if r.code:
            return f"git rev-parse HEAD failed: {r.tail()}"
        self.start_head = self.pushed_head = r.out.decode().strip()
        return self._install_hooks() or self._set_identity()

    def _install_hooks(self) -> str | None:
        """Point the clone's core.hooksPath at HOOK_SHIMs that run the user's own hooks, behind
        the push guardrail. Set locally, so a global hooksPath doesn't replace the guardrail."""
        r = self._git("config", "--path", "--get", "core.hooksPath")
        mine = r.out.decode().strip() if r.code == 0 else ""
        theirs = (self.workdir / mine) if mine else (self.workdir / ".git" / "hooks")
        hooks = self.workdir / ".git" / "agentos-hooks"
        try:
            hooks.mkdir()
            for name in HOOK_NAMES:
                guard = PUSH_GUARD.format(self.push_marker) if name == "pre-push" else ""
                target = shlex.quote((theirs / name).as_posix())
                (hooks / name).write_bytes(HOOK_SHIM.format(guard, target).encode())
                (hooks / name).chmod(0o755)
        except OSError as exc:
            return f"cannot install the git hooks: {exc}"
        r = self._git("config", "core.hooksPath", hooks.as_posix())
        return f"git config core.hooksPath failed: {r.tail()}" if r.code else None

    def _set_identity(self) -> str | None:
        """On Actions (API mode) commit as AGENTOS_GIT_NAME / _EMAIL, default AgentOS. A local
        or client worker is the user: their git config (identity, signing) applies as if they
        committed themselves, and nothing is set."""
        if not self.api_mode:
            return None
        for setting, var, default in (
            ("user.name", "AGENTOS_GIT_NAME", "AgentOS"),
            ("user.email", "AGENTOS_GIT_EMAIL", "agentos@localhost"),
        ):
            r = self._git("config", setting, os.environ.get(var, "").strip() or default)
            if r.code:
                return f"git config {setting} failed: {r.tail()}"
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
        lines = "".join(f"/{p}\n" for p in [*exclude, *self.m.workspace.protect])
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
        status, exit_code, reason, verdict = self._exec_round(stage, round_, loop, feedback)
        problem = self._after_stage(stage.id)
        if problem:
            verdict = None
            if status == "succeeded":
                status, reason = "failed", problem
            else:
                reason = f"{reason}; {problem}" if reason else problem
        reason = None if reason is None else clip(reason)
        self.events.emit(
            "stage_finished",
            stage.id,
            {"status": status, "exit_code": exit_code, "reason": reason, "round": round_},
        )
        say(f"stage {stage.id} {status}" + (f": {reason}" if reason else ""))
        return status, reason, verdict

    def _exec_round(
        self, stage: Stage, round_: int, loop: Loop | None, feedback: str
    ) -> tuple[str, int | None, str | None, Verdict | None]:
        """The stage's call(s) in this round: (status, exit code, reason, a check's verdict)."""
        match stage:
            case ShellStage(check=True) if loop is not None:
                return self._shell_check(stage, round_, loop.max_rounds, feedback)
            case AgentStage(check=AgentCheck() as check) if loop is not None:
                return self._agent_check(stage, check, round_, loop.max_rounds, feedback)
            case _:
                return *self._exec(stage, *self._call(stage, round_, feedback)), None

    def _call(self, stage: Stage, round_: int, feedback: str) -> tuple[list[str], str | None]:
        """(argv, stdin) of `stage` in `round_`: round 1 as given, later rounds with feedback."""
        match stage:
            case ShellStage():
                return [*shell_prefix(), stage.command], None
            case AgentStage() if round_ == 1:
                return list(stage.argv), stage.stdin
            case AgentStage():
                return stage.rounds[round_ - 2].build(feedback)

    def _resolve(self, what: str, argv: list[str]) -> tuple[list[str], str | None]:
        program = shutil.which(argv[0], path=self.stage_env.get("PATH"))
        if program is None:
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
        short = min(SHELL_LINE_BYTES, self.max_line_bytes)
        match stage:
            case AgentStage():
                what, stdout_cap = "program", self.max_line_bytes
                echo = Echo(stage.id) if self.echo else None
            case ShellStage():
                what, stdout_cap, echo = "shell (set AGENTOS_SHELL)", short, None
        argv, problem = self._resolve(what, argv)
        if problem:
            return "failed", None, problem
        stage_id = stage.id

        def on_line(stream: str, text: str, truncated: bool) -> None:
            self.events.log(stage_id, stream, text, truncated)
            if collect is not None:
                collect(stream, text, truncated)
            if echo is not None:
                echo(stream, text, truncated)

        result = run_process(
            argv,
            cwd=self.workdir,
            env=self.stage_env,
            stdin=stdin,
            timeout_s=stage.timeout_s,
            caps=(stdout_cap, short),
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
    # A check's call(s) and verdict: (status, exit code, reason, verdict). No verdict means the
    # stage failed or was cancelled: an invalid verdict is never a pass, nor a fail.

    def _shell_check(
        self, stage: ShellStage, round_: int, max_rounds: int, feedback: str
    ) -> tuple[str, int | None, str | None, Verdict | None]:
        """A shell check: its exit code is the verdict."""
        argv, stdin = self._call(stage, round_, feedback)
        lines: deque[str] = deque(maxlen=SHELL_CHECK_LINES)
        status, code, reason = self._exec(stage, argv, stdin, lambda _s, t, _c: lines.append(t))
        if code is None:
            return status, None, reason, None
        verdict = shell_verdict(code, list(lines))
        return self._decided(stage, round_, max_rounds, verdict, "exit_code", 0, code)

    def _agent_check(
        self, stage: AgentStage, check: AgentCheck, round_: int, max_rounds: int, feedback: str
    ) -> tuple[str, int | None, str | None, Verdict | None]:
        """An agent check: its JSON verdict, repaired while it is invalid and repairs are left."""
        argv, stdin = self._call(stage, round_, feedback)
        attempt, error = 0, ""
        while True:
            status, code, reason, found = self._verdict_call(stage, check, argv, stdin)
            if code is None:  # not started, timed out or cancelled: never repaired
                return status, None, reason, None
            match found:
                case Verdict():
                    source = check.verdict_from
                    return self._decided(stage, round_, max_rounds, found, source, attempt, code)
                case Unfit(why=why):
                    error = clip(why)
            if attempt >= check.verdict_repairs:
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
            argv, stdin = check.repairs[round_ - 1].build(error)
        after = f" after {attempt} repair{'s' if attempt != 1 else ''}" if attempt else ""
        return "failed", code, f"{stage.name} gave no valid verdict{after}: {error}", None

    def _verdict_call(
        self, stage: AgentStage, check: AgentCheck, argv: list[str], stdin: str | None
    ) -> tuple[str, int | None, str | None, Verdict | Unfit | None]:
        """One call of an agent check, and its valid verdict or why there is none; neither when it
        didn't run to its end (exit code None), and then the status and reason are the call's."""
        from_file = check.verdict_from == "file"
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
            return status, None, reason, None
        if code != 0:
            return status, code, reason, Unfit(f"exited with code {code}")
        if from_file:
            value, error = file_verdict(self.workdir)
        else:
            value, error = result_verdict(results[-1] if results else None)
        return status, code, reason, checked_verdict(value) if error is None else Unfit(error)

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
        message = f"{self.m.ticket.key} [{stage_id}] wip"
        r = self._git("commit", "-q", "-m", message, show=True)
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
        if any(f"agentos:begin {b}".encode() in r.out for b in self.m.workspace.blocks):
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
        if datetime.now(UTC) >= self.m.job.not_after:
            raise RunGone(f"the job's not_after ({self.m.job.not_after.isoformat()}) has passed")

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
                    timeout=tunables.GIT_TIMEOUT_S,
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
                    timeout=tunables.GIT_TIMEOUT_S,
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
