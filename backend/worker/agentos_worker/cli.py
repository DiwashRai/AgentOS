"""AgentOS worker: runs one job of a run, reporting it as the run's journal, then exits.

Python >= 3.11 standard library only (enforced by a test). The server serves it, with the
`agentos_worker` package beside it, as one zip at GET /api/worker/worker.py (agentos_worker.archive)
so runners always get the matching version; a checkout runs it in place. See SPEC §9.

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
checked by `checked_verdict`, and repaired by resuming the check's session at most `verdict_repairs`
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
import os
import urllib.parse
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LiveMode:
    """One job from the server; with `lock_file`, a client's run worker."""

    server: str
    token: str
    workdir: Path | None  # None: ./agentos-run-<run_id>
    lock_file: Path | None


@dataclass(frozen=True)
class FileMode:
    """Mailbox mode: the package is a file, the journal goes to the outbox."""

    manifest: Path
    outbox: Path
    workdir: Path


@dataclass(frozen=True)
class ClientMode:
    """Lease this user's runs and start a worker for each (the token: $AGENTOS_CLIENT_TOKEN)."""

    server: str
    root: Path  # absolute
    max_runs: int
    env_file: Path | None


Mode = LiveMode | FileMode | ClientMode


def parse_args(argv: list[str]) -> Mode:
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
    if args.client:
        return _client_mode(parser, args)
    if any(value is not None for value in (args.max_runs, args.workdir_root, args.env_file)):
        parser.error("--max-runs, --workdir-root and --env-file need --client")
    if args.manifest is not None or args.outbox is not None:
        return _file_mode(parser, args)
    return _live_mode(parser, args)


def _file_mode(parser: argparse.ArgumentParser, args: argparse.Namespace) -> FileMode:
    if args.server is not None or args.token is not None:
        parser.error("--manifest/--outbox (mailbox mode) can't be used with --server/--token")
    if not (args.manifest and args.outbox and args.workdir):
        parser.error("mailbox mode needs --manifest, --outbox and --workdir")
    return FileMode(Path(args.manifest), Path(args.outbox), Path(args.workdir))


def _live_mode(parser: argparse.ArgumentParser, args: argparse.Namespace) -> LiveMode:
    if args.server is None:
        parser.error("the following arguments are required: --server")
    token = args.token or os.environ.get("AGENTOS_TOKEN")
    if not token:
        parser.error("a job token is required (--token or AGENTOS_TOKEN)")
    if urllib.parse.urlsplit(args.server).scheme not in ("http", "https"):
        parser.error("--server must be an http:// or https:// URL")
    workdir = Path(args.workdir) if args.workdir else None
    lock_file = None if args.lock_file is None else Path(args.lock_file)
    return LiveMode(args.server, token, workdir, lock_file)


def _client_mode(parser: argparse.ArgumentParser, args: argparse.Namespace) -> ClientMode:
    used = [args.token, args.workdir, args.lock_file, args.manifest, args.outbox]
    if any(value is not None for value in used):
        parser.error(
            "--client can't be used with --token, --workdir, --lock-file, --manifest or --outbox"
        )
    if args.server is None:
        parser.error("--client needs --server")
    if urllib.parse.urlsplit(args.server).scheme not in ("http", "https"):
        parser.error("--server must be an http:// or https:// URL")
    if not os.environ.get("AGENTOS_CLIENT_TOKEN", "").strip():
        parser.error("client mode needs a client token in AGENTOS_CLIENT_TOKEN")
    max_runs = 1 if args.max_runs is None else args.max_runs
    if not 1 <= max_runs <= 100:  # a lease lists at most 100 `alive` jobs
        parser.error("--max-runs must be between 1 and 100")
    root = Path(args.workdir_root or "~/.agentos/workspaces").expanduser().resolve()
    env_file = Path(args.env_file) if args.env_file else None
    return ClientMode(args.server, root, max_runs, env_file)
