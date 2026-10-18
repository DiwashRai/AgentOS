"""Publishing a branch through the Git Data API as one commit (`publish_branch`), then tracking
it locally (`track_published`): ancestry checks, blob uploads, the tree, the commit, the guarded
ref write and the local git reconciliation. Any failure is a PublishError, the stage's failure
reason.
"""

from __future__ import annotations

import base64
import urllib.parse
from collections.abc import Callable
from typing import Any

from agentos_worker.git import GitRunner
from agentos_worker.github import GitHub, GitHubError
from agentos_worker.publication import PUSH_REJECTED, PublishError, parse_diff_tree, tree_entry
from agentos_worker.text import decode_utf8


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


def _tree_entries(
    git: GitRunner, github: GitHub, base: str, head: str
) -> list[dict[str, str | None]]:
    """The tree entries that turn `base`'s tree into `head`'s, uploading what can't go inline."""
    diffs = parse_diff_tree(_git_out(git, "diff-tree", "-r", "-z", "--no-renames", base, head))
    blobs = _read_blobs(git, list(dict.fromkeys(d.new_sha for d in diffs if d.needs_content)))

    entries: list[dict[str, str | None]] = []
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
