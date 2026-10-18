"""The rules for publishing through the GitHub Git Data API (on Actions: GITHUB_ACTIONS=true
with GH_TOKEN; elsewhere the worker pushes with git): the repo from its URL, the tree entries
from `git diff-tree`, and what a failed GitHub call means (`classify`). A failure is a
PublishError, the stage's failure reason.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agentos_worker.text import clip, decode_utf8

PUSH_REJECTED = "push rejected: the branch moved during the stage; did the agent push?"


GITHUB_MIN_WAIT_S = 1.0  # the shortest pause GitHub's rate-limit answers get


INLINE_MAX_BYTES = 1024 * 1024  # text up to this goes inline in the tree; larger is a blob
MAX_GITHUB_MESSAGE_CHARS = 500


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
        path = decode_utf8(raw_path)
        if match is None or path is None:
            raise PublishError("push failed: unexpected git diff-tree output (or a non-UTF-8 path)")
        old_mode, new_mode, old_sha, new_sha, status = match.groups()
        entries.append(DiffEntry(old_mode, new_mode, old_sha, new_sha, status, path))
    return entries


def inline_text(data: bytes) -> str | None:
    """`data` as a tree entry's inline `content`: UTF-8, no NUL, at most INLINE_MAX_BYTES."""
    if len(data) > INLINE_MAX_BYTES or b"\0" in data:
        return None
    return decode_utf8(data)


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
    return clip(" ".join(text.split()), MAX_GITHUB_MESSAGE_CHARS)
