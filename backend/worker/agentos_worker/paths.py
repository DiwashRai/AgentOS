"""The path, branch and PR URL rules (SPEC §6), mirroring the server's validate.py: each
returns why a value breaks them, or None.
"""

from __future__ import annotations

import re
import unicodedata
import urllib.parse

MAX_PATH_CHARS = 255


MAX_PR_URL_CHARS = 500  # the server's ResultData.pr_url max_length


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
