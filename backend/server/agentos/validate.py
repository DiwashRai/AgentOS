"""Input validators: paths, keys, repo URLs, branches, block content, sizes (SPEC §4, §6).

Pure: no web, db or http imports (enforced by import-linter). Every `validate_*` / `check_*`
raises `ApiError(422, "validation_error", "<field>: <why>")` (size limits: 413 `too_large`) and
returns the value to store. The repo URL and branch rules are also `*_problem` functions that
return what is wrong (or None) as UI text, for pydantic validators and other checks that collect
issues. `pydantic_issues` turns pydantic's errors into `ValidationIssue`s worded as UI text;
`parsed` and `too_many` collect them for checks that report every problem at once.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence, Sized
from typing import Any, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

from agentos.errors import ApiError, field_error
from agentos.models import ValidationIssue

_M = TypeVar("_M", bound=BaseModel)

# ------------------------------------------------------------------ limits (SPEC §6)

MAX_FILE_BYTES = 1024 * 1024  # one file, UTF-8
MAX_BUNDLE_BYTES = 20 * 1024 * 1024  # all of a ticket's current files
MAX_AGENT_LINE_BYTES = 256 * 1024  # manifest limits.max_line_bytes (Claude stream-json lines)
MAX_PATH_CHARS = 255
MAX_KEY_CHARS = 40  # keeps skill.name (context-{key}) within 64 chars (see scaffold.py)
MAX_REPO_URL_CHARS = 2048
MAX_PR_URL_CHARS = 500  # CompleteRequest.pr_url max_length
MAX_SYNC_BACK_BYTES = 20 * 1024 * 1024  # the run's sync_back zip, compressed

# The workspace folder whose files the worker returns as one zip at the end of a job (repo and
# no-repo mode alike); `.agentos/` is excluded from git, so nothing in it is ever committed.
SYNC_BACK_DIR = ".agentos/sync_back"

# ------------------------------------------------------------------ block markers (SPEC §6)
# The worker mirrors these (it is stdlib-only and can't import them).

BLOCK_BEGIN = "<!-- agentos:begin {key} -->"
BLOCK_END = "<!-- agentos:end {key} -->"
# Any line that looks like a marker, for any key, with any spacing. Block content must not contain
# one, or reading the block back would stop early or match the wrong block.
MARKER_RE = re.compile(r"<!--\s*agentos:(?:begin|end)\b", re.IGNORECASE)


def block_markers(key: str) -> tuple[str, str]:
    return BLOCK_BEGIN.format(key=key), BLOCK_END.format(key=key)


def _invalid(field: str, why: str) -> ApiError:
    return ApiError(422, "validation_error", f"{field}: {why}")


def _valid_at(field: str, value: str, problem: str | None) -> str:
    """`value`, or a 422 with one issue at `field`."""
    if problem is not None:
        raise field_error([field], problem)
    return value


# ------------------------------------------------------------------ pydantic errors → issues


def _count(n: object, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _at_least_chars(ctx: dict[str, Any]) -> str:
    n = ctx["min_length"]
    return "Required." if n == 1 else f"At least {_count(n, 'character')}."


_WHOLE_NUMBER = "Must be a whole number."
_NUMBER = "Must be a number."
_OBJECT = "Must be an object."

# UI text for pydantic's error types, from the error's `ctx` (its constraint). A type missing
# here keeps pydantic's own message.
_MESSAGES: dict[str, Callable[[dict[str, Any]], str]] = {
    "missing": lambda ctx: "Required.",
    "string_too_short": _at_least_chars,
    "string_too_long": lambda ctx: f"At most {_count(ctx['max_length'], 'character')}.",
    "too_short": lambda ctx: f"At least {_count(ctx['min_length'], 'item')}.",
    "too_long": lambda ctx: f"At most {_count(ctx['max_length'], 'item')}.",
    "greater_than_equal": lambda ctx: f"At least {ctx['ge']}.",
    "less_than_equal": lambda ctx: f"At most {ctx['le']}.",
    "greater_than": lambda ctx: f"More than {ctx['gt']}.",
    "less_than": lambda ctx: f"Less than {ctx['lt']}.",
    "int_type": lambda ctx: _WHOLE_NUMBER,
    "int_parsing": lambda ctx: _WHOLE_NUMBER,
    "int_from_float": lambda ctx: _WHOLE_NUMBER,
    "float_type": lambda ctx: _NUMBER,
    "float_parsing": lambda ctx: _NUMBER,
    "string_type": lambda ctx: "Must be text.",
    "bool_type": lambda ctx: "Must be true or false.",
    "bool_parsing": lambda ctx: "Must be true or false.",
    "list_type": lambda ctx: "Must be a list.",
    "tuple_type": lambda ctx: "Must be a list.",
    "model_type": lambda ctx: _OBJECT,
    "model_attributes_type": lambda ctx: _OBJECT,
    "dict_type": lambda ctx: _OBJECT,
    "literal_error": lambda ctx: f"Must be {ctx['expected']}.",
    "extra_forbidden": lambda ctx: "Unknown field.",
}


def issue_message(error: ErrorDetails) -> str:
    """One pydantic error as UI text: a validator's ValueError gives its own text (the exception
    in the error's `ctx`, without pydantic's `Value error, ` prefix), a constraint or type error
    its `_MESSAGES` entry, anything else pydantic's message."""
    ctx: dict[str, Any] = error.get("ctx", {})
    raised = ctx.get("error")
    if error["type"] == "value_error" and isinstance(raised, ValueError):
        return str(raised)

    message = _MESSAGES.get(error["type"])
    return error["msg"] if message is None else message(ctx)


def pydantic_issues(
    errors: Iterable[ErrorDetails],
    prefix: Sequence[str | int] = (),
    *,
    strip_body: bool = False,
) -> list[ValidationIssue]:
    """One issue per pydantic error (`issue_message`), its `loc` behind `prefix` (a leading
    `body` dropped first with `strip_body`)."""
    issues: list[ValidationIssue] = []
    for error in errors:
        loc = list(error["loc"])
        if strip_body and loc[:1] == ["body"]:
            loc = loc[1:]
        issues.append(ValidationIssue(loc=[*prefix, *loc], message=issue_message(error)))
    return issues


def parsed(
    model: type[_M], data: Mapping[str, Any], prefix: Sequence[str | int] = ()
) -> tuple[_M | None, list[ValidationIssue]]:
    """`model` from `data` and no issues, or None and every problem, each at `prefix` + its
    field."""
    try:
        return model.model_validate(data), []
    except ValidationError as exc:
        return None, pydantic_issues(exc.errors(), prefix)


def too_many(
    items: Sized, limit: int, loc: Sequence[str | int], noun: str = "items"
) -> list[ValidationIssue]:
    """One issue at `loc` when `items` holds more than `limit` ("At most 100 repos.")."""
    if len(items) <= limit:
        return []
    return [ValidationIssue(loc=list(loc), message=f"At most {limit} {noun}.")]


# ------------------------------------------------------------------ file paths

_SEGMENT_RE = re.compile(r"[A-Za-z0-9._ @+-]+")
_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def validate_path(path: str, field: str = "path") -> str:
    """Return the path unchanged, or raise 422.

    Segments are ASCII-only, so no Unicode normalisation is needed. Normalising first would
    silently rename the few characters NFC maps to ASCII (U+212A KELVIN SIGN → 'K'), which the
    worker rejects; fixtures/path_cases.json keeps the two validators in step.
    """
    p = path
    if not p:
        raise _invalid(field, "must not be empty")
    if len(p) > MAX_PATH_CHARS:
        raise _invalid(field, f"longer than {MAX_PATH_CHARS} characters")
    if p.startswith("/"):
        raise _invalid(field, "must be relative")
    for seg in p.split("/"):
        if not seg:
            raise _invalid(field, "empty path segment")
        if seg == "..":
            raise _invalid(field, "'..' is not allowed")
        if not _SEGMENT_RE.fullmatch(seg):
            raise _invalid(field, f"segment {seg!r} may only contain A-Z a-z 0-9 . _ space @ + -")
        if seg.endswith((".", " ")):
            raise _invalid(field, f"segment {seg!r} must not end in '.' or a space")
        if seg.lower() == ".git":
            raise _invalid(field, "'.git' is not allowed")
        # Windows ignores spaces before the extension: "CON .txt" is CON.
        if seg.split(".", 1)[0].rstrip(" ").upper() in _RESERVED:
            raise _invalid(field, f"segment {seg!r} is a reserved Windows name")
    return p


def path_key(path: str) -> str:
    """Identity of a path for case-insensitive uniqueness (paths are ASCII after validation)."""
    return path.lower()


def check_unique_paths(paths: Iterable[str], field: str = "path") -> None:
    seen: dict[str, str] = {}
    for p in paths:
        k = path_key(p)
        if k in seen:
            raise _invalid(field, f"{p!r} collides with {seen[k]!r} (paths are case-insensitive)")
        seen[k] = p


# ------------------------------------------------------------------ keys, repos, branches

_KEY_RE = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+")


def validate_key(key: str) -> str:
    if len(key) > MAX_KEY_CHARS or not _KEY_RE.fullmatch(key):
        raise _invalid("key", f"must match [A-Z][A-Z0-9]*-<n> (at most {MAX_KEY_CHARS} chars)")
    return key


_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*")
_SCP_RE = re.compile(r"git@([^:/@]+):(.+)")
_PORT_RE = re.compile(r"[0-9]{1,5}")


def _host_problem(host: str) -> str | None:
    if _HOST_RE.fullmatch(host):
        return None
    return f"{host} isn't a valid host." if host else "Missing the host."


def repo_url_problem(url: str) -> str | None:
    """What is wrong with a repo URL, or None. Valid: `https://host/path`,
    `ssh://[git@]host[:port]/path` or `git@host:path`.

    No userinfo other than `git@` (so no embedded credentials), no leading `-` (option injection
    into git), no whitespace or control characters, no query or fragment.
    """
    if not url:
        return "Required."
    if len(url) > MAX_REPO_URL_CHARS:
        return f"At most {MAX_REPO_URL_CHARS} characters."
    if url.startswith("-"):
        return "Must not start with -."
    if any(c.isspace() or not c.isprintable() for c in url):
        return "No spaces or control characters."

    scp = _SCP_RE.fullmatch(url)
    if scp and "://" not in url:
        host, path = scp.groups()
        if problem := _host_problem(host):
            return problem
        return "The path must not start with -." if path.startswith("-") else None

    parts = urlsplit(url)
    if parts.scheme not in ("https", "ssh"):
        return "Use https://host/owner/repo, ssh://git@host/owner/repo or git@host:owner/repo."
    if parts.query or parts.fragment or "?" in url or "#" in url:
        return "No query (?) or fragment (#)."
    netloc = parts.netloc
    if "@" in netloc:
        user, _, netloc = netloc.rpartition("@")
        if parts.scheme != "ssh" or user != "git":
            return "No credentials in the URL (only git@ on ssh://)."
    host, sep, port = netloc.partition(":")
    if problem := _host_problem(host):
        return problem
    if sep and not _PORT_RE.fullmatch(port):
        return "Invalid port."
    if parts.path.strip("/") == "":
        return "Include the repository path."
    return None


def validate_repo_url(url: str) -> str:
    """`url`, or a 422 with one issue at `repo_url` (`repo_url_problem`)."""
    return _valid_at("repo_url", url, repo_url_problem(url))


def validate_pr_url(url: str) -> str:
    """A pull request link the UI renders: `https://host[:port]/...`, at most MAX_PR_URL_CHARS.

    No userinfo (credentials), no whitespace or control characters.
    """
    if len(url) > MAX_PR_URL_CHARS:
        raise _invalid("pr_url", f"longer than {MAX_PR_URL_CHARS} characters")
    if any(c.isspace() or not c.isprintable() for c in url):
        raise _invalid("pr_url", "must not contain whitespace or control characters")
    if not url.startswith("https://"):
        raise _invalid("pr_url", "must be an https:// URL")
    try:
        parts = urlsplit(url)
    except ValueError as exc:  # e.g. an unbalanced IPv6 bracket
        raise _invalid("pr_url", f"not a URL: {exc}") from exc
    if "@" in parts.netloc:
        raise _invalid("pr_url", "must not contain credentials")
    host, sep, port = parts.netloc.partition(":")
    if not _HOST_RE.fullmatch(host):
        raise _invalid("pr_url", f"invalid host {host!r}")
    if sep and not _PORT_RE.fullmatch(port):
        raise _invalid("pr_url", f"invalid port {port!r}")
    return url


_BRANCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")


def base_branch_problem(branch: str) -> str | None:
    """What is wrong with a branch name, or None. SPEC §6, plus git's rule that no
    `/`-component starts with `.` or ends in `.lock`."""
    if not branch:
        return "Required."
    if not _BRANCH_RE.fullmatch(branch):
        return (
            "Letters, digits, . _ / or -, starting with a letter or digit, at most 200 characters."
        )
    if ".." in branch or "//" in branch:
        return "Must not contain .. or //."
    if branch.endswith(("/", ".", ".lock")):
        return "Must not end in /, . or .lock."
    for comp in branch.split("/"):
        if comp.startswith(".") or comp.endswith(".lock"):
            return "No part may start with . or end in .lock."
    return None


def validate_base_branch(branch: str) -> str:
    """`branch`, or a 422 with one issue at `base_branch` (`base_branch_problem`)."""
    return _valid_at("base_branch", branch, base_branch_problem(branch))


# ------------------------------------------------------------------ content


def validate_block_content(content: str, field: str = "content") -> None:
    """Block-mode content must not contain a line that looks like a block marker (any key)."""
    for line in content.splitlines():
        if MARKER_RE.search(line):
            raise _invalid(field, "block content must not contain an agentos:begin/end marker")


def utf8_size(content: str, field: str = "content") -> int:
    try:
        return len(content.encode("utf-8"))
    except UnicodeEncodeError as e:  # lone surrogates from JSON "\ud800"
        raise _invalid(field, "not valid UTF-8") from e


def check_file_size(content: str, field: str = "content") -> int:
    """Raise 413 if the file is over MAX_FILE_BYTES; return its UTF-8 size."""
    size = utf8_size(content, field)
    if size > MAX_FILE_BYTES:
        raise ApiError(413, "too_large", f"{field}: {size} bytes exceeds {MAX_FILE_BYTES}")
    return size


def check_bundle_size(contents: Iterable[str]) -> int:
    """Raise 413 if all files together are over MAX_BUNDLE_BYTES; return the total."""
    total = sum(utf8_size(c) for c in contents)
    if total > MAX_BUNDLE_BYTES:
        raise ApiError(413, "too_large", f"bundle: {total} bytes exceeds {MAX_BUNDLE_BYTES}")
    return total
