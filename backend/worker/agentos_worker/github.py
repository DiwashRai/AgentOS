"""Publishing a branch through the Git Data API of one repo (`GitHub`, `publish_branch`) as
one commit, then tracking it locally. A GitHub failure is a stage failure (PublishError), never a
lost run or an unreachable server.
"""

from __future__ import annotations

import base64
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import IO, Any

from agentos_worker import tunables
from agentos_worker.console import say
from agentos_worker.git import GitRunner
from agentos_worker.publication import (
    PUSH_REJECTED,
    PublishError,
    classify,
    parse_diff_tree,
    retry_wait,
    scrub,
    tree_entry,
)
from agentos_worker.text import decode_utf8

GITHUB_RETRY_BUDGET_S = 300.0  # rate limits, network errors and 5xx are retried this long


GITHUB_API_VERSION = "2022-11-28"


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
        delay = tunables.BACKOFF_START_S
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
                with self._opener.open(request, timeout=tunables.HTTP_TIMEOUT_S) as resp:
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
            delay = min(delay * 2, tunables.BACKOFF_CAP_S)

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
    parts = [p for p in (decode_utf8(f) for f in out.split(b"\0", 3)) if p is not None]
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
