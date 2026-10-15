"""Publish to a real GitHub repo through the Git Data API, the way the worker does on Actions.

A manual check, not a test: pytest never collects it and CI never runs it. From backend/:

    uv run python worker/live_publish.py <owner/repo>

with GH_TOKEN set to a token that can write to the repo's contents (GITHUB_API_URL and
GITHUB_SERVER_URL default to github.com). It clones the repo into a temp dir and, on a throwaway
branch `agentos-live-publish-<timestamp>` off the default branch, commits added, modified,
deleted, executable, symlinked (index-only, so it works without symlink support) and binary
files, a merge and an empty commit. It publishes them with `worker.publish_branch`, checks the
remote tree and the commit count, then publishes a second batch and checks it fast-forwards. The
branch is deleted at the end, whatever happened.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import worker

GIT_TIMEOUT_S = 300


class Fail(Exception):
    """A check failed; the message says which."""


def make_git(workdir: Path, env: dict[str, str]) -> worker.GitRunner:
    def git(*args: str, stdin: bytes | None = None) -> worker.GitResult:
        p = subprocess.run(  # noqa: S603 - git with argv built by this script, no shell
            ["git", "-c", "core.quotePath=false", *args],  # noqa: S607 - git from PATH, as the worker
            cwd=workdir,
            env=env,
            input=stdin,
            stdin=None if stdin is not None else subprocess.DEVNULL,
            capture_output=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
        return worker.GitResult(p.returncode, p.stdout, p.stderr)

    return git


def git_env(server_url: str, token: str) -> dict[str, str]:
    """The environment for git: the token as an HTTP header for the server (env, never argv)."""
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"http.{server_url.rstrip('/')}/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
            "GIT_AUTHOR_NAME": "AgentOS live publish",
            "GIT_AUTHOR_EMAIL": "agentos@example.invalid",
            "GIT_COMMITTER_NAME": "AgentOS live publish",
            "GIT_COMMITTER_EMAIL": "agentos@example.invalid",
        }
    )
    return env


def out(git: worker.GitRunner, *args: str, stdin: bytes | None = None) -> str:
    r = git(*args, stdin=stdin)
    if r.code:
        raise Fail(f"git {args[0]} failed: {r.tail()}")
    return r.out.decode().strip()


def write(root: Path, rel: str, data: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def commit(git: worker.GitRunner, message: str, *paths: str) -> str:
    """Commit what is staged (plus `paths`); never `add -A`, which would drop index-only links."""
    if paths:
        out(git, "add", "--", *paths)
    out(git, "commit", "-q", "--allow-empty", "-m", message)
    return out(git, "rev-parse", "HEAD")


def first_batch(git: worker.GitRunner, root: Path, base: str, branch: str) -> int:
    """Added text, binary, executable and symlinked files; a modify and a delete; a merge of a
    side branch (not on the remote either); an empty commit. Returns how many commits."""
    write(root, "live/a.txt", b"first line\n")
    write(root, "live/old.txt", b"to be deleted\n")
    write(root, "live/data.bin", bytes(range(256)) * 4)
    write(root, "live/run.sh", b"#!/bin/sh\necho live\n")
    out(git, "add", "--chmod=+x", "--", "live/run.sh")
    target = out(git, "hash-object", "-w", "--stdin", stdin=b"a.txt")
    out(git, "update-index", "--add", "--cacheinfo", f"120000,{target},live/link")
    commit(
        git,
        "live: add text, binary, executable and symlink",
        "live/a.txt",
        "live/old.txt",
        "live/data.bin",
    )

    write(root, "live/a.txt", b"first line\nsecond line\n")
    out(git, "rm", "-q", "--", "live/old.txt")
    commit(git, "live: modify and delete", "live/a.txt")

    out(git, "switch", "-q", "-c", f"{branch}-side", base)
    write(root, "live/side.txt", b"from the side branch\n")
    commit(git, "live: side branch", "live/side.txt")
    out(git, "switch", "-q", branch)
    out(git, "merge", "-q", "--no-ff", "-m", "live: merge the side branch", f"{branch}-side")

    commit(git, "live: empty commit")
    return 5


def second_batch(git: worker.GitRunner, root: Path) -> int:
    """A modify, a mode-only change and a delete on top of the published branch."""
    write(root, "live/a.txt", b"first line\nsecond line\nthird line\n")
    out(git, "update-index", "--chmod=-x", "live/run.sh")
    out(git, "rm", "-q", "--", "live/data.bin")
    commit(git, "live: second batch", "live/a.txt")
    return 1


def check_remote(github: worker.GitHub, git: worker.GitRunner, sha: str) -> Any:
    """The remote commit `sha`; fails unless its tree is the local HEAD's tree."""
    remote = github.call("GET", f"/git/commits/{sha}")
    local_tree = out(git, "rev-parse", "HEAD^{tree}")
    if remote["tree"]["sha"] != local_tree:
        raise Fail(f"remote tree {remote['tree']['sha']} != local tree {local_tree}")
    return remote


def publish(
    git: worker.GitRunner, github: worker.GitHub, branch: str, pushed: str, exists: bool
) -> tuple[str, int]:
    sha, count = worker.publish_branch(git, github, branch, pushed, exists)
    problem = worker.track_published(git, branch, sha)
    if problem:
        raise Fail(problem)
    return sha, count


def run(repo: str, token: str, api_url: str, server_url: str, tmp: Path) -> None:
    github = worker.GitHub(api_url, token, repo)
    root = tmp / "clone"
    env = git_env(server_url, token)
    clone = make_git(tmp, env)
    out(clone, "clone", "-q", "--filter=blob:none", f"{server_url.rstrip('/')}/{repo}.git", "clone")

    git = make_git(root, env)
    out(git, "config", "core.autocrlf", "false")
    base = out(git, "rev-parse", "HEAD")
    branch = f"agentos-live-publish-{time.strftime('%Y%m%d-%H%M%S')}"
    out(git, "switch", "-q", "-c", branch)

    try:
        expected = first_batch(git, root, base, branch)
        sha1, count = publish(git, github, branch, base, exists=False)
        check_remote(github, git, sha1)
        if count != expected:
            raise Fail(f"first batch: published {count} commits, expected {expected}")
        print(f"first batch: {count} commits, remote tree matches ({sha1[:12]})")

        expected = second_batch(git, root)
        sha2, count = publish(git, github, branch, sha1, exists=True)
        remote = check_remote(github, git, sha2)
        parents = [p["sha"] for p in remote["parents"]]
        ref = github.call("GET", f"/git/ref/heads/{branch}")
        if count != expected or parents != [sha1] or ref["object"]["sha"] != sha2:
            raise Fail("second batch did not fast-forward the branch")
        print(f"second batch: fast-forwarded to {sha2[:12]}")
    finally:
        try:
            github.call("DELETE", f"/git/refs/heads/{branch}")
            print(f"deleted {branch}")
        except worker.GitHubError as exc:
            print(f"could not delete {branch}: {exc}")


def main(argv: list[str]) -> int:
    token = os.environ.get("GH_TOKEN", "").strip()
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com").strip()
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").strip()
    if len(argv) != 1 or not token:
        print("usage: GH_TOKEN=... python worker/live_publish.py <owner/repo>", file=sys.stderr)
        return 2
    repo, why = worker.github_repo(f"{server_url.rstrip('/')}/{argv[0]}", server_url)
    if repo is None:
        print(f"bad repo: {why}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(
        prefix="agentos-live-publish-", ignore_cleanup_errors=True
    ) as tmp:
        try:
            run(repo, token, api_url, server_url, Path(tmp))
        except (Fail, worker.PublishError, worker.GitHubError) as exc:
            print(f"FAIL: {worker.scrub(str(exc), (token,))}", file=sys.stderr)
            return 1
        finally:
            worker.remove_tree(Path(tmp) / "clone")
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
